from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="PhoneTrace / 帧迹 · 安卓性能记录")
    parser.add_argument("--demo", action="store_true", help="打开明确标注的模拟数据演示")
    parser.add_argument("--demo-seconds", type=int, default=0, help="自动结束演示的秒数")
    parser.add_argument("--data-dir", type=Path, help="指定本地记录目录")
    parser.add_argument("--screenshot", type=Path, help="测试：保存窗口截图")
    parser.add_argument("--quit-after", type=int, default=0, help="测试：指定秒数后安全退出")
    args = parser.parse_args()
    if args.data_dir:
        os.environ["PHONETRACE_DATA_DIR"] = str(args.data_dir.resolve())
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QFont, QFontDatabase, QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox
    from phonetrace.paths import data_root, resource_root
    from phonetrace.ui import MainWindow
    from phonetrace.controller import Controller

    app = QApplication(sys.argv[:1])
    app.setApplicationName("PhoneTrace")
    app.setOrganizationName("PhoneTrace")
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        for font_path in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/segoeui.ttf"):
            if Path(font_path).exists():
                QFontDatabase.addApplicationFont(font_path)
    app.setFont(QFont("Microsoft YaHei UI", 10))
    icon = resource_root() / "assets" / "phonetrace.ico"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    data_dir = data_root()
    window = MainWindow(data_dir)
    controller = Controller(window, data_dir)
    window.show()

    def exception_hook(kind, value, tb):
        import traceback
        log = data_dir / "error.log"
        with log.open("a", encoding="utf-8") as handle:
            traceback.print_exception(kind, value, tb, file=handle)
        QMessageBox.critical(window, "运行错误", f"{value}\n详细信息已保存到 {log}")

    sys.excepthook = exception_hook
    if args.demo:
        QTimer.singleShot(1200, lambda: controller.start({"demo": True, "interval_s": 0.5, "title": "界面演示（模拟数据）", "duration_s": args.demo_seconds}))
    if args.screenshot:
        def screenshot():
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            window.grab().save(str(args.screenshot))
        QTimer.singleShot(6500 if args.demo else 1500, screenshot)
    if args.quit_after:
        QTimer.singleShot(args.quit_after * 1000, controller.close)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
