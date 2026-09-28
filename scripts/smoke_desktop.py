"""Exercise the real desktop/controller/recorder workflow without a phone."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from phonetrace.controller import Controller
from phonetrace.models import Device
from phonetrace.recorder import list_sessions
from phonetrace.ui import MainWindow


def wait_until(predicate, timeout=8):
    started = time.monotonic()
    while not predicate():
        if time.monotonic() - started > timeout:
            raise AssertionError("Timed out waiting for desktop state")
        QTest.qWait(50)


def main():
    app = QApplication([])
    for path in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/segoeui.ttf"):
        if Path(path).exists():
            QFontDatabase.addApplicationFont(path)
    app.setFont(QFont("Microsoft YaHei UI", 10))
    output = ROOT / "test-output" / ("desktop-workflow-" + str(time.time_ns()))
    window = MainWindow(output)
    controller = Controller(window, output)
    window.show()
    QTest.qWait(700)
    wait_until(lambda: not controller.jobs)
    window.grab().save(str(output.parent / "desktop-empty.png"))
    # Use the user's actual controls to start an explicitly labeled demo.
    window.demo_check.setChecked(True)
    window.title_edit.setText("桌面流程测试（模拟数据）")
    window.start_button.click()
    wait_until(lambda: controller.worker is not None and window._sample_count >= 6)
    assert window.recording
    assert not window.device_combo.isEnabled()
    assert not window.start_button.isEnabled()
    window.grab().save(str(output.parent / "desktop-live.png"))
    window.stop_button.click()
    wait_until(lambda: controller.worker is None)
    wait_until(lambda: not controller.jobs)
    sessions = list_sessions(output)
    assert len(sessions) == 1
    assert sessions[0]["status"] == "completed"
    assert sessions[0]["demo"] is True
    assert sessions[0]["sample_count"] >= 6
    controller.open_session(sessions[0]["path"])
    wait_until(lambda: not controller.jobs)
    assert window._history_mode
    assert all(chart.full_history for chart in window.charts.values())
    window.grab().save(str(output.parent / "desktop-replay.png"))
    # Close while recording must first flush and stop the worker.
    window.start_button.click()
    wait_until(lambda: controller.worker is not None and window._sample_count >= 2)
    window.close()
    wait_until(lambda: controller.worker is None)
    QTest.qWait(150)
    assert not window.isVisible()
    sessions = list_sessions(output)
    assert len(sessions) == 2
    assert all(row["status"] == "completed" for row in sessions)
    print(json.dumps({"desktop_workflow": "passed", "sessions": len(sessions), "records": str(output)}, ensure_ascii=False))
    controller.pool.waitForDone(3000)


if __name__ == "__main__":
    main()
