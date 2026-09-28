"""Chinese desktop interface; every device operation is delegated to the controller."""

from __future__ import annotations

import math
from pathlib import Path
from statistics import median

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QSpinBox, QStackedWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from .charts import TimeSeriesChart, elapsed_label
from .models import Device, Sample


STYLE = """
QMainWindow, QWidget { background: #0a1322; color: #dfe9f8; font-family: 'Microsoft YaHei UI', 'Microsoft YaHei', sans-serif; font-size: 13px; }
QFrame#sidebar { background: #0d192a; border-right: 1px solid #233248; }
QFrame#sidebar QLabel { background: transparent; }
QFrame#card { background: #111e30; border: 1px solid #25354b; border-radius: 12px; }
QFrame#card QLabel, QFrame#card QWidget { background: transparent; }
QLabel#heading { font-size: 24px; font-weight: 700; color: #f1f7ff; }
QLabel#sectionTitle { font-size: 16px; font-weight: 600; }
QLabel#muted { color: #8fa5c0; }
QLabel#badge { color: #4dd4d0; background: #133233; border: 1px solid #265457; border-radius: 6px; padding: 5px 10px; font-size: 12px; }
QLabel#banner { background: #13283d; border: 1px solid #284967; border-radius: 8px; padding: 11px 14px; color: #a9cce9; }
QLabel#demoBanner { background: #3d2c13; border: 1px solid #795923; border-radius: 8px; padding: 11px 14px; color: #f2cd8c; }
QLabel#metricValue { font-size: 27px; font-weight: 600; color: #f5f9ff; }
QLabel#metricUnit { font-size: 11px; color: #7891af; }
QPushButton { background: #1c2d45; color: #dce9fa; border: 1px solid #354963; border-radius: 7px; padding: 8px 13px; font-weight: 500; }
QPushButton:hover { background: #28405d; border-color: #527295; }
QPushButton:pressed { background: #102238; }
QPushButton:disabled { background: #172133; color: #526680; border-color: #263246; }
QPushButton#primary { background: #36c7c0; color: #052325; border-color: #36c7c0; font-weight: 700; padding: 10px 20px; }
QPushButton#primary:hover { background: #6adeD8; }
QPushButton#primary:disabled { background: #234a50; color: #679295; border-color: #234a50; }
QPushButton#danger { background: #542a36; border-color: #78404d; color: #ffc5cb; padding: 10px 20px; }
QPushButton#nav { text-align: left; border: none; background: transparent; color: #93a9c4; padding: 12px 16px; font-size: 14px; }
QPushButton#nav:checked { background: #19383e; color: #68e0d8; border-left: 3px solid #48d4cd; }
QPushButton#nav:hover { background: #172b42; }
QLineEdit, QComboBox, QSpinBox { background: #0c1727; color: #e2edfb; border: 1px solid #334660; border-radius: 6px; padding: 7px 9px; min-height: 20px; selection-background-color: #276468; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #48cec8; }
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled { color: #62758f; background: #131e2f; border-color: #263449; }
QComboBox QAbstractItemView { background: #15263b; selection-background-color: #23505a; color: #e0edfc; }
QComboBox::drop-down { border: none; width: 23px; }
QCheckBox { spacing: 8px; color: #b8cbe0; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px; border: 1px solid #506680; background: #102033; }
QCheckBox::indicator:checked { background: #39c6bf; border-color: #39c6bf; }
QScrollArea { border: none; }
QScrollBar:vertical { background: #0c1727; width: 9px; }
QScrollBar::handle:vertical { background: #30445e; border-radius: 4px; min-height: 35px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QTableWidget { background: #101d2f; alternate-background-color: #142238; gridline-color: #29394f; border: 1px solid #283a51; border-radius: 8px; selection-background-color: #22454f; }
QHeaderView::section { background: #1b2c43; color: #9db7d4; border: none; padding: 11px 9px; }
QTableWidget::item { padding: 8px; }
QToolTip { color: #e5f2ff; background: #20344d; border: 1px solid #426581; padding: 6px; }
"""


def text_label(text: str, kind: str = "", wrap=False) -> QLabel:
    label = QLabel(text)
    if kind:
        label.setObjectName(kind)
    label.setWordWrap(wrap)
    return label


def button(text: str, callback=None, kind="") -> QPushButton:
    result = QPushButton(text)
    if kind:
        result.setObjectName(kind)
    if callback:
        result.clicked.connect(callback)
    return result


def card() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(12)
    return frame, layout


def number(value, places=1) -> str:
    try:
        return f"{float(value):.{places}f}" if value is not None and math.isfinite(float(value)) else "—"
    except (TypeError, ValueError):
        return "—"


class MainWindow(QMainWindow):
    refresh_requested = Signal()
    device_changed = Signal(str)
    detect_requested = Signal()
    layers_requested = Signal()
    start_requested = Signal(object)
    stop_requested = Signal()
    pair_requested = Signal(str, str)
    connect_requested = Signal(str)
    history_requested = Signal()
    session_open_requested = Signal(str)
    open_folder_requested = Signal()
    close_requested = Signal()

    def __init__(self, data_dir: Path):
        super().__init__()
        self.data_dir = Path(data_dir)
        self.recording = False
        self._busy = False
        self._history_mode = False
        self._device_list: list[Device] = []
        self._history_rows: list[dict] = []
        self._sample_count = 0
        self._demo_recording = False
        self.setWindowTitle("PhoneTrace · 手机游戏性能记录")
        self.resize(1320, 920)
        self.setMinimumSize(900, 650)
        self.setStyleSheet(STYLE)
        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(190)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(13, 25, 13, 20)
        brand = text_label("PhoneTrace")
        brand.setStyleSheet("font-size:21px; font-weight:700; color:#66ddd5; background:transparent;")
        side.addWidget(brand)
        side.addWidget(text_label("手机游戏性能记录", "muted"))
        side.addSpacing(30)
        self.nav_buttons = []
        for index, caption in enumerate(("01   实时监测", "02   连接手机", "03   测试记录", "04   使用说明")):
            nav = button(caption, lambda checked=False, i=index: self._navigate(i), "nav")
            nav.setCheckable(True)
            side.addWidget(nav)
            self.nav_buttons.append(nav)
        side.addStretch()
        side.addWidget(text_label("数据仅保存在本机", "muted"))
        side.addWidget(text_label("自动保存 CSV / JSONL\n测试报告 HTML", "muted", True))
        self.folder_button = button("打开记录文件夹 ↗", self.open_folder_requested.emit)
        self.folder_button.setToolTip(str(self.data_dir))
        side.addWidget(self.folder_button)
        side.addSpacing(12)
        side.addWidget(text_label("免费 · 无需 Root", "muted"))
        outer.addWidget(sidebar)

        shell = QWidget()
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(25, 22, 25, 14)
        shell_layout.setSpacing(14)
        header = QHBoxLayout()
        self.page_heading = text_label("实时监测", "heading")
        header.addWidget(self.page_heading)
        header.addStretch()
        self.state_badge = text_label("就绪", "badge")
        header.addWidget(self.state_badge)
        shell_layout.addLayout(header)
        self.stack = QStackedWidget()
        shell_layout.addWidget(self.stack, 1)
        self.status_label = text_label("连接手机或启用演示模式，然后开始记录。", "muted", True)
        self.status_label.setMinimumHeight(24)
        shell_layout.addWidget(self.status_label)
        outer.addWidget(shell, 1)

        self._build_monitor()
        self._build_connection()
        self._build_history()
        self._build_help()
        self._navigate(0)
        self._update_enabled()
        self._update_banner()

    def _page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(14)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        self.stack.addWidget(scroll)
        return page, layout

    def _build_monitor(self):
        _, layout = self._page()
        self.banner = text_label("", "banner", True)
        layout.addWidget(self.banner)
        config, config_layout = card()
        row = QHBoxLayout()
        row.addWidget(text_label("测试设置", "sectionTitle"))
        row.addStretch()
        self.demo_check = QCheckBox("演示模式（模拟数据）")
        self.demo_check.toggled.connect(self._demo_toggled)
        row.addWidget(self.demo_check)
        config_layout.addLayout(row)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        self.device_combo = QComboBox()
        self.device_combo.addItem("未发现手机", "")
        self.device_combo.setMinimumWidth(165)
        self.device_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.device_combo.currentIndexChanged.connect(self._device_selected)
        self.refresh_button = button("刷新", self.refresh_requested.emit)
        device_row = QHBoxLayout()
        device_row.addWidget(self.device_combo, 1)
        device_row.addWidget(self.refresh_button)
        self.package_combo = QComboBox()
        self.package_combo.setEditable(True)
        self.package_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.package_combo.lineEdit().setPlaceholderText("先在手机打开游戏，或输入包名")
        self.package_combo.currentTextChanged.connect(lambda _: self._update_enabled())
        self.detect_button = button("识别前台", self.detect_requested.emit)
        package_row = QHBoxLayout()
        package_row.addWidget(self.package_combo, 1)
        package_row.addWidget(self.detect_button)
        grid.addWidget(text_label("手机设备", "muted"), 0, 0)
        grid.addWidget(text_label("游戏 / 应用包名", "muted"), 0, 1)
        grid.addLayout(device_row, 1, 0)
        grid.addLayout(package_row, 1, 1)
        self.layer_combo = QComboBox()
        self.layer_combo.setEditable(True)
        self.layer_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.layer_combo.addItem("自动选择游戏图层", "")
        self.layer_combo.setToolTip("默认自动选择当前游戏图层；帧率不可用时刷新并手动选择包含游戏包名的 SurfaceView 图层。")
        self.layers_button = button("刷新图层", self.layers_requested.emit)
        layer_row = QHBoxLayout()
        layer_row.addWidget(self.layer_combo, 1)
        layer_row.addWidget(self.layers_button)
        grid.addWidget(text_label("帧率采集图层", "muted"), 2, 0, 1, 2)
        grid.addLayout(layer_row, 3, 0, 1, 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        config_layout.addLayout(grid)

        details = QHBoxLayout()
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("测试名称，例如：原神 · 高画质 · 25℃")
        details.addWidget(self.title_edit, 3)
        details.addWidget(text_label("采样", "muted"))
        self.interval_combo = QComboBox()
        for caption, value in (("0.5 秒", 0.5), ("1 秒", 1.0), ("2 秒", 2.0)):
            self.interval_combo.addItem(caption, value)
        details.addWidget(self.interval_combo)
        details.addWidget(text_label("时长", "muted"))
        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(0, 1440)
        self.duration_spin.setSpecialValueText("手动停止")
        self.duration_spin.setSuffix(" 分钟")
        self.duration_spin.setToolTip("设为 0 时手动停止；其他值到时自动保存。")
        self.duration_spin.setMinimumWidth(108)
        details.addWidget(self.duration_spin)
        config_layout.addLayout(details)

        actions = QHBoxLayout()
        self.elapsed_label = text_label("00:00", "sectionTitle")
        actions.addWidget(self.elapsed_label)
        self.samples_label = text_label("0 个采样点", "muted")
        actions.addWidget(self.samples_label)
        actions.addStretch()
        self.start_button = button("开始记录", self._start, "primary")
        self.stop_button = button("停止并保存", self.stop_requested.emit, "danger")
        actions.addWidget(self.start_button)
        actions.addWidget(self.stop_button)
        config_layout.addLayout(actions)
        layout.addWidget(config)

        metrics = QHBoxLayout()
        metrics.setSpacing(10)
        self.metric_labels = {}
        for key, name, unit in (
            ("fps", "游戏帧率", "FPS"), ("power_w", "整机放电功率", "W · 电池端估算"),
            ("battery_temp_c", "电池温度", "℃"), ("cpu_pct", "整机 CPU", "%"),
            ("battery_pct", "剩余电量", "%"), ("app_mem_mb", "应用内存", "MiB · PSS · 5秒采样"),
        ):
            frame, inner = card()
            inner.setContentsMargins(13, 12, 13, 11)
            inner.setSpacing(3)
            inner.addWidget(text_label(name, "muted"))
            value = text_label("—", "metricValue")
            inner.addWidget(value)
            inner.addWidget(text_label(unit, "metricUnit"))
            self.metric_labels[key] = value
            metrics.addWidget(frame, 1)
        layout.addLayout(metrics)

        charts = QGridLayout()
        charts.setSpacing(12)
        self.charts = {
            "fps": TimeSeriesChart("游戏帧率", "FPS", "#52d7ce"),
            "power_w": TimeSeriesChart("整机放电功率", "W", "#7eabff"),
            "battery_temp_c": TimeSeriesChart("电池温度", "℃", "#edb875", zero_base=False),
            "cpu_pct": TimeSeriesChart("整机 CPU 使用率", "%", "#aa97f5"),
        }
        for i, chart_widget in enumerate(self.charts.values()):
            charts.addWidget(chart_widget, i // 2, i % 2)
        layout.addLayout(charts)
        self.detail_label = text_label("帧耗时 — ms    P95 — ms    慢帧 —    电流 — mA    电压 — V", "muted", True)
        layout.addWidget(self.detail_label)
        self.source_label = text_label("帧率：等待采集  ·  功耗：仅在确认拔线放电时显示", "muted", True)
        self.source_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.source_label)
        self.notes_label = text_label("提示：游戏插帧、受限图层或系统权限可能影响帧率读取；无数据会显示 —。", "muted", True)
        layout.addWidget(self.notes_label)
        self.summary_label = text_label("", "banner", True)
        self.summary_label.hide()
        layout.addWidget(self.summary_label)
        layout.addStretch()

    def _build_connection(self):
        _, layout = self._page()
        layout.addWidget(text_label("USB 用来建立调试连接；测功耗建议切换无线连接后拔掉数据线。", "banner", True))
        wired, inner = card()
        inner.addWidget(text_label("01  ·  首次连接：USB 调试", "sectionTitle"))
        inner.addWidget(text_label("1. 手机开启开发者选项与 USB 调试。\n2. 使用可传输数据的数据线连接电脑，手机保持解锁。\n3. 在手机上允许本台电脑调试，再点击下方刷新。\n4. 回到实时监测，选择手机、打开游戏并点击“识别前台”。", "muted", True))
        self.connection_refresh = button("刷新 USB / 已连接设备", self.refresh_requested.emit)
        inner.addWidget(self.connection_refresh, alignment=Qt.AlignmentFlag.AlignLeft)
        self.devices_note = text_label("尚未发现设备", "muted", True)
        inner.addWidget(self.devices_note)
        layout.addWidget(wired)

        wireless, inner = card()
        inner.addWidget(text_label("02  ·  无线调试：配对与连接", "sectionTitle"))
        inner.addWidget(text_label("Android 11 及以上：电脑和手机连接同一局域网，在手机“无线调试”中开启功能。配对端口与连接端口通常不同，请分别填写。", "muted", True))
        pairing_grid = QGridLayout()
        pairing_grid.addWidget(text_label("配对地址（“使用配对码配对设备”页面）", "muted"), 0, 0)
        pairing_grid.addWidget(text_label("6 位配对码", "muted"), 0, 1)
        self.pair_endpoint = QLineEdit()
        self.pair_endpoint.setPlaceholderText("192.168.1.8:配对端口")
        self.pair_code = QLineEdit()
        self.pair_code.setPlaceholderText("123456")
        self.pair_code.setMaxLength(6)
        self.pair_button = button("① 配对", self._pair)
        pairing_grid.addWidget(self.pair_endpoint, 1, 0)
        pairing_grid.addWidget(self.pair_code, 1, 1)
        pairing_grid.addWidget(self.pair_button, 1, 2)
        pairing_grid.addWidget(text_label("连接地址（返回“无线调试”主页面查看 IP 地址和端口）", "muted"), 2, 0, 1, 3)
        self.connect_endpoint = QLineEdit()
        self.connect_endpoint.setPlaceholderText("192.168.1.8:连接端口")
        self.connect_button = button("② 连接", self._connect)
        pairing_grid.addWidget(self.connect_endpoint, 3, 0, 1, 2)
        pairing_grid.addWidget(self.connect_button, 3, 2)
        pairing_grid.setColumnStretch(0, 3)
        pairing_grid.setColumnStretch(1, 1)
        inner.addLayout(pairing_grid)
        inner.addWidget(text_label("连接成功后拔掉 USB，并在实时监测中选择无线设备。之后可直接填写连接地址连接，通常无需重复配对。网络变更或无线调试重启可能改变端口。", "muted", True))
        layout.addWidget(wireless)
        note, inner = card()
        inner.addWidget(text_label("连接状态说明", "sectionTitle"))
        inner.addWidget(text_label("• unauthorized：请解锁手机并确认 USB 调试授权。\n• offline：重新连接数据线或重新开启无线调试后刷新。\n• 连接正常但帧率为空：先进入游戏实际画面，再刷新图层。\n• 功耗为空：可能正在充电、未确认放电，或系统不提供可读电流。\n• 软件不会自动下载或上传测试记录。", "muted", True))
        layout.addWidget(note)
        layout.addStretch()

    def _build_history(self):
        _, layout = self._page()
        actions = QHBoxLayout()
        actions.addWidget(text_label("选择一条记录以查看完整曲线；文件夹中还包含 CSV 和离线报告。", "muted", True), 1)
        self.history_refresh = button("刷新记录", self.history_requested.emit)
        self.history_view = button("查看所选记录", self._open_history, "primary")
        actions.addWidget(self.history_refresh)
        actions.addWidget(self.history_view)
        layout.addLayout(actions)
        self.history_table = QTableWidget(0, 7)
        self.history_table.setHorizontalHeaderLabels(["测试名称", "开始时间", "时长", "平均 FPS", "平均功耗 W", "采样点", "状态"])
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.history_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_table.setAlternatingRowColors(True)
        self.history_table.verticalHeader().hide()
        self.history_table.verticalHeader().setDefaultSectionSize(43)
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.history_table.setMinimumHeight(360)
        self.history_table.itemDoubleClicked.connect(lambda _: self._open_history())
        self.history_table.itemSelectionChanged.connect(self._update_enabled)
        layout.addWidget(self.history_table)
        self.history_note = text_label("还没有测试记录。开始一次真实测试或演示后，记录会自动保存在本机。", "muted", True)
        layout.addWidget(self.history_note)
        layout.addStretch()

    def _build_help(self):
        _, layout = self._page()
        for title, body in (
            ("从这里开始", "连接手机 → 打开游戏 → 识别前台应用 → 开始记录。可以先用演示模式熟悉操作；演示数据会在界面、记录与报告中明确标记。测试过程中数据自动写入电脑，停止后生成汇总和 HTML 报告。"),
            ("帧率是什么", "采集游戏 SurfaceFlinger 图层可用的呈现时间戳，计算观察窗口内的 FPS 与帧间隔。它不是屏幕刷新率。系统限制、图层切换、插帧、时间戳缓冲区长度及轮询间隔都会影响覆盖率；无法取得时显示 —，不会用屏幕刷新率替代。推荐 0.5 秒采样；高帧率场景依然可能漏帧。"),
            ("如何理解功耗", "功率是电池端电流与电压的整机放电估算，包含屏幕、网络、处理器等，不等于芯片功耗。只有确认未插电且正在放电时才显示。不同系统的电流上报周期和权限不同；软件无法读取时留空。无线连接后拔掉 USB，等待电源状态更新。"),
            ("其他参数", "电池温度不等于处理器结温或机身表面温度。CPU 为整机 CPU 使用率，应用内存为 PSS（MiB），每 5 秒采样一次，其余采样点留空。慢帧指本轮观察到的间隔超过 50 ms 的帧，不等同于厂商定义的 Jank。P95 为本次采样窗口的第 95 百分位帧间隔。电流保留系统原始正负号。"),
            ("让不同测试可比较", "固定室温、亮度、画质、帧率上限和性能模式；从接近的电量与温度开始。进入同一关卡、跑相同路线，连续测试 20–30 分钟。加载、菜单与过场会影响统计。散热背夹、游戏插帧和录屏请单独分组测试。"),
            ("记录保存在哪里", f"默认目录：{self.data_dir}\n每次测试独立保存采样 CSV、原始 JSONL、观察到的帧间隔、设备与会话元数据、汇总 JSON，以及可在浏览器打开的离线 HTML 报告。CSV 可用 Excel 打开。空值保留为空，不会作为零计算。测试记录不会自动上传。"),
        ):
            frame, inner = card()
            inner.addWidget(text_label(title, "sectionTitle"))
            body_label = text_label(body, "muted", True)
            body_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            inner.addWidget(body_label)
            layout.addWidget(frame)
        layout.addStretch()

    def _navigate(self, index: int):
        if self.recording and index == 2:
            return
        self.stack.setCurrentIndex(index)
        self.page_heading.setText(("实时监测", "连接手机", "测试记录", "使用说明")[index])
        for i, nav in enumerate(self.nav_buttons):
            nav.setChecked(i == index)
        if index == 2:
            self.history_requested.emit()

    def _device_selected(self, index):
        self._update_enabled()
        self._update_banner()
        if index >= 0:
            self.device_changed.emit(self.device_combo.currentData() or "")

    def _demo_toggled(self, enabled):
        self._update_enabled()
        self._update_banner()

    def _online_device(self):
        serial = self.device_combo.currentData()
        return any(d.serial == serial and d.state == "device" for d in self._device_list)

    def _update_enabled(self):
        if not hasattr(self, "start_button"):
            return
        idle = not self.recording and not self._busy
        demo = self.demo_check.isChecked()
        online = self._online_device()
        for widget in (self.device_combo, self.refresh_button, self.demo_check, self.title_edit, self.interval_combo, self.duration_spin):
            widget.setEnabled(idle)
        for widget in (self.package_combo, self.layer_combo, self.detect_button, self.layers_button):
            widget.setEnabled(idle and online and not demo)
        self.device_combo.setEnabled(idle and not demo)
        self.start_button.setEnabled(idle and (demo or (online and bool(self.package_combo.currentText().strip()))))
        self.start_button.setText("开始演示记录" if demo else "开始记录")
        self.stop_button.setEnabled(self.recording and not self._busy)
        if hasattr(self, "pair_button"):
            for widget in (self.pair_endpoint, self.pair_code, self.pair_button, self.connect_endpoint, self.connect_button, self.connection_refresh):
                widget.setEnabled(idle)
        if hasattr(self, "history_table"):
            self.history_view.setEnabled(idle and self.history_table.currentRow() >= 0)
            self.history_refresh.setEnabled(idle)
            self.history_table.setEnabled(idle)
        if len(self.nav_buttons) > 2:
            self.nav_buttons[2].setEnabled(not self.recording)

    def _update_banner(self):
        if self._history_mode:
            return
        demo = self._demo_recording if self.recording else self.demo_check.isChecked()
        self.banner.setObjectName("demoBanner" if demo else "banner")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        if demo:
            self.banner.setText("演示模式  ·  当前使用模拟数据，仅用于体验软件，不能作为手机性能测试结果。")
        elif self.recording:
            self.banner.setText("正在记录  ·  数据实时写入电脑。测整机放电功率请使用无线连接，并拔掉 USB 数据线。")
        elif not self._online_device():
            self.banner.setText("尚未连接可用手机  ·  请到“连接手机”完成 USB 或无线调试；也可勾选演示模式体验。")
        else:
            self.banner.setText("设备已连接  ·  请在手机进入游戏，点击“识别前台”并开始记录。功耗测试请使用无线连接。")

    def _start(self):
        self._history_mode = False
        options = self.get_options()
        self.set_sampling_interval(options["interval_s"])
        self.start_requested.emit(options)

    def set_sampling_interval(self, interval_s: float):
        for chart_widget in self.charts.values():
            chart_widget.set_sampling_interval(interval_s)

    def get_options(self) -> dict:
        layer = self.layer_combo.currentText().strip()
        if layer == "自动选择游戏图层":
            layer = ""
        return {
            "serial": self.device_combo.currentData() or "",
            "package": self.package_combo.currentText().strip(),
            "layer": layer,
            "interval_s": self.interval_combo.currentData(),
            "title": self.title_edit.text().strip() or "游戏测试",
            "duration_s": self.duration_spin.value() * 60,
            "demo": self.demo_check.isChecked(),
        }
    def _pair(self):
        endpoint, code = self.pair_endpoint.text().strip(), self.pair_code.text().strip()
        if not endpoint or len(code) != 6 or not code.isdigit():
            self.show_error("请填写手机配对页面显示的 IP:端口，以及 6 位数字配对码。")
            return
        self.pair_requested.emit(endpoint, code)

    def _connect(self):
        endpoint = self.connect_endpoint.text().strip()
        if not endpoint:
            self.show_error("请填写无线调试主页面显示的连接地址（IP:端口）。")
            return
        self.connect_requested.emit(endpoint)

    def _open_history(self):
        row = self.history_table.currentRow()
        if self.recording or self._busy or row < 0 or row >= len(self._history_rows):
            return
        path = self._history_rows[row].get("path", "")
        if path:
            self.session_open_requested.emit(str(path))

    def set_devices(self, devices: list[Device]):
        previous = self.device_combo.currentData()
        self._device_list = list(devices)
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        if not devices:
            self.device_combo.addItem("未发现手机", "")
        for device in devices:
            state = {"device": "已连接", "unauthorized": "待授权", "offline": "离线"}.get(device.state, device.state)
            self.device_combo.addItem(f"{device.model} · {device.transport} · {state} · {device.serial}", device.serial)
        selected = self.device_combo.findData(previous)
        if selected < 0:
            selected = next((i for i, d in enumerate(devices) if d.state == "device"), 0)
        self.device_combo.setCurrentIndex(selected)
        self.device_combo.blockSignals(False)
        self.devices_note.setText("\n".join(f"{d.model} · {d.serial} · {d.transport} · {d.state}" for d in devices) or "尚未发现设备；检查数据线、USB 调试与手机授权。")
        self._update_enabled()
        self._update_banner()
        self.device_changed.emit(self.device_combo.currentData() or "")

    def set_packages(self, packages: list[str]):
        previous = self.package_combo.currentText()
        self.package_combo.blockSignals(True)
        self.package_combo.clear()
        self.package_combo.addItems(sorted(set(packages)))
        self.package_combo.setCurrentText(previous)
        self.package_combo.blockSignals(False)
        self._update_enabled()

    def set_package(self, package: str):
        self.package_combo.setCurrentText(package)
        self._update_enabled()

    def set_layers(self, layers: list[str]):
        previous = self.layer_combo.currentText()
        self.layer_combo.clear()
        self.layer_combo.addItem("自动选择游戏图层", "")
        self.layer_combo.addItems(layers)
        if previous in layers:
            self.layer_combo.setCurrentText(previous)

    def set_status(self, message: str, level="info"):
        colors = {"info": "#91a9c4", "success": "#65d7bd", "warning": "#e9bf7c", "error": "#f49aab"}
        self.status_label.setText(message)
        self.status_label.setStyleSheet(f"color: {colors.get(level, colors['info'])};")

    def set_busy(self, busy: bool):
        self._busy = busy
        self._update_enabled()
        if busy:
            self.state_badge.setText("处理中…")
        elif self.recording:
            self.state_badge.setText("● 演示记录中" if self._demo_recording else "● 正在记录")
        elif self._history_mode:
            self.state_badge.setText("历史记录")
        else:
            self.state_badge.setText("就绪")

    def set_recording(self, recording: bool, demo=False):
        self.recording = recording
        self._demo_recording = bool(demo) if recording else self._demo_recording
        if recording:
            self.demo_check.setChecked(bool(demo))
            self.set_sampling_interval(self.interval_combo.currentData())
            self._history_mode = False
            self._navigate(0)
        self.state_badge.setText(("● 演示记录中" if demo else "● 正在记录") if recording else "已停止")
        self._update_enabled()
        self._update_banner()

    def add_sample(self, sample: Sample):
        self._sample_count += 1
        self.elapsed_label.setText(elapsed_label(sample.elapsed_s))
        self.samples_label.setText(f"{self._sample_count:,} 个采样点")
        for key, label in self.metric_labels.items():
            label.setText(number(getattr(sample, key, None), 0 if key in ("battery_pct", "app_mem_mb") else (2 if key == "power_w" else 1)))
        for key, chart_widget in self.charts.items():
            chart_widget.add_point(sample.elapsed_s, getattr(sample, key, None))
        self._set_sample_details(sample)

    def _set_sample_details(self, sample: Sample):
        self.detail_label.setText(f"帧耗时 {number(sample.frame_time_ms)} ms    P95 {number(sample.frame_p95_ms)} ms    本轮慢帧 {number(sample.slow_frames, 0)}    原始电流 {number(sample.current_ma, 0)} mA    电压 {number(sample.voltage_v, 3)} V")
        self.source_label.setText(f"帧率来源：{sample.fps_source}  ·  功耗来源：{sample.power_source}" + (f"\n图层：{sample.layer}" if sample.layer else ""))
        self.notes_label.setText(" · ".join(sample.notes) if sample.notes else "采样数据已写入电脑；缺失项显示 —。")

    def reset_charts(self):
        self._history_mode = False
        self._sample_count = 0
        for chart_widget in self.charts.values():
            chart_widget.clear()
        for label in self.metric_labels.values():
            label.setText("—")
        self.elapsed_label.setText("00:00")
        self.samples_label.setText("0 个采样点")
        self.detail_label.setText("帧耗时 — ms    P95 — ms    慢帧 —    电流 — mA    电压 — V")
        self.source_label.setText("等待采集来源信息…")
        self.notes_label.setText("等待第一个采样点…")
        self.summary_label.hide()
        self._update_banner()

    def set_summary(self, summary: dict):
        demo = "演示数据 · " if summary.get("demo") else ""
        duration = summary.get("duration_s", 0) or 0
        fps = summary.get("avg_fps")
        power = summary.get("avg_power_w")
        self.summary_label.setText(f"{demo}测试汇总  ·  时长 {elapsed_label(duration)}  ·  平均 FPS {number(fps)}  ·  平均功耗 {number(power, 2)} W  ·  {summary.get('sample_count', self._sample_count)} 个采样点\n平均值仅统计有效数据；完整覆盖说明见报告。")
        self.summary_label.show()

    def set_history(self, sessions: list[dict]):
        self._history_rows = list(sessions)
        self.history_table.setRowCount(len(sessions))
        statuses = {"completed": "已完成", "stopped": "已停止", "interrupted": "中断", "error": "异常", "recording": "未结束"}
        for row, session in enumerate(sessions):
            values = [
                ("[演示] " if session.get("demo") else "") + str(session.get("title", "未命名测试")),
                str(session.get("started_at", ""))[:19].replace("T", " "),
                elapsed_label(session.get("duration_s", 0) or 0),
                number(session.get("avg_fps")), number(session.get("avg_power_w"), 2),
                str(session.get("sample_count", 0)), statuses.get(session.get("status", ""), session.get("status", "")),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(str(session.get("path", "")))
                if column > 1:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if session.get("demo") and column == 0:
                    item.setForeground(QColor("#efc57f"))
                self.history_table.setItem(row, column, item)
        self.history_note.setText(f"共 {len(sessions)} 条记录  ·  双击记录可查看曲线。" if sessions else "还没有测试记录。开始一次真实测试或演示后，记录会自动保存在本机。")
        self._update_enabled()

    def show_session(self, summary: dict, samples: list[Sample]):
        self.reset_charts()
        self._history_mode = True
        interval_s = summary.get("interval_s")
        if interval_s is None:
            intervals = [later.elapsed_s - earlier.elapsed_s for earlier, later in zip(samples, samples[1:]) if later.elapsed_s > earlier.elapsed_s]
            interval_s = median(intervals) if intervals else 0.5
        self.set_sampling_interval(interval_s)
        self._sample_count = len(samples)
        self.samples_label.setText(f"{len(samples):,} 个采样点")
        for key, chart_widget in self.charts.items():
            chart_widget.set_points([(sample.elapsed_s, getattr(sample, key, None)) for sample in samples], full_history=True)
        if samples:
            last = samples[-1]
            self.elapsed_label.setText(elapsed_label(last.elapsed_s))
            for key, label in self.metric_labels.items():
                label.setText(number(getattr(last, key, None), 2 if key == "power_w" else 1))
            self._set_sample_details(last)
        self.banner.setObjectName("demoBanner" if summary.get("demo") else "banner")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        demo = "【演示数据】" if summary.get("demo") else ""
        self.banner.setText(f"历史回放 {demo}  ·  {summary.get('title', '测试记录')}  ·  {str(summary.get('started_at', ''))[:19].replace('T', ' ')}\n曲线显示完整记录，指标卡为最后一次采样；下方汇总为有效数据统计。")
        self.state_badge.setText("历史记录 · 演示" if summary.get("demo") else "历史记录")
        self.set_summary(summary)
        self._navigate(0)
        self.set_status("正在查看已保存记录；开始新的测试会清空当前曲线，已有文件会保留。")

    def show_error(self, message: str):
        self.set_status(message, "error")
        QMessageBox.warning(self, "PhoneTrace · 操作提示", message)

    def closeEvent(self, event):
        if self.recording:
            event.ignore()
            self.close_requested.emit()
        else:
            event.accept()
