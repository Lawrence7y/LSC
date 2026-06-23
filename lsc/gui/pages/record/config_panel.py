"""Configuration panel for the record page."""
from __future__ import annotations

import os

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from lsc.gui.components.widgets import Card, ChipGroup, InputField, ParamPanel


def _label(text, style="secondary", size=12):
    """Create a themed label with objectName-based styling."""
    lbl = QLabel(text)
    lbl.setObjectName(f"label_{style}")
    lbl.setProperty("fontSize", size)
    return lbl


class ConfigPanel(QWidget):
    connect_requested = Signal(str)
    start_record_requested = Signal()
    analyze_requested = Signal()
    export_analysis_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = QSettings("LSC", "LiveStreamClipper")
        self._build()

    def _build(self):
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(16)

        card = Card()

        title = QLabel("录制配置")
        title.setObjectName("card_title")
        card.add_widget(title)

        # URL
        card.add_widget(_label("直播间链接"))
        url_lay = QHBoxLayout()
        self._url = InputField("https://live.douyin.com/xxx")
        url_lay.addWidget(self._url)
        self._connect_btn = QPushButton("连接")
        self._connect_btn.setFixedHeight(36)
        self._connect_btn.setObjectName("btnPrimary")
        self._connect_btn.clicked.connect(lambda: self.connect_requested.emit(self._url.text()))
        url_lay.addWidget(self._connect_btn)
        card.add_layout(url_lay)

        # Output dir
        card.add_widget(_label("输出目录"))
        default_output = self._settings.value("output_dir", os.path.join(os.path.expanduser("~"), "LSC", "recordings"))
        out_lay = QHBoxLayout()
        self._output = InputField(default_output)
        self._output.set_text(str(default_output))
        out_lay.addWidget(self._output)
        self._browse_btn = QPushButton("浏览")
        self._browse_btn.setFixedHeight(36)
        self._browse_btn.setObjectName("btnPrimary")
        self._browse_btn.clicked.connect(self._on_browse)
        out_lay.addWidget(self._browse_btn)
        card.add_layout(out_lay)

        # Quality
        card.add_widget(_label("画质预设"))
        self._quality = ChipGroup(["原画", "高清", "流畅"])
        card.add_widget(self._quality)

        # Encoder
        card.add_widget(_label("编码器"))
        self._encoder = ChipGroup(["H.264 NVENC", "H.264 CPU", "Copy"])
        self._encoder.selection_changed.connect(self._on_encoder_changed)
        card.add_widget(self._encoder)

        # Param mode
        self._param_label = _label("编码参数")
        card.add_widget(self._param_label)
        self._param = ChipGroup(["CRF 质量", "码率限制", "不限制"])
        self._param.selection_changed.connect(self._on_param_mode)
        card.add_widget(self._param)

        # Param panel
        self._param_panel = ParamPanel()
        card.add_widget(self._param_panel)

        # Start button
        self._start_btn = QPushButton("开始录制")
        self._start_btn.setFixedHeight(36)
        self._start_btn.setObjectName("btnPrimary")
        self._start_btn.clicked.connect(self.start_record_requested.emit)
        card.add_widget(self._start_btn)

        self._analyze_btn = QPushButton("分析当前录制")
        self._analyze_btn.setFixedHeight(36)
        self._analyze_btn.setObjectName("btnPrimary")
        self._analyze_btn.setEnabled(False)
        self._analyze_btn.clicked.connect(self.analyze_requested.emit)
        card.add_widget(self._analyze_btn)

        self._export_analysis_btn = QPushButton("导出分析高光")
        self._export_analysis_btn.setFixedHeight(36)
        self._export_analysis_btn.setObjectName("btnPrimary")
        self._export_analysis_btn.setEnabled(False)
        self._export_analysis_btn.clicked.connect(self.export_analysis_requested.emit)
        card.add_widget(self._export_analysis_btn)

        # Analysis profile
        card.add_widget(_label("分析 Profile"))
        self._analysis_profile = ChipGroup(["valorant", "fps", "generic"])
        card.add_widget(self._analysis_profile)

        self._layout.addWidget(card)

        # Info card
        info_card = Card()
        info_title = QLabel("录制信息")
        info_title.setObjectName("card_title")
        info_card.add_widget(info_title)

        self._info_grid = QWidget()
        gl = QGridLayout(self._info_grid)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setSpacing(14)

        self._info_values = {}
        for i, (label_text, key) in enumerate([
            ("分辨率", "res"), ("帧率", "fps"), ("编码", "codec"),
            ("编码参数", "bitrate"), ("文件大小", "size"), ("输出路径", "path"),
            ("分析结果", "analysis"), ("结果文件", "analysis_path"),
        ]):
            col = i % 2
            row = i // 2
            item = QWidget()
            il = QVBoxLayout(item)
            il.setContentsMargins(0, 0, 0, 0)
            il.setSpacing(4)
            lbl = QLabel(label_text)
            lbl.setObjectName("info_label")
            il.addWidget(lbl)
            val = QLabel("--")
            val.setObjectName("info_value")
            val.setWordWrap(True)  # Long paths should wrap, not expand the panel
            val.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            il.addWidget(val)
            self._info_values[key] = val
            gl.addWidget(item, row, col)

        info_card.add_widget(self._info_grid)
        self._layout.addWidget(info_card)
        self._layout.addStretch()

        # Initialize param panel state to match default encoder selection.
        # ChipGroup does NOT emit selection_changed on construction,
        # so we must call the handlers explicitly.
        self._load_saved_defaults()
        self._on_encoder_changed(self._encoder.selected)
        self._on_param_mode(self._param.selected)

    def _load_saved_defaults(self):
        encoder = self._settings.value("encoder", self._encoder.selected)
        if encoder in self._encoder._items:
            self._encoder._click(encoder)

        quality = self._settings.value("quality", self._quality.selected)
        quality_map = {
            "原画": "原画",
            "高清": "高清",
            "高清 1080p": "高清",
            "高清 720p": "高清",
            "标清": "流畅",
            "流畅": "流畅",
        }
        quality = quality_map.get(quality, quality)
        if quality in self._quality._items:
            self._quality._click(quality)

        param_mode = self._settings.value("param_mode", self._param.selected)
        if param_mode in self._param._items:
            self._param._click(param_mode)

        self._param_panel.set_crf_value(self._settings.value("crf", "23"))
        self._param_panel.set_bitrate_value(self._settings.value("bitrate_value", "8000"))
        self._param_panel.set_bitrate_unit(self._settings.value("bitrate_unit", "kbps"))

    def _on_encoder_changed(self, text):
        """When encoder changes, update param panel availability.

        Copy mode: no encoding params needed (stream saved as-is).
        NVENC: CRF maps to -cq, CBR maps to -b:v.
        CPU: all param modes fully supported.
        """
        is_copy = (text == "Copy")
        self._param.setEnabled(not is_copy)
        self._param_panel.setEnabled(not is_copy)
        self._param_label.setEnabled(not is_copy)
        if is_copy:
            # Dim the labels to indicate they're inactive
            self._param_label.setStyleSheet("opacity:0.4;")
        else:
            self._param_label.setStyleSheet("")

    def _on_param_mode(self, text):
        mode_map = {"CRF 质量": 0, "码率限制": 1, "不限制": 2}
        self._param_panel.set_mode(mode_map.get(text, 0))

    def _on_browse(self):
        path = QFileDialog.getExistingDirectory(self, "选择输出目录", self._output.text())
        if path:
            self._output.set_text(path)

    def set_connected(self, connected):
        if connected:
            self._connect_btn.setText("已连接")
            self._connect_btn.setEnabled(False)
        else:
            self._connect_btn.setText("连接")
            self._connect_btn.setEnabled(True)

    def set_connecting(self):
        self._connect_btn.setText("连接中...")
        self._connect_btn.setEnabled(False)

    def set_recording(self, recording):
        if recording:
            self._start_btn.setText("停止录制")
            self._start_btn.setObjectName("btnStopRecording")
            self._start_btn.setStyleSheet("")  # Let theme handle it
            self.set_analyze_enabled(False)
        else:
            self._start_btn.setText("开始录制")
            self._start_btn.setObjectName("btnPrimary")
            self._start_btn.setStyleSheet("")

    def set_info(self, key, value):
        if key in self._info_values:
            self._info_values[key].setText(value)

    # ── Public accessors (replace direct private-member access) ──

    @property
    def output_path(self) -> str:
        """Current output directory text."""
        return self._output.text()

    def set_analyze_enabled(self, enabled: bool):
        self._analyze_btn.setEnabled(enabled)

    def set_export_analysis_enabled(self, enabled: bool):
        self._export_analysis_btn.setEnabled(enabled)

    @property
    def quality_selection(self) -> str:
        """Currently selected quality preset text."""
        return self._quality.selected

    @property
    def encoder_selection(self) -> str:
        """Currently selected encoder chip text."""
        return self._encoder.selected

    @property
    def param_mode_selection(self) -> str:
        """Currently selected encoding parameter mode text."""
        return self._param.selected

    @property
    def crf_value(self) -> int:
        """Current CRF value from param panel."""
        return self._param_panel.crf_value()

    @property
    def bitrate_value(self) -> str:
        """Current bitrate text from param panel."""
        return self._param_panel.bitrate_value()

    @property
    def bitrate_unit(self) -> str:
        """Current bitrate unit from param panel."""
        return self._param_panel.bitrate_unit()

    @property
    def analysis_profile(self) -> str:
        """Currently selected analysis profile."""
        return self._analysis_profile.selected
