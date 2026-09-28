from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThread, QThreadPool, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from .adb import AdbClient
from .collector import Collector
from .demo import DemoCollector
from .models import Sample, SessionConfig
from .paths import find_adb
from .recorder import SessionRecorder, list_sessions, load_session


class JobSignals(QObject):
    result = Signal(object)
    error = Signal(object)
    done = Signal()


class Job(QRunnable):
    def __init__(self, key, fn, token=""):
        super().__init__()
        self.key, self.fn, self.token = key, fn, token
        self.signals = JobSignals()

    @Slot()
    def run(self):
        try:
            self.signals.result.emit((self.key, self.token, self.fn()))
        except Exception as exc:
            self.signals.error.emit((self.key, self.token, str(exc)))
        finally:
            self.signals.done.emit()


class RecordingWorker(QThread):
    sample_ready = Signal(object)
    report_ready = Signal(object)
    problem = Signal(str)
    status = Signal(str)

    def __init__(self, client, config: SessionConfig, root: Path):
        super().__init__()
        self.client, self.config, self.root = client, config, root
        self.stop_event = threading.Event()

    def stop(self):
        self.stop_event.set()

    def run(self):
        recorder = collector = None
        status, error = "completed", ""
        started = None
        failures = 0
        try:
            if self.config.demo:
                info = {"model": "演示设备（模拟数据）", "android_version": "演示", "transport": "demo"}
                collector = DemoCollector(self.config)
            else:
                self.status.emit("正在读取设备信息并识别游戏图层…")
                info = self.client.device_info(self.config.serial)
                if self.stop_event.is_set():
                    return
                collector = Collector(self.client, self.config)
            recorder = SessionRecorder(self.root, self.config, info)
            started = time.monotonic()
            self.status.emit("演示记录中 · 所有数值均为模拟数据" if self.config.demo else "正在记录 · 数据实时保存到电脑")
            while not self.stop_event.is_set():
                tick = time.monotonic()
                if self.config.duration_s and tick - started >= self.config.duration_s:
                    break
                try:
                    sample = collector.sample()
                    # One clock for successful reads, failures and test duration.
                    sample.elapsed_s = time.monotonic() - started
                    failures = 0
                except Exception as exc:
                    failures += 1
                    error = str(exc)
                    sample = Sample(
                        timestamp=datetime.now().astimezone().isoformat(timespec="milliseconds"),
                        elapsed_s=time.monotonic() - started,
                        notes=[f"设备读取失败（{failures}/3）：{error}"],
                    )
                recorder.append(sample)
                self.sample_ready.emit(sample)
                if failures >= 3:
                    status = "interrupted"
                    self.problem.emit("连续三次读取失败，已结束记录并保留数据。\n" + error)
                    break
                self.stop_event.wait(max(0, self.config.interval_s - (time.monotonic() - tick)))
            if failures == 0:
                error = ""
        except Exception as exc:
            status, error = "error", str(exc)
            self.problem.emit("记录未能继续：" + error)
        finally:
            if collector:
                try:
                    collector.close()
                except Exception:
                    pass
            if recorder:
                try:
                    report = recorder.finish(status=status, error=error)
                    self.report_ready.emit(report)
                except Exception as exc:
                    self.problem.emit(f"报告生成失败，已写入的原始数据仍保留在 {recorder.path}\n{exc}")


class Controller(QObject):
    def __init__(self, window, data_dir: Path):
        super().__init__(window)
        self.window, self.data_dir = window, data_dir
        adb_path = find_adb()
        self.client = AdbClient(adb_path) if adb_path else None
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(3)
        self.jobs = set()
        self.worker: RecordingWorker | None = None
        self.serial = ""
        self._closing = False
        self._last_report = None
        window.refresh_requested.connect(self.refresh)
        window.device_changed.connect(self.device_changed)
        window.detect_requested.connect(self.detect)
        window.layers_requested.connect(self.layers)
        window.start_requested.connect(self.start)
        window.stop_requested.connect(self.stop)
        window.pair_requested.connect(self.pair)
        window.connect_requested.connect(self.connect_device)
        window.history_requested.connect(self.history)
        window.session_open_requested.connect(self.open_session)
        window.open_folder_requested.connect(self.open_folder)
        window.close_requested.connect(self.close)
        QTimer.singleShot(100, self.refresh)
        QTimer.singleShot(150, self.history)

    def job(self, key, fn, token=""):
        job = Job(key, fn, token)
        self.jobs.add(job)
        self.window.set_busy(True)
        job.signals.result.connect(self.job_result)
        job.signals.error.connect(self.job_error)
        job.signals.done.connect(lambda: self.job_done(job))
        self.pool.start(job)

    def job_done(self, job):
        self.jobs.discard(job)
        self.window.set_busy(bool(self.jobs))

    def require_adb(self):
        if self.client:
            return True
        self.window.show_error("找不到 adb.exe。请保留安装目录的 tools/platform-tools 文件夹，或按使用说明配置 Android Platform Tools。也可以先体验演示模式。")
        return False

    @Slot()
    def refresh(self):
        if self.worker:
            return
        if not self.client:
            self.window.set_status("ADB 未找到 · 可先体验演示模式", "warning")
            return
        self.window.set_status("正在查找已连接的手机…")
        self.job("devices", self.client.devices)

    @Slot(str)
    def device_changed(self, serial):
        if self.worker:
            return
        self.serial = serial
        self.window.set_packages([])
        self.window.set_layers([])
        if serial and self.client:
            self.job("packages", lambda: self.client.packages(serial), serial)

    @Slot()
    def detect(self):
        if self.require_adb() and self.serial:
            self.job("detect", lambda: self.client.foreground_package(self.serial), self.serial)
        elif self.client:
            self.window.set_status("请先选择在线设备，并在手机上打开游戏。", "warning")

    @Slot()
    def layers(self):
        options = self.window.get_options()
        serial, package = options.get("serial", ""), options.get("package", "").strip()
        if self.require_adb() and serial:
            self.job("layers", lambda: self.client.layers(serial, package), serial)

    @Slot(str, str)
    def pair(self, endpoint, code):
        if self.worker or not self.require_adb():
            return
        self.window.set_status("正在配对手机…")
        self.job("pair", lambda: self.client.pair(endpoint.strip(), code.strip()))

    @Slot(str)
    def connect_device(self, endpoint):
        if self.worker or not self.require_adb():
            return
        self.window.set_status("正在连接无线调试设备…")
        self.job("connect", lambda: self.client.connect(endpoint.strip()))

    @Slot(object)
    def job_result(self, payload):
        key, token, value = payload
        if token and token != self.serial:
            return
        if key == "devices":
            self.window.set_devices(value)
            ready = [d for d in value if d.state == "device"]
            if not ready:
                self.serial = ""
                message = "尚未发现手机 · 连接 USB 并在手机上允许调试，或在无线连接页配对。"
                if value:
                    message = "设备尚未授权或离线 · 请解锁手机并允许 USB 调试，然后刷新。"
                self.window.set_status(message, "warning")
            else:
                selected = self.window.get_options().get("serial", "")
                if selected != self.serial:
                    self.device_changed(selected)
                self.window.set_status(f"已发现 {len(ready)} 台在线设备 · 请打开游戏并识别当前应用")
        elif key == "packages":
            self.window.set_packages(value)
        elif key == "detect":
            if value:
                self.window.set_package(value)
                self.window.set_status("已识别当前应用，可直接开始记录。")
                self.layers()
            else:
                self.window.set_status("未能识别前台应用，可手动选择或输入游戏包名。", "warning")
        elif key == "layers":
            self.window.set_layers(value)
            self.window.set_status(f"找到 {len(value)} 个候选图层 · 默认自动选择游戏图层" if value else "暂未找到游戏图层 · 请进入游戏画面后重试；其他指标仍可记录。", "info" if value else "warning")
        elif key in ("pair", "connect"):
            self.window.set_status(str(value))
            self.refresh()
        elif key == "history":
            if not self.worker:
                self.window.set_history(value)
        elif key == "session":
            self.window.show_session(*value)

    @Slot(object)
    def job_error(self, payload):
        key, token, message = payload
        if token and token != self.serial:
            return
        self.window.set_status(message, "error")
        if key in ("pair", "connect", "session"):
            self.window.show_error(message)

    @Slot(object)
    def start(self, options):
        if self.worker:
            return
        demo = bool(options.get("demo", False))
        serial = str(options.get("serial", "")).strip()
        package = str(options.get("package", "")).strip()
        if not demo:
            if not self.require_adb():
                return
            if not serial or not package:
                self.window.show_error("请选择在线手机和游戏包名。也可以点击识别当前应用。")
                return
            # Only package identifiers are accepted; shell quoting is still done by AdbClient.
            import re
            if not re.fullmatch(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+", package):
                self.window.show_error("包名格式不正确，例如 com.tencent.tmgp.sgame。请输入应用包名。")
                return
        config = SessionConfig(
            serial="DEMO" if demo else serial,
            package="demo.synthetic.game" if demo else package,
            layer=str(options.get("layer", "")),
            interval_s=max(0.25, min(5.0, float(options.get("interval_s", 0.5)))),
            title=str(options.get("title", "游戏测试")).strip() or "游戏测试",
            demo=demo,
            duration_s=max(0, int(options.get("duration_s", 0))),
            metadata={"app_version": "0.1.0", "metric_notes": "整机电池放电估计；FPS 为选定 SurfaceFlinger 图层呈现间隔；慢帧为大于50ms的观测间隔。"},
        )
        self.window.reset_charts()
        self.window.set_sampling_interval(config.interval_s)
        self.window.set_recording(True, demo=demo)
        self._last_report = None
        worker = RecordingWorker(self.client, config, self.data_dir)
        self.worker = worker
        worker.sample_ready.connect(self.window.add_sample)
        worker.status.connect(self.window.set_status)
        worker.problem.connect(lambda text: self.window.set_status(text, "error"))
        worker.report_ready.connect(self.received_report)
        worker.finished.connect(self.worker_finished)
        worker.start()

    @Slot(object)
    def received_report(self, report):
        self._last_report = report
        self.window.set_summary(report)

    @Slot()
    def worker_finished(self):
        worker, self.worker = self.worker, None
        self.window.set_recording(False)
        if self._last_report:
            interrupted = self._last_report.get("status") != "completed"
            self.window.set_status("记录已中断，已采集数据保存在电脑。" if interrupted else "记录已保存 · 可在历史记录中回看，或打开记录文件夹查看 CSV 和报告。", "warning" if interrupted else "success")
        self.history()
        if worker:
            worker.deleteLater()
        if self._closing:
            QTimer.singleShot(0, self.window.close)

    @Slot()
    def stop(self):
        if self.worker:
            self.window.set_status("正在结束采集并保存报告…")
            self.worker.stop()

    @Slot()
    def history(self):
        if self.worker:
            return
        self.job("history", lambda: list_sessions(self.data_dir))

    @Slot(str)
    def open_session(self, path):
        if self.worker:
            return
        target = Path(path).resolve()
        # The history list only offers this data root; reject arbitrary paths from signals.
        if not target.is_relative_to(self.data_dir.resolve()):
            self.window.show_error("只能打开当前记录文件夹内的测试记录。")
            return
        self.job("session", lambda: load_session(target))

    @Slot()
    def open_folder(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.data_dir)))

    @Slot()
    def close(self):
        self._closing = True
        if self.worker:
            self.stop()
        else:
            self.window.close()
