from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", app_root()))


def find_adb() -> Path | None:
    candidates = [
        app_root() / "tools" / "platform-tools" / "adb.exe",
        resource_root() / "tools" / "platform-tools" / "adb.exe",
    ]
    sdk = os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME")
    if sdk:
        candidates.append(Path(sdk) / "platform-tools" / "adb.exe")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "Android" / "Sdk" / "platform-tools" / "adb.exe")
    on_path = shutil.which("adb")
    if on_path:
        candidates.append(Path(on_path))
    return next((path for path in candidates if path.is_file()), None)


def data_root() -> Path:
    override = os.environ.get("PHONETRACE_DATA_DIR")
    if override:
        result = Path(override).expanduser().resolve()
    else:
        result = app_root() / "records"
    try:
        result.mkdir(parents=True, exist_ok=True)
        probe = result / ".write_check"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError:
        result = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "PhoneTrace" / "records"
        result.mkdir(parents=True, exist_ok=True)
    return result
