"""Durable, local session storage and dependency-free HTML reports.

JSONL is the recovery source of truth. CSV files intentionally contain empty cells
for unavailable measurements; no missing measurement is converted into zero.
"""
from __future__ import annotations

import csv
import html
import json
import math
import os
import re
import time
import uuid
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import Sample, SessionConfig


CSV_FIELDS = [field.name for field in fields(Sample) if field.name != "frame_intervals_ms"]
FRAME_FIELDS = ["sample_timestamp", "sample_elapsed_s", "interval_index", "frame_interval_ms"]
_NUMERIC_FIELDS = {
    "fps", "frame_time_ms", "frame_p95_ms", "slow_frames", "power_w",
    "current_ma", "voltage_v", "battery_pct", "battery_temp_c", "cpu_pct", "app_mem_mb",
}
_LIMITATIONS = (
    "帧率由所选应用图层中实际观测到的帧间隔计算；采样缓冲区、权限和系统适配可能导致漏帧，"
    "覆盖率不是完整性保证。慢帧指观测间隔 > 50 ms，不等同于厂商卡顿指标。"
    "功耗为整机电池放电估算（含屏幕和网络），不是芯片功耗；仅对有效相邻采样积分，"
    "缺失值与长时间中断不计入平均功耗。"
)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _valid_values(samples: list[Sample], key: str) -> list[float]:
    return [value for sample in samples if (value := _number(getattr(sample, key))) is not None]


def _average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _max_gap(metadata: dict) -> float:
    config = metadata.get("config", {})
    interval = _number(config.get("interval_s", metadata.get("interval_s", 0.5)))
    return max(2.0, 3.0 * (interval or 0.5))


def build_summary(samples: list[Sample], metadata: dict | None = None) -> dict:
    """Summarize observed intervals and duration-weighted valid discharge power."""
    metadata = metadata or {}
    config = metadata.get("config", {})
    duration = max([0.0] + [value for sample in samples
                           if (value := _number(sample.elapsed_s)) is not None])
    duration = max(duration, _number(metadata.get("record_duration_s")) or 0.0)
    intervals = [value for sample in samples for interval in sample.frame_intervals_ms
                 if (value := _number(interval)) is not None and value > 0]
    frame_duration = sum(intervals) / 1000.0
    power_duration = 0.0
    energy_j = 0.0
    max_gap = _max_gap(metadata)
    for left, right in zip(samples, samples[1:]):
        left_time, right_time = _number(left.elapsed_s), _number(right.elapsed_s)
        left_power, right_power = _number(left.power_w), _number(right.power_w)
        if None in (left_time, right_time, left_power, right_power):
            continue
        dt = right_time - left_time
        if not 0 < dt <= max_gap or min(left_power, right_power) < 0:
            continue
        # A plugged sample is not a valid whole-device discharge measurement.
        if left.plugged is True or right.plugged is True:
            continue
        power_duration += dt
        energy_j += (left_power + right_power) * 0.5 * dt
    sorted_intervals = sorted(intervals)
    temperatures = _valid_values(samples, "battery_temp_c")
    powers = [sample.power_w for sample in samples
              if _number(sample.power_w) is not None and sample.power_w >= 0
              and sample.plugged is not True]
    return {
        "id": metadata.get("id", ""),
        "title": metadata.get("title", config.get("title", "游戏测试")),
        "started_at": metadata.get("started_at", samples[0].timestamp if samples else ""),
        "ended_at": metadata.get("ended_at", ""),
        "duration_s": duration,
        "sample_count": len(samples),
        "interval_s": config.get("interval_s", metadata.get("interval_s", 0.5)),
        "avg_fps": len(intervals) / frame_duration if frame_duration else None,
        "avg_power_w": energy_j / power_duration if power_duration else None,
        "path": metadata.get("path", ""),
        "demo": bool(metadata.get("demo", config.get("demo", False))),
        "status": metadata.get("status", "interrupted"),
        "error": metadata.get("error", ""),
        "package": config.get("package", metadata.get("package", "")),
        "power_valid_duration_s": power_duration,
        "power_coverage_pct": min(100.0, power_duration / duration * 100) if duration else 0.0,
        "energy_wh": energy_j / 3600 if power_duration else None,
        "peak_power_w": max(powers) if powers else None,
        "frame_interval_count": len(intervals),
        "frame_observed_duration_s": frame_duration,
        "frame_coverage_pct": min(100.0, frame_duration / duration * 100) if duration else 0.0,
        "avg_frame_time_ms": _average(intervals),
        "p95_frame_time_ms": sorted_intervals[math.ceil(0.95 * len(intervals)) - 1] if intervals else None,
        "slow_frames": sum(value > 50 for value in intervals) if intervals else None,
        "avg_cpu_pct": _average(_valid_values(samples, "cpu_pct")),
        "avg_temp_c": _average(temperatures),
        "max_temp_c": max(temperatures) if temperatures else None,
        "limitations": _LIMITATIONS,
    }


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _flush(handle) -> None:
    handle.flush()
    os.fsync(handle.fileno())


def _csv_cell(value: Any) -> Any:
    # Device-provided layer names and notes are text, never spreadsheet formulas.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


class SessionRecorder:
    """Write each sample immediately; interrupted recordings remain recoverable."""

    def __init__(self, root: Path, config: SessionConfig, device_info: dict):
        root = Path(root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        started = datetime.now().astimezone()
        title_part = re.sub(r"[^\w\-]+", "_", config.title, flags=re.UNICODE).strip("_")[:40] or "session"
        while True:
            self.id = f"{started:%Y%m%d_%H%M%S}_{title_part}_{uuid.uuid4().hex[:10]}"
            self.path = root / self.id
            try:
                self.path.mkdir()
                break
            except FileExistsError:
                continue
        self._metadata = {
            "format_version": 1, "id": self.id, "title": config.title,
            "started_at": started.isoformat(timespec="seconds"), "ended_at": "",
            "path": str(self.path), "demo": config.demo, "status": "recording", "error": "",
            "config": asdict(config), "device_info": device_info,
            "limitations": _LIMITATIONS,
            "csv_units": {"fps": "frames/s", "frame_time_ms": "ms", "frame_p95_ms": "ms",
                          "power_w": "whole-device battery discharge W", "current_ma": "signed mA",
                          "voltage_v": "V", "battery_pct": "%", "battery_temp_c": "°C",
                          "cpu_pct": "%", "app_mem_mb": "MiB", "elapsed_s": "s"},
            "electrical_sources": {
                "current_source": "sysfs.battery.current_now_uA or BatteryService.batteryCurrentMicroamps; raw uA converted to signed mA",
                "voltage_source": "sysfs.battery.voltage_now_uV or BatteryService.voltage_mV; converted to V",
                "current_sign": "BatteryService: positive charging, negative discharging; sysfs: driver raw sign",
            },
        }
        self._samples: list[Sample] = []
        self._started_monotonic = time.monotonic()
        self._finished: dict | None = None
        self._handles = []
        _atomic_json(self.path / "metadata.json", self._metadata)
        try:
            self._jsonl = (self.path / "samples.jsonl").open("a", encoding="utf-8", newline="\n")
            self._handles.append(self._jsonl)
            self._csv = (self.path / "samples.csv").open("w", encoding="utf-8-sig", newline="")
            self._handles.append(self._csv)
            self._frame_csv = (self.path / "frame_intervals.csv").open("w", encoding="utf-8-sig", newline="")
            self._handles.append(self._frame_csv)
            self._writer = csv.DictWriter(self._csv, CSV_FIELDS)
            self._frame_writer = csv.DictWriter(self._frame_csv, FRAME_FIELDS)
            self._writer.writeheader()
            self._frame_writer.writeheader()
            for handle in self._handles:
                _flush(handle)
        except Exception:
            self._close_handles()
            raise

    def append(self, sample: Sample) -> None:
        if self._finished is not None or self._jsonl.closed:
            raise RuntimeError("测试记录已关闭，不能继续写入")
        # Serialize before any write; JSON is durable before secondary CSVs.
        data = sample.to_dict()
        line = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        self._jsonl.write(line + "\n")
        _flush(self._jsonl)
        self._samples.append(Sample(**json.loads(line)))
        row = {key: data[key] for key in CSV_FIELDS}
        row["notes"] = " | ".join(data["notes"])
        self._writer.writerow({key: _csv_cell(value) for key, value in row.items()})
        for index, interval in enumerate(sample.frame_intervals_ms):
            self._frame_writer.writerow({"sample_timestamp": sample.timestamp,
                                        "sample_elapsed_s": sample.elapsed_s,
                                        "interval_index": index,
                                        "frame_interval_ms": interval})
        _flush(self._csv)
        _flush(self._frame_csv)

    def _close_handles(self) -> None:
        for handle in self._handles:
            try:
                handle.close()
            except OSError:
                pass

    def finish(self, status: str = "completed", error: str = "") -> dict:
        if self._finished is not None:
            return dict(self._finished)
        self._close_handles()
        self._metadata.update(status=status, error=error,
                              record_duration_s=max(0.0, time.monotonic() - self._started_monotonic),
                              ended_at=datetime.now().astimezone().isoformat(timespec="seconds"))
        summary = build_summary(self._samples, self._metadata)
        # JSONL remains intact if the disk cannot accommodate final artifacts.
        _atomic_json(self.path / "metadata.json", self._metadata)
        _atomic_json(self.path / "summary.json", summary)
        report = _render_report(summary, self._samples, self._metadata)
        (self.path / "report.html").write_text(report, encoding="utf-8")
        self._finished = summary
        return dict(summary)


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _sample_from_record(record: Any) -> Sample | None:
    if not isinstance(record, dict) or not isinstance(record.get("timestamp"), str):
        return None
    elapsed = _number(record.get("elapsed_s"))
    if elapsed is None or elapsed < 0:
        return None
    accepted = {field.name for field in fields(Sample)}
    data = {key: value for key, value in record.items() if key in accepted}
    data["elapsed_s"] = elapsed
    for key in _NUMERIC_FIELDS:
        data[key] = _number(data.get(key))
    for key in ("layer", "fps_source", "power_source", "current_source", "voltage_source"):
        if key in data and not isinstance(data[key], str):
            data[key] = ""
    data["plugged"] = data.get("plugged") if isinstance(data.get("plugged"), bool) else None
    data["notes"] = [str(note) for note in data.get("notes", [])] if isinstance(data.get("notes", []), list) else []
    intervals = data.get("frame_intervals_ms", [])
    data["frame_intervals_ms"] = [value for item in intervals
                                 if (value := _number(item)) is not None and value > 0] if isinstance(intervals, list) else []
    return Sample(**data)


def load_session(path: Path) -> tuple[dict, list[Sample]]:
    """Recover valid JSONL rows, tolerating a truncated or corrupted trailing row."""
    path = Path(path).resolve()
    metadata = _read_json(path / "metadata.json")
    metadata.setdefault("id", path.name)
    metadata["path"] = str(path)
    if metadata.get("status") in (None, "recording"):
        metadata["status"] = "interrupted"
    samples: list[Sample] = []
    skipped = 0
    try:
        with (path / "samples.jsonl").open("r", encoding="utf-8-sig", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    sample = _sample_from_record(json.loads(line))
                except (ValueError, TypeError):
                    sample = None
                if sample is None:
                    skipped += 1
                else:
                    samples.append(sample)
    except OSError as exc:
        metadata["error"] = metadata.get("error") or f"无法读取采样记录：{exc}"
    summary = build_summary(samples, metadata)
    summary["recovery_skipped_rows"] = skipped
    if skipped:
        summary["recovery_note"] = f"恢复了 {len(samples)} 条有效记录，跳过 {skipped} 条不完整或损坏记录。"
    return summary, samples


def list_sessions(root: Path) -> list[dict]:
    root = Path(root)
    if not root.exists():
        return []
    sessions = []
    for path in root.iterdir():
        if not path.is_dir() or not ((path / "metadata.json").exists() or (path / "samples.jsonl").exists()):
            continue
        metadata = _read_json(path / "metadata.json")
        summary = _read_json(path / "summary.json")
        if not summary or metadata.get("status") in (None, "recording"):
            summary, _ = load_session(path)
        else:
            summary["path"] = str(path.resolve())
        sessions.append(summary)
    sessions.sort(key=lambda item: str(item.get("started_at", "")), reverse=True)
    return sessions


def _display(value: Any, digits: int = 2) -> str:
    number = _number(value)
    return f"{number:.{digits}f}" if number is not None else "—"


def _chart(samples: list[Sample], key: str, title: str, unit: str, metadata: dict) -> str:
    width, height, left, top, right, bottom = 900, 230, 56, 24, 16, 34
    duration = max([1.0] + [sample.elapsed_s for sample in samples])
    values = _valid_values(samples, key)
    if not values:
        return f'<section class="chart"><h2>{html.escape(title)}</h2><div class="empty">此指标没有有效数据</div></section>'
    low = min(values) - 1 if key == "battery_temp_c" else 0.0
    high = max(max(values) * 1.05, low + 1)
    graph_w, graph_h = width - left - right, height - top - bottom
    pieces = []
    for fraction in (0, 0.5, 1):
        y = top + graph_h * (1 - fraction)
        value = low + (high - low) * fraction
        pieces.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}" stroke="#26384b"/>')
        pieces.append(f'<text x="{left-8}" y="{y+4:.1f}" text-anchor="end">{value:.1f}</text>')
    for fraction in (0, 0.25, 0.5, 0.75, 1):
        x = left + graph_w * fraction
        pieces.append(f'<text x="{x:.1f}" y="{height-10}" text-anchor="middle">{duration*fraction:.1f}s</text>')
    groups: list[list[tuple[float, float]]] = []
    group: list[tuple[float, float]] = []
    previous_time = None
    for sample in samples:
        value = _number(getattr(sample, key))
        elapsed = _number(sample.elapsed_s)
        broken = value is None or elapsed is None
        if previous_time is not None and elapsed is not None:
            broken = broken or not 0 < elapsed - previous_time <= _max_gap(metadata)
        if broken and group:
            groups.append(group)
            group = []
        if value is not None and elapsed is not None:
            group.append((left + elapsed / duration * graph_w,
                          top + (1 - (value-low)/(high-low)) * graph_h))
        previous_time = elapsed
    if group:
        groups.append(group)
    for group in groups:
        if len(group) == 1:
            x, y = group[0]
            pieces.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="2.5" fill="#4bdfc9"/>')
        else:
            points = " ".join(f"{x:.2f},{y:.2f}" for x, y in group)
            pieces.append(f'<polyline points="{points}" fill="none" stroke="#4bdfc9" stroke-width="2"/>')
    label = html.escape(f"{title} ({unit})")
    return f'<section class="chart"><h2>{label}</h2><svg viewBox="0 0 {width} {height}" role="img" aria-label="{label}">{"".join(pieces)}</svg></section>'


def _render_report(summary: dict, samples: list[Sample], metadata: dict) -> str:
    escaped = lambda value: html.escape(str(value), quote=True)
    demo = '<div class="demo">演示数据 · 由软件模拟生成 · 不代表真实手机性能</div>' if summary["demo"] else ""
    cards = [
        ("观测平均帧率", _display(summary["avg_fps"]), "FPS"),
        ("整机平均放电功耗", _display(summary["avg_power_w"]), "W"),
        ("P95 观测帧间隔", _display(summary["p95_frame_time_ms"]), "ms"),
        ("最高电池温度", _display(summary["max_temp_c"], 1), "°C"),
    ]
    card_html = "".join(f'<div class="card"><span>{label}</span><strong>{value}<small>{unit}</small></strong></div>'
                        for label, value, unit in cards)
    status_names = {"completed": "已完成", "stopped": "已停止", "interrupted": "中断后恢复", "error": "异常停止"}
    details = [
        ("应用包名", summary["package"] or "—"),
        ("开始时间", summary["started_at"]),
        ("结束状态", status_names.get(summary["status"], summary["status"])),
        ("实际经过时间", f'{_display(summary["duration_s"], 1)} s'),
        ("采样记录", str(summary["sample_count"])),
        ("有效功耗时长 / 覆盖率", f'{_display(summary["power_valid_duration_s"], 1)} s / {_display(summary["power_coverage_pct"], 1)}%'),
        ("观测帧间隔数量", str(summary["frame_interval_count"])),
        ("帧间隔累计时长 / 观测覆盖率", f'{_display(summary["frame_observed_duration_s"], 1)} s / {_display(summary["frame_coverage_pct"], 1)}%'),
        ("观测慢帧 (>50 ms)", str(summary["slow_frames"]) if summary["slow_frames"] is not None else "—"),
        ("有效时段放电能量", f'{_display(summary["energy_wh"], 4)} Wh'),
    ]
    detail_html = "".join(f"<dt>{escaped(label)}</dt><dd>{escaped(value)}</dd>" for label, value in details)
    error = f'<p class="warning">{escaped(summary["error"])}</p>' if summary.get("error") else ""
    charts = "".join(_chart(samples, key, title, unit, metadata) for key, title, unit in [
        ("fps", "帧率", "FPS"), ("power_w", "整机电池放电功耗", "W"),
        ("battery_temp_c", "电池温度", "°C"), ("cpu_pct", "整机 CPU 使用率", "%")])
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:">
<title>{escaped(summary["title"])} · PhoneTrace 测试报告</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#0b1420;color:#e2ebf5;font-family:Segoe UI,Microsoft YaHei,sans-serif;line-height:1.6}}
main{{max-width:1060px;margin:40px auto;padding:0 24px}}header p,.muted{{color:#97adc4}}h1{{font-size:30px;margin:12px 0}}h2{{font-size:16px;margin:0 0 10px}}
.brand{{color:#4bdfc9;font-weight:700;letter-spacing:3px}}.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:24px 0}}
.card,.chart,.details{{background:#111f30;border:1px solid #26384b;border-radius:12px;padding:20px}}.card span{{font-size:13px;color:#a5b8ce}}
strong{{display:block;font-size:32px;margin-top:6px}}small{{font-size:14px;color:#9bb2c9;margin-left:8px}}.chart{{margin:16px 0}}svg{{width:100%;display:block}}svg text{{fill:#9bb2c9;font-size:12px}}
.empty{{color:#8095ad;height:110px;display:flex;align-items:center;justify-content:center}}dl{{display:grid;grid-template-columns:250px 1fr;gap:8px;margin:0}}dt{{color:#97adc4}}dd{{margin:0;overflow-wrap:anywhere}}
.demo{{padding:14px 18px;border:1px solid #e9b34d;background:#3c301a;color:#ffd67e;border-radius:10px;margin-bottom:20px;font-weight:bold}}.warning{{color:#ffc681}}
footer{{font-size:13px;color:#97adc4;margin:24px 0 40px}}a{{color:#4bdfc9}}@media(max-width:700px){{.cards{{grid-template-columns:repeat(2,1fr)}}dl{{grid-template-columns:1fr}}dd{{margin-bottom:8px}}main{{padding:0 16px}}}}
@media print{{body{{background:white;color:#152538}}.card,.chart,.details{{background:white;border-color:#c7d2df;break-inside:avoid}}svg text{{fill:#41556c}}}}
</style></head><body><main>{demo}<header><div class="brand">PHONETRACE / 本地性能记录</div>
<h1>{escaped(summary["title"])}</h1><p>手机运行游戏，电脑保留数据。缺失项显示为 —，曲线断开表示无数据。</p></header>
<div class="cards">{card_html}</div><section class="details"><dl>{detail_html}</dl>{error}</section>{charts}
<footer><p>{escaped(_LIMITATIONS)}</p><p>数据文件：<a href="samples.csv">samples.csv</a> · <a href="frame_intervals.csv">frame_intervals.csv</a> · <a href="samples.jsonl">samples.jsonl</a> · <a href="summary.json">summary.json</a></p>
<p>所有图表均嵌入此 HTML，无需联网。若移动报告，请将整个测试文件夹一同移动以保留数据链接。</p></footer>
</main></body></html>'''
