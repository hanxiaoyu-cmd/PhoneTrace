"""Verify the portable executable in isolation from the Python environment."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

root = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--dist-root", default="dist", help="Build output directory, relative to the project or absolute (default: dist)")
args = parser.parse_args()
dist = Path(args.dist_root)
dist = (dist if dist.is_absolute() else root / dist).resolve()
if dist == root or not dist.is_relative_to(root):
    parser.error("--dist-root must be a subdirectory of this project")
exe = dist / "PhoneTrace" / "PhoneTrace.exe"
if not exe.is_file():
    parser.error(f"Portable executable not found: {exe}. Run build.ps1 with this output directory first.")
output = root / "test-output" / ("packaged-" + str(time.time_ns()))
output.mkdir(parents=True)
env = dict(os.environ)
env.pop("PYTHONPATH", None)
env.pop("PYTHONHOME", None)
env["PATH"] = os.pathsep.join((str(Path(os.environ["SystemRoot"]) / "System32"), os.environ["SystemRoot"]))
env["QT_QPA_PLATFORM"] = "offscreen"
result = subprocess.run(
    [str(exe), "--demo", "--demo-seconds", "8", "--quit-after", "11",
     "--data-dir", str(output), "--screenshot", str(output / "preview.png")],
    cwd=output, env=env, timeout=35, capture_output=True,
    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
)
assert result.returncode == 0, (result.returncode, result.stderr)
assert (output / "preview.png").stat().st_size > 1000
summaries = list(output.glob("*/summary.json"))
assert len(summaries) == 1, summaries
summary = json.loads(summaries[0].read_text(encoding="utf-8"))
assert summary["demo"] is True
assert summary["status"] == "completed"
assert summary["sample_count"] >= 10
assert summary["avg_fps"] is not None and summary["avg_power_w"] is not None
assert not (output / "error.log").exists()
for filename in ("samples.csv", "samples.jsonl", "frame_intervals.csv", "metadata.json", "report.html"):
    assert (summaries[0].parent / filename).stat().st_size > 0
adb = subprocess.run([str(exe.parent / "tools" / "platform-tools" / "adb.exe"), "version"],
                     capture_output=True, text=True, timeout=10)
assert adb.returncode == 0
# Exercise the native Windows platform as well as the offscreen rendering path.
native_env = dict(env)
native_env.pop("QT_QPA_PLATFORM", None)
native_output = output / "native-window"
native = subprocess.run(
    [str(exe), "--quit-after", "4", "--data-dir", str(native_output),
     "--screenshot", str(output / "native-preview.png")],
    cwd=output, env=native_env, timeout=20, capture_output=True,
    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
)
assert native.returncode == 0, (native.returncode, native.stderr)
assert (output / "native-preview.png").stat().st_size > 1000
assert not (native_output / "error.log").exists()
print(json.dumps({"portable_exe": "passed", "records": str(output), "samples": summary["sample_count"]}, ensure_ascii=False))
