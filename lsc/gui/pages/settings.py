"""Settings page with QSettings persistence."""
from __future__ import annotations

import os

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from lsc.gui.components.widgets import Card, ChipGroup, InputField
from lsc.gui.theme import connect_theme_changed, is_dark, set_dark


def _section_title(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("section_title")
    return lbl


def _label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("label_secondary")
    return lbl


def _hint(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("label_tertiary")
    return lbl


class SettingsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = QSettings("LSC", "LiveStreamClipper")
        self._loading = False
        self._last_applied_theme = "深色" if is_dark() else "浅色"
        self._build()
        self._load()
        self._connect_persistence()
        connect_theme_changed(self._on_external_theme_change)

    def _build(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        inner = QWidget()
        main = QVBoxLayout(inner)
        main.setContentsMargins(24, 24, 24, 24)
        main.setSpacing(24)
        main.setAlignment(Qt.AlignTop)

        title = QLabel("设置")
        title.setObjectName("page_title")
        main.addWidget(title)

        subtitle = QLabel("管理输出目录、录制参数与应用主题")
        subtitle.setObjectName("label_secondary")
        main.addWidget(subtitle)

        g = Card()
        g.add_widget(_section_title("通用设置"))
        g.layout.addSpacing(4)

        g.add_widget(_label("默认输出目录"))
        default_output = os.path.join(os.path.expanduser("~"), "LSC", "output")
        out_lay = QHBoxLayout()
        out_lay.setSpacing(8)
        self._output_dir = InputField(default_output)
        out_lay.addWidget(self._output_dir)
        self._browse_btn = QPushButton("浏览")
        self._browse_btn.setObjectName("btnPrimary")
        self._browse_btn.setFixedHeight(36)
        self._browse_btn.setFixedWidth(72)
        self._browse_btn.clicked.connect(self._on_browse_output)
        out_lay.addWidget(self._browse_btn)
        g.add_layout(out_lay)

        g.add_widget(_label("主题"))
        self._theme = ChipGroup(["跟随系统", "深色", "浅色"])
        self._theme.selection_changed.connect(self._on_theme_changed)
        g.add_widget(self._theme)
        main.addWidget(g)

        r = Card()
        r.add_widget(_section_title("录制设置"))
        r.layout.addSpacing(4)

        r.add_widget(_label("默认编码器"))
        self._encoder = ChipGroup(["H.264 NVENC", "H.264 CPU", "Copy"])
        self._encoder.selection_changed.connect(lambda v: self._save("encoder", v))
        r.add_widget(self._encoder)

        r.add_widget(_label("默认画质"))
        self._quality = ChipGroup(["原画", "高清", "流畅"])
        self._quality.selection_changed.connect(lambda v: self._save("quality", v))
        r.add_widget(self._quality)
        main.addWidget(r)

        enc = Card()
        enc.add_widget(_section_title("编码参数设置"))
        enc.layout.addSpacing(4)

        enc.add_widget(_label("默认编码参数模式"))
        self._param_mode = ChipGroup(["CRF 质量", "码率限制", "不限制"])
        self._param_mode.selection_changed.connect(lambda v: self._save("param_mode", v))
        enc.add_widget(self._param_mode)

        enc.add_widget(_label("默认 CRF 值"))
        crf_row = QHBoxLayout()
        crf_row.setSpacing(8)
        self._crf = InputField("23")
        self._crf.setFixedWidth(60)
        crf_row.addWidget(self._crf)
        crf_row.addWidget(_hint("范围 18–28，越小画质越高"))
        crf_row.addStretch()
        enc.add_layout(crf_row)

        enc.add_widget(_label("默认码率"))
        bitrate_row = QHBoxLayout()
        bitrate_row.setSpacing(8)
        self._bitrate_value = InputField("8000")
        bitrate_row.addWidget(self._bitrate_value)
        self._bitrate_unit = InputField("kbps")
        self._bitrate_unit.setFixedWidth(90)
        bitrate_row.addWidget(self._bitrate_unit)
        bitrate_row.addStretch()
        enc.add_layout(bitrate_row)
        main.addWidget(enc)

        main.addStretch()
        scroll.setWidget(inner)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def _save(self, key: str, value: str):
        self._settings.setValue(key, value)

    def _load(self):
        """Load saved values from QSettings and persist defaults.

        The _loading flag suppresses theme refresh side-effects while
        restoring the theme chip selection (so merely opening the
        settings page does not trigger a full style rebuild).
        """
        self._loading = True
        try:
            default_output = os.path.join(os.path.expanduser("~"), "LSC", "output")
            output_dir = self._settings.value("output_dir", default_output)
            self._output_dir.set_text(str(output_dir))
            self._save("output_dir", output_dir)

            crf = self._settings.value("crf", "23")
            self._crf.set_text(str(crf))
            self._save("crf", crf)

            bitrate_value = self._settings.value("bitrate_value", "8000")
            self._bitrate_value.set_text(str(bitrate_value))
            self._save("bitrate_value", bitrate_value)

            bitrate_unit = self._settings.value("bitrate_unit", "kbps")
            self._bitrate_unit.set_text(str(bitrate_unit))
            self._save("bitrate_unit", bitrate_unit)

            encoder = self._settings.value("encoder", "H.264 NVENC")
            if encoder in self._encoder._items:
                self._encoder.set_selected(encoder)

            quality = self._settings.value("quality", "原画")
            if quality in self._quality._items:
                self._quality.set_selected(quality)

            param_mode = self._settings.value("param_mode", "CRF 质量")
            if param_mode in self._param_mode._items:
                self._param_mode.set_selected(param_mode)

            theme_val = self._settings.value("theme", "深色")
            if theme_val in ["跟随系统", "深色", "浅色"]:
                self._theme.set_selected(theme_val)
            self._save("theme", theme_val)
            self._last_applied_theme = "深色" if is_dark() else "浅色"
        finally:
            self._loading = False

    def _on_browse_output(self):
        d = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if d:
            self._output_dir.set_text(d)
            self._save("output_dir", d)

    def _connect_persistence(self):
        """Connect input fields' change signals to QSettings persistence.

        Connected after _load() so the initial set_text() calls during load
        do not trigger spurious writes (defaults are persisted explicitly
        in _load instead).
        """
        self._output_dir.text_changed.connect(lambda v: self._save("output_dir", v))
        self._crf.text_changed.connect(lambda v: self._save("crf", v))
        self._bitrate_value.text_changed.connect(lambda v: self._save("bitrate_value", v))
        self._bitrate_unit.text_changed.connect(lambda v: self._save("bitrate_unit", v))

    def _on_theme_changed(self, value: str):
        self._save("theme", value)
        if self._loading:
            return
        if value == "深色":
            set_dark(True)
            self._last_applied_theme = "深色"
        elif value == "浅色":
            set_dark(False)
            self._last_applied_theme = "浅色"
        else:  # 跟随系统
            is_system_dark = self._system_prefers_dark()
            set_dark(is_system_dark)
            self._last_applied_theme = "深色" if is_system_dark else "浅色"

    def _on_external_theme_change(self) -> None:
        """当通过侧栏等其他入口切换主题时，同步设置页的 Chip 状态。"""
        current = "深色" if is_dark() else "浅色"
        if current == self._last_applied_theme:
            return
        self._last_applied_theme = current
        saved = self._settings.value("theme", current)
        # 若用户选择了"跟随系统"，则保留该选项，仅同步实际明暗状态；
        # 否则将 Chip 更新为当前实际主题并持久化。
        if saved != "跟随系统":
            self._theme.set_selected(current)
            self._save("theme", current)

    @staticmethod
    def _system_prefers_dark() -> bool:
        """Detect whether the OS prefers a dark color scheme."""
        try:
            from PySide6.QtGui import QGuiApplication

            hints = QGuiApplication.styleHints()
            return hints.colorScheme() == Qt.ColorScheme.Dark
        except Exception:
            return True

    @staticmethod
    def _is_system_dark() -> bool:
        """Alias for _system_prefers_dark (used by startup theme apply)."""
        return SettingsPage._system_prefers_dark()
