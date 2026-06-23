"""对话框组件 — 导出确认等模态对话框。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QSettings
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from lsc.config import ExportProfile
from lsc.gui.components.clip_list import ClipSegment
from lsc.gui.theme import get_theme
from lsc.utils.helpers import fmt_time


# 编码器选项：(显示名, codec 值)
_ENCODER_OPTIONS = [
    ("libx264（软件编码，兼容性最好）", "libx264"),
    ("h264_nvenc（NVIDIA 硬件加速）", "h264_nvenc"),
    ("h264_qsv（Intel QuickSync）", "h264_qsv"),
    ("h264_amf（AMD AMF）", "h264_amf"),
    ("copy（直接拷贝，最快，切口不精确）", "copy"),
]

_PRESET_OPTIONS = [
    "ultrafast", "superfast", "veryfast", "faster",
    "fast", "medium", "slow", "slower", "veryslow",
]

_RATE_MODE_OPTIONS = [
    ("CRF（恒定质量，推荐）", "crf"),
    ("码率限制", "bitrate"),
    ("不限制", "unrestricted"),
]


class ExportConfirmDialog(QDialog):
    """导出确认对话框。

    在导出前展示片段列表、涉及房间、输出文件数和总时长，
    并允许用户配置编码参数（编码器、质量模式、CRF/码率、分辨率等），
    确认后通过 :meth:`get_profile` 获取配置。

    Usage::

        dlg = ExportConfirmDialog(segments, room_names, parent=self)
        if dlg.exec() == QDialog.Accepted:
            profile = dlg.get_profile()
            do_export(profile)
    """

    def __init__(
        self,
        segments: list[ClipSegment],
        room_names: list[str],
        parent=None,
        default_profile: ExportProfile | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("确认导出")
        self.setModal(True)
        self.setMinimumWidth(480)
        self._segments = list(segments)
        self._room_names = list(room_names)
        self._default_profile = default_profile or self._load_saved_profile()
        self._build()
        self._apply_style()
        self._sync_quality_controls()

    def _build(self) -> None:
        self.setObjectName("exportConfirmDialog")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)

        c = get_theme()

        # ── 标题 ──
        title = QLabel("确认导出")
        title.setStyleSheet(f"font-size:16px;font-weight:600;color:{c.text_primary};")
        layout.addWidget(title)

        # ── 摘要 ──
        total_duration = sum(s.duration for s in self._segments)
        total_files = len(self._segments) * len(self._room_names)
        summary = QLabel(
            f"将导出 <b>{len(self._segments)}</b> 个片段 × "
            f"<b>{len(self._room_names)}</b> 个房间 = "
            f"<b>{total_files}</b> 个文件，总时长 <b>{fmt_time(total_duration)}</b>"
        )
        summary.setWordWrap(True)
        summary.setStyleSheet(f"font-size:13px;color:{c.text_secondary};")
        layout.addWidget(summary)

        # ── 片段列表 ──
        if self._segments:
            seg_label = QLabel("片段列表：")
            seg_label.setStyleSheet(f"font-size:12px;font-weight:600;color:{c.text_primary};")
            layout.addWidget(seg_label)

            seg_box = QWidget()
            seg_layout = QVBoxLayout(seg_box)
            seg_layout.setContentsMargins(0, 0, 0, 0)
            seg_layout.setSpacing(4)
            for i, seg in enumerate(self._segments):
                row = QLabel(
                    f"  #{i + 1}  {fmt_time(seg.start)} → {fmt_time(seg.end)}  "
                    f"<span style='color:{c.text_tertiary}'>({fmt_time(seg.duration)})</span>"
                )
                row.setStyleSheet(
                    f"font-family:'JetBrains Mono',monospace;font-size:11px;"
                    f"color:{c.text_secondary};"
                )
                seg_layout.addWidget(row)
            layout.addWidget(seg_box)

        # ── 房间列表 ──
        if self._room_names:
            room_label = QLabel("涉及房间：")
            room_label.setStyleSheet(f"font-size:12px;font-weight:600;color:{c.text_primary};")
            layout.addWidget(room_label)
            room_text = "、".join(self._room_names)
            room_row = QLabel(f"  {room_text}")
            room_row.setWordWrap(True)
            room_row.setStyleSheet(f"font-size:12px;color:{c.text_secondary};")
            layout.addWidget(room_row)

        # ── 编码参数 ──
        enc_label = QLabel("编码参数：")
        enc_label.setStyleSheet(f"font-size:12px;font-weight:600;color:{c.text_primary};")
        layout.addWidget(enc_label)

        enc_box = self._build_encoding_panel()
        layout.addWidget(enc_box)

        layout.addStretch()

        # ── 按钮区 ──
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.setFixedHeight(36)
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        confirm_btn = QPushButton("确认导出")
        confirm_btn.setObjectName("btnPrimary")
        confirm_btn.setFixedHeight(36)
        confirm_btn.setCursor(Qt.PointingHandCursor)
        confirm_btn.clicked.connect(self.accept)
        btn_row.addWidget(confirm_btn)

        layout.addLayout(btn_row)

    def _build_encoding_panel(self) -> QWidget:
        """构建编码参数配置面板。"""
        box = QWidget()
        box.setObjectName("encodingBox")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(14, 12, 14, 12)
        bl.setSpacing(10)

        c = get_theme()

        # 编码器选择
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        lbl1 = QLabel("编码器：")
        lbl1.setFixedWidth(70)
        lbl1.setStyleSheet(f"font-size:12px;color:{c.text_secondary};")
        row1.addWidget(lbl1)
        self._codec_combo = QComboBox()
        for display, value in _ENCODER_OPTIONS:
            self._codec_combo.addItem(display, value)
        # 设置默认值
        idx = self._codec_combo.findData(self._default_profile.codec)
        if idx >= 0:
            self._codec_combo.setCurrentIndex(idx)
        self._codec_combo.currentIndexChanged.connect(self._sync_quality_controls)
        row1.addWidget(self._codec_combo, 1)
        bl.addLayout(row1)

        # 质量模式
        row2 = QHBoxLayout()
        row2.setSpacing(8)
        lbl2 = QLabel("质量模式：")
        lbl2.setFixedWidth(70)
        lbl2.setStyleSheet(f"font-size:12px;color:{c.text_secondary};")
        row2.addWidget(lbl2)
        self._rate_combo = QComboBox()
        for display, value in _RATE_MODE_OPTIONS:
            self._rate_combo.addItem(display, value)
        idx = self._rate_combo.findData(self._default_profile.rate_mode)
        if idx >= 0:
            self._rate_combo.setCurrentIndex(idx)
        self._rate_combo.currentIndexChanged.connect(self._sync_quality_controls)
        row2.addWidget(self._rate_combo, 1)
        bl.addLayout(row2)

        # CRF 滑块
        row3 = QHBoxLayout()
        row3.setSpacing(8)
        self._crf_label = QLabel("CRF: 23")
        self._crf_label.setFixedWidth(70)
        self._crf_label.setStyleSheet(f"font-size:12px;color:{c.text_secondary};")
        row3.addWidget(self._crf_label)
        self._crf_slider = QSlider(Qt.Horizontal)
        self._crf_slider.setRange(0, 51)
        self._crf_slider.setValue(self._default_profile.crf)
        self._crf_slider.valueChanged.connect(
            lambda v: self._crf_label.setText(f"CRF: {v}")
        )
        row3.addWidget(self._crf_slider, 1)
        bl.addLayout(row3)

        # 码率输入
        row4 = QHBoxLayout()
        row4.setSpacing(8)
        self._bitrate_label = QLabel("视频码率：")
        self._bitrate_label.setFixedWidth(70)
        self._bitrate_label.setStyleSheet(f"font-size:12px;color:{c.text_secondary};")
        row4.addWidget(self._bitrate_label)
        self._bitrate_input = QLineEdit(self._default_profile.video_bitrate)
        row4.addWidget(self._bitrate_input, 1)
        bl.addLayout(row4)

        # 分辨率
        row5 = QHBoxLayout()
        row5.setSpacing(8)
        lbl5 = QLabel("分辨率：")
        lbl5.setFixedWidth(70)
        lbl5.setStyleSheet(f"font-size:12px;color:{c.text_secondary};")
        row5.addWidget(lbl5)
        self._resolution_input = QLineEdit(self._default_profile.resolution)
        self._resolution_input.setPlaceholderText("留空=不缩放，如 1920x1080")
        row5.addWidget(self._resolution_input, 1)
        bl.addLayout(row5)

        # preset
        row6 = QHBoxLayout()
        row6.setSpacing(8)
        lbl6 = QLabel("预设：")
        lbl6.setFixedWidth(70)
        lbl6.setStyleSheet(f"font-size:12px;color:{c.text_secondary};")
        row6.addWidget(lbl6)
        self._preset_combo = QComboBox()
        for p in _PRESET_OPTIONS:
            self._preset_combo.addItem(p)
        idx = self._preset_combo.findText(self._default_profile.preset)
        if idx >= 0:
            self._preset_combo.setCurrentIndex(idx)
        row6.addWidget(self._preset_combo, 1)
        bl.addLayout(row6)

        return box

    def _sync_quality_controls(self) -> None:
        """根据编码器和质量模式启用/禁用相关控件。"""
        codec = self._codec_combo.currentData() or "libx264"
        rate_mode = self._rate_combo.currentData() or "crf"
        is_copy = codec == "copy"

        # copy 模式下禁用所有质量控件
        self._rate_combo.setEnabled(not is_copy)
        self._crf_label.setEnabled(not is_copy)
        self._crf_slider.setEnabled(not is_copy and rate_mode == "crf")
        self._bitrate_label.setEnabled(not is_copy)
        self._bitrate_input.setEnabled(not is_copy and rate_mode == "bitrate")
        self._preset_combo.setEnabled(not is_copy)
        # 分辨率在 copy 模式下也禁用（需要重编码）
        self._resolution_input.setEnabled(not is_copy)

    def get_profile(self) -> ExportProfile:
        """返回用户配置的 ExportProfile。"""
        codec = self._codec_combo.currentData() or "libx264"
        rate_mode = self._rate_combo.currentData() or "crf"
        preset = self._preset_combo.currentText() or "medium"
        crf = self._crf_slider.value()
        bitrate = self._bitrate_input.text().strip() or "8000k"
        resolution = self._resolution_input.text().strip()

        # Persist user choices for next time
        self._save_profile(codec, rate_mode, preset, crf, bitrate, resolution)

        # copy 模式忽略质量参数
        if codec == "copy":
            return ExportProfile(
                codec="copy",
                crf=self._default_profile.crf,
                preset=preset,
                audio_bitrate=self._default_profile.audio_bitrate,
                vertical_crop=self._default_profile.vertical_crop,
            )

        return ExportProfile(
            codec=codec,
            crf=crf,
            preset=preset,
            audio_bitrate=self._default_profile.audio_bitrate,
            vertical_crop=self._default_profile.vertical_crop,
            rate_mode=rate_mode,
            video_bitrate=bitrate,
            resolution=resolution,
            fps=self._default_profile.fps,
        )

    @staticmethod
    def _load_saved_profile() -> ExportProfile:
        """Load the last-used export profile from QSettings."""
        s = QSettings("LSC", "LiveStreamClipper")
        return ExportProfile(
            codec=str(s.value("export/codec", "libx264")),
            crf=int(s.value("export/crf", 23)),
            preset=str(s.value("export/preset", "medium")),
            rate_mode=str(s.value("export/rate_mode", "crf")),
            video_bitrate=str(s.value("export/video_bitrate", "8000k")),
            resolution=str(s.value("export/resolution", "")),
        )

    @staticmethod
    def _save_profile(codec: str, rate_mode: str, preset: str,
                      crf: int, bitrate: str, resolution: str) -> None:
        """Persist export profile choices to QSettings."""
        s = QSettings("LSC", "LiveStreamClipper")
        s.setValue("export/codec", codec)
        s.setValue("export/rate_mode", rate_mode)
        s.setValue("export/preset", preset)
        s.setValue("export/crf", crf)
        s.setValue("export/video_bitrate", bitrate)
        s.setValue("export/resolution", resolution)

    def _apply_style(self) -> None:
        # 样式已迁移到 theme.py generate_stylesheet，使用 objectName 驱动
        pass
