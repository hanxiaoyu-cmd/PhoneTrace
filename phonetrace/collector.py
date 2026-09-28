"""Best-effort Android telemetry, without Root, agents or device state changes.

Sources and semantics:
* AOSP FrameTracker::dumpStats: desired, actual-present, ready timestamps (ns).
  https://android.googlesource.com/platform/frameworks/native/+/master/services/surfaceflinger/FrameTracker.cpp
* Linux power_supply: current_now in microamps; voltage_now in microvolts.
  https://docs.kernel.org/power/power_supply_class.html
* BatteryService's read-only `get -f current_now` refreshes HealthInfo and emits
  batteryCurrentMicroamps (positive charging, negative discharging).
  https://android.googlesource.com/platform/frameworks/base/+/master/services/core/java/com/android/server/BatteryService.java

Frame intervals describe observed presents of the selected game's layer. They do
not measure input latency, GPU render time, or reconstruct missing frame history.
"""
from __future__ import annotations

from datetime import datetime
import math
import re
import shlex
import time

from .adb import AdbClient, AdbError, layer_matches_package, validate_package
from .models import Sample, SessionConfig


_MAX_TIMESTAMP = 9223372036854775807
_MARKER = "__PHONETRACE_"


def parse_frame_timestamps(output: str) -> list[int]:
    """Ignore refresh-period header, pending fences, zeros and duplicate presents."""
    timestamps = set()
    for line in output.splitlines():
        parts = line.split()
        if len(parts) == 3 and all(p.lstrip("-").isdigit() for p in parts):
            actual = int(parts[1])
            if 0 < actual < _MAX_TIMESTAMP:
                timestamps.add(actual)
    return sorted(timestamps)


def _split_sections(output: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current = ""
    for line in output.splitlines():
        match = re.fullmatch(r"__PHONETRACE_([A-Z_]+)__", line.strip())
        if match:
            current = match.group(1)
            sections[current] = []
        elif current:
            sections[current].append(line)
    return {key: "\n".join(lines).strip() for key, lines in sections.items()}


def _number(text: str) -> float | None:
    text = text.strip()
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return None
    value = float(text)
    return value if math.isfinite(value) else None


def _current_microamps(text: str) -> int | None:
    """Both supported sources specify integer microamps, never guessed units."""
    text = text.strip()
    if not re.fullmatch(r"-?[0-9]{1,12}", text):
        return None
    value = int(text)
    # Reject sentinel values and implausible readings while retaining a real
    # zero in the raw-current field. Zero alone cannot validate load power.
    return value if abs(value) <= 100_000_000 else None


class Collector:
    def __init__(self, client: AdbClient, config: SessionConfig):
        self.client, self.config = client, config
        self.package = validate_package(config.package)
        if config.layer and ("\n" in config.layer or "\r" in config.layer or "\0" in config.layer
                             or not layer_matches_package(config.layer, self.package)):
            raise AdbError("请选择属于目标游戏包名的图层。")
        self._layer = config.layer
        self._started = time.monotonic()
        self._last_frame_ns: int | None = None
        self._last_cpu: tuple[int, int] | None = None
        self._stale_polls = 0
        self._rebaseline_after_stale = False
        self._last_scan = -float("inf")
        self._last_memory_poll = -float("inf")
        self._closed = False

    def close(self):
        # Collection is passive: no on-device collector or temporary files exist.
        self._closed = True

    def _choose_layer(self, now: float):
        if self.config.layer:
            return
        if self._layer and self._stale_polls < 3:
            return
        if now - self._last_scan < 3:
            return
        self._last_scan = now
        candidates = self.client.layers(self.config.serial, self.package)
        # SurfaceView/BLAST generally hosts native games. Probe a bounded number
        # and choose the most recently presented game layer, never the display.
        def score(layer: str) -> tuple[int, int]:
            return (int("SurfaceView" in layer), int("BLAST" in layer))
        candidates.sort(key=score, reverse=True)
        best = ""
        best_rank = ((-1, -1), 0)
        for candidate in candidates[:6]:
            frames = parse_frame_timestamps(self.client.shell(
                self.config.serial, "dumpsys SurfaceFlinger --latency " + shlex.quote(candidate)))
            if frames and (score(candidate), frames[-1]) > best_rank:
                best, best_rank = candidate, (score(candidate), frames[-1])
        selected = best or (candidates[0] if candidates else "")
        if selected != self._layer:
            self._layer = selected
            self._last_frame_ns = None
            self._stale_polls = 0
            self._rebaseline_after_stale = False

    def _command(self, include_memory: bool = True) -> str:
        reads = {
            "FRAMES": ("dumpsys SurfaceFlinger --latency " + shlex.quote(self._layer)) if self._layer else ":",
            # Fall back only when the sysfs read fails or is empty; never replace
            # a valid zero or reinterpret current units from its magnitude.
            # Refresh precedes the battery dump so status/voltage use the updated
            # HealthInfo too. `get -f` does not freeze/override battery state.
            "CURRENT": (
                "if _pt_current=$(cat /sys/class/power_supply/battery/current_now 2>/dev/null) "
                '&& [ -n "$_pt_current" ]; then printf \'%s\\n\' "$_pt_current"; '
                "else printf '\\n__PHONETRACE_SERVICE_CURRENT__\\n'; "
                "cmd battery get -f current_now 2>/dev/null; fi"
            ),
            "VOLTAGE": "cat /sys/class/power_supply/battery/voltage_now 2>/dev/null",
            "SYS_STATUS": "cat /sys/class/power_supply/battery/status 2>/dev/null",
            "BATTERY": "dumpsys battery",
            "CPU": "cat /proc/stat 2>/dev/null",
        }
        if include_memory:
            # --local avoids an IPC dump into the game; --package covers its
            # processes; -s limits output to the PSS summary (AOSP AMS options).
            reads["MEMORY"] = "dumpsys meminfo --local --package -s " + shlex.quote(self.package)
        # These markers are an internal protocol; all interpolated values are
        # quoted for Android's POSIX shell. No command modifies device state.
        return "; ".join("printf '\\n" + _MARKER + key + "__\\n'; " + command
                         for key, command in reads.items()) + "; true"

    def sample(self) -> Sample:
        if self._closed:
            raise AdbError("采集已停止。")
        self._choose_layer(time.monotonic())
        poll_started = time.monotonic()
        include_memory = poll_started - self._last_memory_poll >= 5.0
        sections = _split_sections(self.client.shell(self.config.serial, self._command(include_memory), timeout=10))
        sample = Sample(timestamp=datetime.now().astimezone().isoformat(timespec="milliseconds"),
                        elapsed_s=max(0, time.monotonic() - self._started), layer=self._layer)
        self._frames(sections.get("FRAMES", ""), sample)
        self._battery(sections, sample)
        self._cpu(sections.get("CPU", ""), sample)
        if include_memory:
            self._last_memory_poll = poll_started
            sample.app_mem_mb = self._memory(sections.get("MEMORY", ""))
            if sample.app_mem_mb is None:
                sample.notes.append("应用 PSS 内存不可读取或应用尚未运行")
        else:
            sample.notes.append("PSS 内存每 5 秒采集一次，本次留空以降低采集开销")
        return sample

    def _frames(self, output: str, sample: Sample):
        frames = parse_frame_timestamps(output)
        if not self._layer or not frames:
            self._stale_polls += 1
            self._last_frame_ns = None
            sample.notes.append("未获得游戏图层帧时间；请打开游戏、检查图层或系统支持情况")
            return
        if self._last_frame_ns is None or frames[-1] < self._last_frame_ns:
            self._last_frame_ns = frames[-1]
            self._rebaseline_after_stale = False
            sample.notes.append("帧率正在建立基线，本次历史帧不计入测试")
            return
        new_frames = [t for t in frames if t > self._last_frame_ns]
        if not new_frames:
            self._stale_polls += 1
            if self._stale_polls >= 3:
                self._rebaseline_after_stale = True
            sample.notes.append("游戏图层没有新帧；暂停、卡住或图层变化时不推算 FPS")
            return
        self._stale_polls = 0
        if self._rebaseline_after_stale:
            self._last_frame_ns = frames[-1]
            self._rebaseline_after_stale = False
            sample.notes.append("图层长时间无新帧后恢复，重新建立基线；中断期间不推算帧间隔")
            return
        previous = self._last_frame_ns
        if previous < frames[0]:
            # SurfaceFlinger's bounded history rolled over. Do not invent an
            # interval joining the previous poll to an unknown number of frames.
            previous = new_frames.pop(0)
            sample.notes.append("帧历史缓冲区已覆盖部分数据；仅统计仍可观察到的帧间隔")
        intervals = []
        for stamp in new_frames:
            intervals.append((stamp - previous) / 1_000_000.0)
            previous = stamp
        self._last_frame_ns = frames[-1]
        if not intervals:
            sample.notes.append("新帧数量不足，等待下一次采样")
            return
        sample.frame_intervals_ms = intervals
        sample.frame_time_ms = sum(intervals) / len(intervals)
        sample.fps = 1000.0 / sample.frame_time_ms
        sample.frame_p95_ms = sorted(intervals)[max(0, math.ceil(len(intervals) * .95) - 1)]
        sample.slow_frames = sum(interval > 50 for interval in intervals)
        sample.fps_source = "SurfaceFlinger.actualPresentTime"

    @staticmethod
    def _battery(sections: dict[str, str], sample: Sample):
        data = {}
        for line in sections.get("BATTERY", "").splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                data[key.strip().lower()] = value.strip()
        powered = {key: value.lower() for key, value in data.items() if key.endswith("powered")}
        if any(value == "true" for value in powered.values()):
            sample.plugged = True
        elif (all(powered.get(key) == "false" for key in ("ac powered", "usb powered", "wireless powered"))
              and all(value == "false" for value in powered.values())):
            sample.plugged = False
        level, scale = _number(data.get("level", "")), _number(data.get("scale", ""))
        if level is not None and scale is not None and scale > 0 and 0 <= level <= scale:
            sample.battery_pct = level / scale * 100
        temp = _number(data.get("temperature", ""))
        if temp is not None and -500 <= temp <= 1500:
            sample.battery_temp_c = temp / 10.0
        voltage_uv = _number(sections.get("VOLTAGE", ""))
        if voltage_uv is not None and 1_000_000 <= voltage_uv <= 20_000_000:
            sample.voltage_v = voltage_uv / 1_000_000.0
            sample.voltage_source = "sysfs.battery.voltage_now_uV"
        else:
            # dumpsys BatteryService reports mV; this is an explicit source unit,
            # not a magnitude-based guess for the sysfs field.
            voltage_mv = _number(data.get("voltage", ""))
            if voltage_mv is not None and 1000 <= voltage_mv <= 20_000:
                sample.voltage_v = voltage_mv / 1000.0
                sample.voltage_source = "BatteryService.voltage_mV"
        current_ua = _current_microamps(sections.get("CURRENT", ""))
        if current_ua is not None:
            sample.current_ma = current_ua / 1000.0  # Retain the driver's raw sign.
            sample.current_source = "sysfs.battery.current_now_uA"
        else:
            current_ua = _current_microamps(sections.get("SERVICE_CURRENT", ""))
            if current_ua is not None:
                sample.current_ma = current_ua / 1000.0
                sample.current_source = "BatteryService.batteryCurrentMicroamps"
        sys_status = sections.get("SYS_STATUS", "").strip().lower()
        discharging = data.get("status") == "3" and data.get("present", "true").lower() != "false"
        simulated = "updates stopped" in sections.get("BATTERY", "").lower()
        contradiction = sys_status in {"charging", "full", "not charging"}
        service_sign_conflict = (sample.current_source == "BatteryService.batteryCurrentMicroamps"
                                 and sample.current_ma is not None and sample.current_ma > 0)
        if (sample.plugged is False and discharging and not simulated and not contradiction
                and not service_sign_conflict
                and sample.current_ma is not None and sample.voltage_v is not None
                and sample.current_ma != 0):
            sample.power_w = abs(sample.current_ma) * sample.voltage_v / 1000.0
            sample.power_source = f"{sample.current_source} × {sample.voltage_source} (discharging)"
        elif sample.plugged:
            sample.notes.append("手机接通电源；电池电流不能代表整机游戏功耗，功耗留空")
        elif simulated:
            sample.notes.append("系统电池信息处于模拟状态，无法验证放电，功耗留空")
        elif sample.plugged is not False or not discharging or contradiction:
            sample.notes.append("无法确认手机处于未接电的放电状态，功耗留空")
        elif service_sign_conflict:
            sample.notes.append("系统电流为正值（充电）但电池状态为放电，读数不一致，功耗留空")
        else:
            sample.notes.append("电池电流 / 电压不可读取或读数无效，功耗留空")

    def _cpu(self, output: str, sample: Sample):
        match = re.search(r"^cpu\s+([0-9 ]+)\s*$", output, re.MULTILINE)
        if not match:
            self._last_cpu = None
            sample.notes.append("系统不允许读取整机 CPU 计数")
            return
        ticks = [int(part) for part in match.group(1).split()]
        if len(ticks) < 4:
            self._last_cpu = None
            return
        # guest/guest_nice (columns 9+) are already included in user/nice.
        total, idle = sum(ticks[:8]), ticks[3] + (ticks[4] if len(ticks) > 4 else 0)
        if self._last_cpu is not None:
            delta_total, delta_idle = total - self._last_cpu[0], idle - self._last_cpu[1]
            if delta_total > 0 and 0 <= delta_idle <= delta_total:
                sample.cpu_pct = 100.0 * (delta_total - delta_idle) / delta_total
        self._last_cpu = (total, idle)

    @staticmethod
    def _memory(output: str) -> float | None:
        # Android emits PSS in KiB despite the historical "kB" spelling.
        totals = re.findall(r"TOTAL PSS:\s*([0-9,]+)", output)
        if totals:
            total = sum(int(value.replace(",", "")) for value in totals)
            return total / 1024.0 if total > 0 else None
        totals = re.findall(r"^\s*TOTAL\s+([0-9,]+)\s", output, re.MULTILINE)
        if totals:
            total = sum(int(value.replace(",", "")) for value in totals)
            return total / 1024.0 if total > 0 else None
        # Never substitute RSS for PSS on newer Android releases.
        return None
