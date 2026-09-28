"""Small, read-only ADB adapter; every host process is bounded and shell=False."""
from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import re
import shlex
import subprocess

from .models import Device


class AdbError(RuntimeError):
    """An actionable connection/ADB error suitable for showing in the UI."""


_PACKAGE = re.compile(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+\Z")
_REQUESTED_LAYER = re.compile(
    r"RequestedLayerState\{(?P<name>.+?#[0-9]+)"
    r"(?: (?:parentId|relativeParentId|layerStack)=[0-9]+"
    r"| z=-?[0-9]+| mirrorId=\{(?:[0-9]+,)*\}| !handle)*\}\Z"
)


def validate_package(package: str) -> str:
    package = package.strip()
    if not _PACKAGE.fullmatch(package):
        raise AdbError("游戏包名无效，请选择应用或填写类似 com.example.game 的包名。")
    return package


def layer_matches_package(layer: str, package: str) -> bool:
    return bool(re.search(r"(?<![A-Za-z0-9_.])" + re.escape(package)
                          + r"(?![A-Za-z0-9_.])", layer))


def normalize_layer_name(line: str) -> str:
    """Recover the exact latency-query name from a known SF debug wrapper."""
    # Android 16 --list prints RequestedLayerState::getDebugString(), while
    # --latency still compares Layer::getName(). The #sequence is part of name.
    # Only strip the documented suffix; preserve unknown formats unchanged.
    # https://android.googlesource.com/platform/frameworks/native/+/refs/heads/android16-release/services/surfaceflinger/SurfaceFlinger.cpp
    # https://android.googlesource.com/platform/frameworks/native/+/refs/heads/main/services/surfaceflinger/FrontEnd/RequestedLayerState.cpp
    line = line.strip()
    match = _REQUESTED_LAYER.fullmatch(line)
    return match.group("name") if match else line


def validate_endpoint(endpoint: str) -> str:
    """Accept an explicit TCP port; never interpret flags or remote shell syntax."""
    endpoint = endpoint.strip()
    if endpoint.startswith("["):
        match = re.fullmatch(r"\[([^\]]+)\]:(\d{1,5})", endpoint)
        if not match:
            raise AdbError("IPv6 地址格式应为 [地址]:端口。")
        try:
            ipaddress.IPv6Address(match.group(1))
        except ValueError as exc:
            raise AdbError("IPv6 地址无效。") from exc
    else:
        match = re.fullmatch(r"([A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?):(\d{1,5})", endpoint)
        if not match or ".." in (match.group(1) if match else ""):
            raise AdbError("请输入手机显示的 地址:端口，例如 192.168.1.10:37123。")
        host = match.group(1)
        if re.fullmatch(r"[0-9.]+", host):
            try:
                ipaddress.IPv4Address(host)
            except ValueError as exc:
                raise AdbError("IPv4 地址无效。") from exc
    if not 1 <= int(match.group(2)) <= 65535:
        raise AdbError("端口必须在 1 到 65535 之间。")
    return endpoint


class AdbClient:
    def __init__(self, executable: str | Path):
        self.executable = str(executable)

    def _run(self, args: list[str], timeout: float = 8) -> str:
        try:
            result = subprocess.run(
                [self.executable, *args], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
                shell=False, stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except FileNotFoundError as exc:
            raise AdbError("未找到 ADB。请重新运行启动脚本，或把官方 platform-tools 放到 tools 文件夹。") from exc
        except subprocess.TimeoutExpired as exc:
            raise AdbError("ADB 操作超时。请检查手机连接、无线调试和网络，然后重试。") from exc
        except OSError as exc:
            raise AdbError(f"无法启动 ADB：{exc}") from exc
        output = result.stdout.strip()
        details = (result.stderr.strip() or output)[-1200:]
        if result.returncode:
            lowered = details.lower()
            if "unauthorized" in lowered:
                raise AdbError("手机尚未授权：请解锁手机并允许此电脑进行 USB 调试。")
            if "offline" in lowered or "not found" in lowered or "no devices" in lowered:
                raise AdbError("手机已断开或离线，请检查 USB / 无线调试连接。")
            raise AdbError(f"ADB 操作失败：{details or '未知错误'}")
        return output

    def shell(self, serial: str, command: str, timeout: float = 8) -> str:
        # The caller builds only our fixed read commands; quote every inserted
        # Android value with shlex.quote because adb shell uses the device shell.
        if not serial or len(serial) > 512 or any(c.isspace() or ord(c) < 32 for c in serial):
            raise AdbError("请选择有效的手机设备。")
        return self._run(["-s", serial, "shell", command], timeout)

    def devices(self) -> list[Device]:
        output = self._run(["devices", "-l"], timeout=12)
        result = []
        for line in output.splitlines():
            parts = line.split()
            if len(parts) < 2 or parts[0] in {"List", "*", "adb:"}:
                continue
            if parts[1] not in {"device", "offline", "unauthorized", "recovery", "sideload", "bootloader", "no"}:
                continue
            fields = dict(p.split(":", 1) for p in parts[2:] if ":" in p)
            serial = parts[0]
            transport = "Wi-Fi" if (":" in serial or "_adb-tls-connect" in serial) else "USB"
            result.append(Device(serial=serial, state=parts[1],
                                 model=fields.get("model", "Android").replace("_", " "), transport=transport))
        return result

    def packages(self, serial: str) -> list[str]:
        output = self.shell(serial, "pm list packages", timeout=12)
        return sorted({line[8:].strip() for line in output.splitlines()
                       if line.startswith("package:") and _PACKAGE.fullmatch(line[8:].strip())})

    def foreground_package(self, serial: str) -> str:
        output = self.shell(serial, "dumpsys activity activities", timeout=10)
        pattern = r"([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+)/[A-Za-z0-9_.$]+"
        for marker in ("topResumedActivity", "mResumedActivity", "ResumedActivity"):
            for line in output.splitlines():
                if marker in line and (match := re.search(pattern, line)):
                    return match.group(1)
        output = self.shell(serial, "dumpsys window windows", timeout=10)
        for line in output.splitlines():
            if ("mCurrentFocus" in line or "mFocusedApp" in line) and (match := re.search(pattern, line)):
                return match.group(1)
        return ""

    def layers(self, serial: str, package: str = "") -> list[str]:
        if package:
            package = validate_package(package)
        output = self.shell(serial, "dumpsys SurfaceFlinger --list", timeout=8)
        names = (normalize_layer_name(line) for line in output.splitlines())
        return sorted({name for name in names
                       if name and "permission denial" not in name.lower()
                       and "permission denied" not in name.lower()
                       and (not package or layer_matches_package(name, package))})

    def device_info(self, serial: str) -> dict:
        # Deliberately query an allowlist rather than saving getprop's full output.
        props = {"manufacturer": "ro.product.manufacturer", "model": "ro.product.model",
                 "android_version": "ro.build.version.release", "sdk": "ro.build.version.sdk",
                 "build": "ro.build.display.id"}
        command = "; ".join("printf " + shlex.quote(key + "=") + "; getprop " + shlex.quote(prop)
                             for key, prop in props.items())
        output = self.shell(serial, command)
        info = {}
        for line in output.splitlines():
            key, separator, value = line.partition("=")
            if separator and key in props:
                info[key] = value.strip()
        return info

    def pair(self, endpoint: str, code: str) -> str:
        endpoint = validate_endpoint(endpoint)
        code = code.strip()
        if not re.fullmatch(r"\d{6}", code):
            raise AdbError("请输入手机显示的 6 位无线调试配对码。")
        # Official workflow: https://developer.android.com/tools/adb#wireless
        output = self._run(["pair", endpoint, code], timeout=20)
        if "successfully paired" not in output.lower():
            raise AdbError("配对未成功，请检查配对端口和配对码是否仍有效。")
        return output

    def connect(self, endpoint: str) -> str:
        endpoint = validate_endpoint(endpoint)
        output = self._run(["connect", endpoint], timeout=15)
        if not re.search(r"(?:already )?connected to", output, re.IGNORECASE):
            raise AdbError(f"无线连接未成功：{output[:500]}。请使用无线调试主页面的连接端口。")
        return output
