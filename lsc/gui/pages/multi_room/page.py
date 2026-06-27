"""多房间工作台页面 — 包含卡片网格、详情面板、控制栏和状态栏。"""
from __future__ import annotations

import logging
import os
import threading

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from lsc.gui.components.clip_list import ClipListWidget, ClipSegment
from lsc.gui.components.dialogs import ExportConfirmDialog
from lsc.gui.components.flow_layout import FlowLayout
from lsc.gui.components.fullscreen_preview import FullscreenPreview
from lsc.gui.components.room_card import RoomCard
from lsc.gui.components.widgets import Card, ChipGroup, InputField, PageHeader, ParamPanel
from lsc.gui.multi_room.manager import MAX_ROOMS, MultiRoomManager
from lsc.gui.multi_room.session import RoomSession
from lsc.gui.pages.multi_room.detail_panel import DetailPanel
from lsc.gui.pages.multi_room.status_bar import _BottomBar
from lsc.gui.theme import connect_theme_changed
from lsc.gui.undo import Command, UndoStack
from lsc.utils.helpers import fmt_time, open_in_explorer

_log = logging.getLogger(__name__)

# ── Grid configuration ────────────────────────────────────────
# _CARD_MIN_WIDTH 仅用于「名义列数」计算(方向键导航 & 响应式测试),
# 实际换行由 FlowLayout 按每张卡片自身宽度完成。
_CARD_MAX_WIDTH = 560
_CARD_MIN_WIDTH = 340
_GRID_H_SPACING = 8
_GRID_VMARGIN = 12
_GRID_HMARGIN = 10


class MultiRoomPage(QWidget):
    """多房间工作台页面。"""

    room_selected = Signal(str)

    def __init__(self, manager: MultiRoomManager | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._manager = manager if manager is not None else MultiRoomManager()
        self._cards: dict[str, RoomCard] = {}
        self._selected_room_id: str | None = None
        self._export_lock = threading.Lock()
        # Multi-selection set (Ctrl/Shift click). When more than one room is
        # selected, timeline seek / seek-back / seek-fwd apply to all of them
        # simultaneously so the user can scrub several streams in lockstep.
        self._selected_room_ids: set[str] = set()
        # Fullscreen preview window handle (created on demand).
        self._fullscreen_window = None
        # 导出进度跟踪：{room_id: percent}，用于聚合多房间导出进度
        self._export_progress: dict[str, float] = {}
        self._export_total_tasks: int = 0
        self._export_completed_tasks: int = 0
        # 已完成导出的任务键集合 (room_id, seg_idx)，用于幂等处理 _finish_export_progress
        # 并过滤迟到进度回调，避免重复计数导致进度回退/超 100%
        self._export_completed_rooms: set[tuple] = set()
        self._grid_columns = 1
        self._undo = UndoStack(limit=50)
        # Position cache for throttled timeline updates
        # Only update UI when position changes by more than 0.2 seconds
        self._last_positions: dict[str, float] = {}
        self._POSITION_THRESHOLD = 0.2  # seconds
        self._build_ui()
        self._connect_signals()
        connect_theme_changed(self._refresh_theme)
        self.setMinimumWidth(900)
        # resizeEvent 防抖：避免拖拽窗口边缘时频繁重建网格
        self._grid_debounce = QTimer(self)
        self._grid_debounce.setSingleShot(True)
        self._grid_debounce.setInterval(150)
        self._grid_debounce.timeout.connect(self._update_grid_columns)
        # 直播间区域高度重算防抖：卡片尺寸/数量变化时实时重算容器高度
        self._resize_debounce = QTimer(self)
        self._resize_debounce.setSingleShot(True)
        self._resize_debounce.setInterval(60)
        self._resize_debounce.timeout.connect(self._update_card_container_min_height)
        # 从持久化配置自动加载上次保存的房间列表
        self._manager.load_rooms()
        for room in self._manager.list_rooms():
            self._add_card(room)
        self._refresh()

    def _get_recording_settings(self) -> tuple[str, str, int]:
        """从 QSettings 读取用户配置的录制参数。

        Returns:
            (output_dir, encoder, crf) 元组。
        """
        import os
        settings = QSettings("LSC", "LiveStreamClipper")
        default_output = os.path.join(os.path.expanduser("~"), "LSC", "output")
        output_dir = ""
        output_input = getattr(self, "_output_input", None)
        if output_input is not None:
            output_dir = output_input.text().strip()
        if not output_dir:
            output_dir = str(settings.value("output_dir", default_output))
        settings.setValue("output_dir", output_dir)
        encoder = settings.value("encoder", "H.264 NVENC")
        try:
            crf = int(settings.value("crf", "23"))
        except (ValueError, TypeError):
            crf = 23
        return output_dir, encoder, crf

    def _get_recording_profile(self) -> tuple[str, str, int, str, str, str]:
        """读取并保存多房间录制配置，返回完整 FFmpeg 参数。"""
        settings = QSettings("LSC", "LiveStreamClipper")
        output_dir, fallback_encoder, fallback_crf = self._get_recording_settings()

        quality_widget = getattr(self, "_record_quality", None)
        if quality_widget is not None:
            settings.setValue("quality", quality_widget.selected)

        encoder_widget = getattr(self, "_record_encoder", None)
        encoder = encoder_widget.selected if encoder_widget is not None else fallback_encoder
        settings.setValue("encoder", encoder)

        param_widget = getattr(self, "_record_param", None)
        param_mode = param_widget.selected if param_widget is not None else str(settings.value("param_mode", "CRF 质量"))
        settings.setValue("param_mode", param_mode)

        param_panel = getattr(self, "_record_param_panel", None)
        try:
            crf = int(param_panel.crf_value() if param_panel is not None else fallback_crf)
        except (ValueError, TypeError):
            crf = 23
        bitrate = param_panel.bitrate_value() if param_panel is not None else str(settings.value("bitrate", "8000"))
        bitrate_unit = param_panel.bitrate_unit() if param_panel is not None else str(settings.value("bitrate_unit", "kbps"))
        settings.setValue("crf", crf)
        settings.setValue("bitrate", bitrate)
        settings.setValue("bitrate_unit", bitrate_unit)
        return output_dir, str(encoder), crf, str(param_mode), str(bitrate), str(bitrate_unit)

    def _on_browse_output_dir(self) -> None:
        current_dir, _encoder, _crf = self._get_recording_settings()
        chosen = QFileDialog.getExistingDirectory(self, "选择输出目录", current_dir)
        if not chosen:
            return
        self._output_input.setText(chosen)
        QSettings("LSC", "LiveStreamClipper").setValue("output_dir", chosen)

    def _record_setting_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("field_label")
        return label

    def _build_record_settings_card(self) -> Card:
        card = Card()
        title = QLabel("录制设置")
        title.setObjectName("card_title")
        card.add_widget(title)

        settings = QSettings("LSC", "LiveStreamClipper")

        card.add_widget(self._record_setting_label("画质预设"))
        self._record_quality = ChipGroup(["原画", "高清", "流畅"])
        quality = str(settings.value("quality", self._record_quality.selected))
        if quality in self._record_quality._items:
            self._record_quality._click(quality)
        self._record_quality._buttons["原画"].setToolTip("不重编码，直接拷贝直播流。画质无损，文件较大。")
        self._record_quality._buttons["高清"].setToolTip("重编码为高清分辨率。平衡画质与体积。")
        self._record_quality._buttons["流畅"].setToolTip("低码率编码。适合网络条件差时使用。")
        card.add_widget(self._record_quality)

        card.add_widget(self._record_setting_label("编码器"))
        self._record_encoder = ChipGroup(["H.264 NVENC", "H.264 CPU", "Copy"])
        encoder = str(settings.value("encoder", self._record_encoder.selected))
        if encoder in self._record_encoder._items:
            self._record_encoder._click(encoder)
        self._record_encoder._buttons["H.264 NVENC"].setToolTip("NVIDIA GPU 硬编码。需要 NVIDIA 显卡，CPU 占用低。")
        self._record_encoder._buttons["H.264 CPU"].setToolTip("CPU 软编码。兼容性好，但 CPU 占用高。")
        self._record_encoder._buttons["Copy"].setToolTip("直接拷贝流，不重编码。速度最快。")
        card.add_widget(self._record_encoder)

        card.add_widget(self._record_setting_label("编码参数"))
        self._record_param = ChipGroup(["CRF 质量", "码率限制", "不限制"])
        param_mode = str(settings.value("param_mode", self._record_param.selected))
        if param_mode in self._record_param._items:
            self._record_param._click(param_mode)
        self._record_param._buttons["CRF 质量"].setToolTip("值越小质量越高。推荐 18-28。")
        self._record_param._buttons["码率限制"].setToolTip("固定码率编码。适合网络带宽有限的场景。")
        self._record_param._buttons["不限制"].setToolTip("不设编码参数上限。")
        card.add_widget(self._record_param)

        self._record_param_panel = ParamPanel()
        self._record_param_panel.set_crf_value(settings.value("crf", "23"))
        self._record_param_panel.set_bitrate_value(str(settings.value("bitrate", "8000")))
        self._record_param_panel.set_bitrate_unit(str(settings.value("bitrate_unit", "kbps")))
        card.add_widget(self._record_param_panel)

        self._record_start_btn = QPushButton("开始录制")
        self._record_start_btn.setObjectName("addRoomButton")
        self._record_start_btn.setToolTip("使用上方配置开始所有已连接房间的录制")
        self._record_start_btn.clicked.connect(self._on_batch_record)
        card.add_widget(self._record_start_btn)

        self._record_analyze_btn = QPushButton("分析当前录制")
        self._record_analyze_btn.setObjectName("btnSecondary")
        self._record_analyze_btn.setEnabled(False)
        self._record_analyze_btn.setToolTip("分析当前选中房间的录制文件，自动识别高光片段")
        self._record_analyze_btn.clicked.connect(self._on_analyze_current)
        card.add_widget(self._record_analyze_btn)

        self._record_export_analysis_btn = QPushButton("导出分析高光")
        self._record_export_analysis_btn.setObjectName("btnSecondary")
        self._record_export_analysis_btn.setEnabled(False)
        self._record_export_analysis_btn.setToolTip("批量导出当前选中房间的分析高光片段")
        self._record_export_analysis_btn.clicked.connect(self._on_export_analysis_results)
        card.add_widget(self._record_export_analysis_btn)

        self._record_param.selection_changed.connect(self._on_record_param_changed)
        self._record_encoder.selection_changed.connect(lambda _value: self._on_record_param_changed(self._record_param.selected))
        self._on_record_param_changed(self._record_param.selected)
        return card

    def _on_record_param_changed(self, value: str) -> None:
        panel = getattr(self, "_record_param_panel", None)
        if panel is None:
            return
        mode_map = {"CRF 质量": 0, "码率限制": 1, "不限制": 2}
        panel.set_mode(mode_map.get(value, 0))
        encoder = getattr(getattr(self, "_record_encoder", None), "selected", "")
        panel.setEnabled(encoder != "Copy" and value != "不限制")

    def _build_ui(self) -> None:
        # ── Root: page-level scroll area containing body ──
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 页面标题栏（专业软件风格） ──
        self._page_header = PageHeader("多房间工作台", "多路直播录制与切片管理")
        self._title = self._page_header._title_label
        root.addWidget(self._page_header)

        self._page_scroll = QScrollArea()
        self._page_scroll.setWidgetResizable(True)
        self._page_scroll.setFrameShape(QScrollArea.NoFrame)
        # 窗口横向压缩到容纳不下左右面板时显示横向滚动条，避免 UI 重叠/截断
        self._page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self._page_body = QWidget()
        self._page_body.setStyleSheet("background:transparent;")
        page_layout = QHBoxLayout(self._page_body)
        page_layout.setContentsMargins(20, 16, 20, 20)
        page_layout.setSpacing(16)

        self._page_scroll.setWidget(self._page_body)
        root.addWidget(self._page_scroll, 1)

        # ── Splitter for left/right panels ──
        self._splitter = QSplitter(Qt.Horizontal, self._page_body)
        self._splitter.setHandleWidth(4)
        self._splitter.setChildrenCollapsible(False)
        page_layout.addWidget(self._splitter)

        # ── Left side: toolbar, card grid, bottom control/status bar ──
        left_widget = QWidget()
        left_widget.setStyleSheet("background:transparent;")
        left_widget.setMinimumWidth(540)
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)
        self._left_layout = left_layout
        self._splitter.addWidget(left_widget)

        # ── 房间数量徽章（放在标题栏操作区，显示 N/12） ──
        self._room_limit_label = QLabel("")
        self._room_limit_label.setObjectName("roomLimitBadge")
        self._room_limit_label.setAlignment(Qt.AlignCenter)
        self._room_limit_label.setToolTip(f"已添加房间数 / 最大房间数（{MAX_ROOMS}）")
        # 必须加入布局才能显示；放在操作按钮区最左侧作为状态指示
        self._page_header.add_action(self._room_limit_label)
        self._page_header.add_separator()

        # ── 标题栏操作按钮 ──
        self._batch_record_btn = QPushButton("批量录制")
        self._batch_record_btn.setObjectName("btnSuccess")
        self._batch_record_btn.setAccessibleName("批量录制")
        self._batch_record_btn.setToolTip("对所有已连接且未录制的房间开始录制")
        self._batch_record_btn.clicked.connect(self._on_batch_record)
        self._page_header.add_action(self._batch_record_btn)

        self._batch_stop_btn = QPushButton("批量停止")
        self._batch_stop_btn.setObjectName("btnDanger")
        self._batch_stop_btn.setAccessibleName("批量停止")
        self._batch_stop_btn.setToolTip("停止所有正在录制的房间")
        self._batch_stop_btn.clicked.connect(self._on_batch_stop)
        self._page_header.add_action(self._batch_stop_btn)

        self._page_header.add_separator()

        self._mute_all_btn = QPushButton("全部静音")
        self._mute_all_btn.setObjectName("btnSecondary")
        self._mute_all_btn.setCheckable(True)
        self._mute_all_btn.setToolTip("切换所有房间预览的静音状态")
        self._mute_all_btn.clicked.connect(self._on_mute_all)
        self._page_header.add_action(self._mute_all_btn)

        self._align_live_btn = QPushButton("对齐直播")
        self._align_live_btn.setObjectName("btnSecondary")
        self._align_live_btn.setToolTip("将所有预览对齐到最新直播画面")
        self._align_live_btn.clicked.connect(self._on_align_live)
        self._page_header.add_action(self._align_live_btn)

        # ── Card grid (直接添加，高度由内容决定) ──
        self._card_container = QWidget()
        self._card_container.setStyleSheet("background:transparent;")
        self._card_layout = FlowLayout(self._card_container, spacing=_GRID_H_SPACING, max_per_row=2)
        self._card_layout.setContentsMargins(_GRID_HMARGIN, _GRID_VMARGIN, _GRID_HMARGIN, _GRID_VMARGIN)
        self._card_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        # FlowLayout 高度自适应宽度，垂直方向由内容驱动
        _sp = self._card_container.sizePolicy()
        _sp.setHeightForWidth(True)
        _sp.setVerticalPolicy(QSizePolicy.Policy.Minimum)
        self._card_container.setSizePolicy(_sp)

        # 空状态覆盖层，居中显示于卡片容器
        self._empty_label = QLabel("暂无房间\n在右侧「添加直播间」卡片输入链接开始管理", self._card_container)
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._empty_label.setObjectName("empty_state")
        self._empty_label.setVisible(False)

        left_layout.addWidget(self._card_container, 1)

        # ── Bottom control/status bar ──
        self._bottom_bar = _BottomBar()
        self._controls = self._bottom_bar.controls
        self._statusbar = self._bottom_bar.status
        left_layout.addWidget(self._bottom_bar)

        self._clip_card = Card()
        self._clip_list = ClipListWidget()
        self._clip_list.set_add_enabled(False)
        # 注入撤销栈：片段增删/清空可通过 Ctrl+Z / Ctrl+Y 撤销重做
        self._clip_list.set_undo_stack(self._undo)
        self._clip_card.add_widget(self._clip_list)
        left_layout.addWidget(self._clip_card)

        # ── Right side: detail + clip list cards（与左侧共享外层滚动，避免滚动不同步）──
        right_widget = QWidget()
        right_widget.setStyleSheet("background:transparent;")
        right_widget.setMinimumWidth(260)
        right_widget.setMaximumWidth(360)
        right_widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(12)

        # 添加直播间 Card（与录制页配置面板风格一致）
        add_card = Card()
        add_title = QLabel("添加直播间")
        add_title.setObjectName("card_title")
        add_card.add_widget(add_title)
        add_card.add_widget(QLabel("直播间链接"))
        self._url_input = InputField()
        self._url_input.setPlaceholderText("粘贴直播间链接...")
        self._url_input.setAccessibleName("直播间链接输入框")
        self._url_input.returnPressed.connect(self._on_add_room)
        add_card.add_widget(self._url_input)
        self._add_btn = QPushButton("+ 添加房间")
        self._add_btn.setObjectName("addRoomButton")
        self._add_btn.setAccessibleName("添加房间")
        self._add_btn.setToolTip("添加直播间到工作台")
        self._add_btn.clicked.connect(self._on_add_room)
        add_card.add_widget(self._add_btn)
        right_layout.addWidget(add_card)

        output_card = Card()
        output_title = QLabel("输出目录")
        output_title.setObjectName("card_title")
        output_card.add_widget(output_title)
        settings = QSettings("LSC", "LiveStreamClipper")
        default_output = os.path.join(os.path.expanduser("~"), "LSC", "output")
        self._output_input = InputField()
        self._output_input.setPlaceholderText(default_output)
        self._output_input.setText(str(settings.value("output_dir", default_output)))
        self._output_input.setAccessibleName("多房间输出目录")
        output_row = QHBoxLayout()
        output_row.setContentsMargins(0, 0, 0, 0)
        output_row.setSpacing(8)
        output_row.addWidget(self._output_input, 1)
        self._output_browse_btn = QPushButton("选择")
        self._output_browse_btn.setObjectName("btnSecondary")
        self._output_browse_btn.setToolTip("选择多房间录制和导出的输出目录")
        self._output_browse_btn.clicked.connect(self._on_browse_output_dir)
        output_row.addWidget(self._output_browse_btn)
        output_card.add_layout(output_row)
        right_layout.addWidget(output_card)

        # 录制信息 Card（与直播录制页右侧信息块保持一致）
        detail_card = Card()
        detail_title = QLabel("录制信息")
        detail_title.setObjectName("card_title")
        detail_card.add_widget(detail_title)
        self._detail = DetailPanel()
        detail_card.add_widget(self._detail)
        right_layout.addWidget(detail_card)

        # 切片列表 Card（ClipListWidget 内部已有标题，这里不再重复添加）
        self._record_settings_card = self._build_record_settings_card()
        right_layout.addWidget(self._record_settings_card)

        right_layout.addStretch()
        self._splitter.addWidget(right_widget)

        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 0)

        self._splitter.setSizes([700, 340])

    def _connect_signals(self) -> None:
        self._controls.timeline.position_changed.connect(self._on_timeline_seek)
        self._controls.mark_in_clicked.connect(self._on_mark_in)
        self._controls.mark_out_clicked.connect(self._on_mark_out)
        self._controls.export_clicked.connect(self._on_export)
        self._controls.play_pause.connect(self._on_play_pause)
        self._controls.seek_back.connect(self._on_seek_back)
        self._controls.seek_fwd.connect(self._on_seek_fwd)
        self._controls.fullscreen_clicked.connect(self._on_fullscreen)
        self._controls.stop_clicked.connect(self._on_stop_clicked)
        self._controls.clear_selection_clicked.connect(self._on_clear_selection)
        self._controls.add_clip_clicked.connect(self._on_add_clip)

        # Clip list: add current selection / export all segments
        self._clip_list._add_btn.clicked.connect(self._on_add_clip)
        self._clip_list.export_all_clicked.connect(self._on_export_all_clips)
        self._clip_list.clip_preview_requested.connect(self._on_clip_preview)

        # Listen for async connect completion
        self._manager.room_connect_finished.connect(self._on_room_connect_finished)
        # Listen for async batch recording progress/completion
        # batch_record_progress 信号签名为 (room_id, success)，覆盖单/批量录制启动结果
        self._manager.batch_record_progress.connect(self._on_batch_record_progress)
        self._manager.batch_record_finished.connect(self._on_batch_record_finished)
        # 注意：MultiRoomManager 没有 recording_started/recording_stopped 信号。
        # 单房间 start_recording / stop_recording 是同步调用，调用方直接刷新卡片即可；
        # 异步启动完成的刷新已由 batch_record_progress 覆盖（单房间异步录制也复用该 worker 信号）。
        # Drive timeline/statusbar refresh from the manager's 1s heartbeat
        # instead of relying on manual card clicks to update elapsed time.
        self._manager.global_tick.connect(self._on_global_tick)

    # ── Room management ──────────────────────────────────────

    def _toast(
        self, message: str, *, toast_type: str = "info", title: str = ""
    ):
        """显示一个 Toast 通知。

        返回 Toast 对象（可用 add_action 添加动作按钮），或 None。
        """
        try:
            import main as _main

            return _main.show_toast(message, toast_type=toast_type, title=title)
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("Toast 显示失败: %s", exc)
            try:
                self._statusbar.show_message(message)
            except Exception:
                pass
        return None

    @staticmethod
    def _open_in_explorer(path: str) -> None:
        """在文件管理器中打开指定目录。"""
        open_in_explorer(path)

    def _on_add_room(self) -> None:
        url = self._url_input.text().strip()
        if not url:
            return

        # URL 格式预验证
        if not url.startswith(("http://", "https://")):
            self._statusbar.show_message("请输入有效的直播间链接")
            self._toast("请输入有效的直播间链接（以 http:// 或 https:// 开头）", toast_type="warning", title="格式错误")
            return

        if self._manager.room_count() >= MAX_ROOMS:
            self._statusbar.show_message(f"已达最大房间数 ({MAX_ROOMS})")
            self._toast(f"最多支持 {MAX_ROOMS} 个房间", toast_type="warning", title="无法添加")
            return

        room = self._manager.add_room(url)
        if room is None:
            self._statusbar.show_message("无法添加更多房间")
            self._toast("无法添加更多房间", toast_type="error")
            return

        self._url_input.clear()
        self._add_card(room)
        self._refresh()
        self._statusbar.show_message(f"已添加房间: {url}")
        self._toast(f"已添加房间: {url}", toast_type="success")

        # 首次添加房间时显示新手引导
        settings = QSettings("LSC", "LiveStreamClipper")
        if not settings.value("onboarding_shown", False, type=bool):
            settings.setValue("onboarding_shown", True)
            from PySide6.QtCore import QTimer
            QTimer.singleShot(1500, lambda: self._toast(
                "连接直播间后可开启预览、标记选区并导出精彩片段。"
                "勾选「参与批量导出」的房间会一并导出。",
                toast_type="info",
                title="快速上手",
            ))

    def _add_card(self, room: RoomSession) -> None:
        card = RoomCard(room, self._card_container)
        card.selected.connect(self._on_room_selected)
        card.connect_clicked.connect(self._on_connect)
        card.disconnect_clicked.connect(self._on_disconnect)
        card.record_clicked.connect(self._on_record)
        card.stop_clicked.connect(self._on_stop)
        card.remove_clicked.connect(self._on_remove)
        card.preview_clicked.connect(self._on_preview)
        card.pause_clicked.connect(self._on_pause)
        card.resume_clicked.connect(self._on_resume)
        card.fullscreen_clicked.connect(self._enter_fullscreen)
        card.timeline_seek_requested.connect(self._on_card_timeline_seek)
        # 注意：RoomCard 没有 seek_relative_requested / return_live_requested 信号。
        # 相对跳转和"回到直播"通过控制栏按钮 -> page 方法直接调用，无需信号连接。
        card.mute_toggled.connect(self._on_mute_toggled)
        card.include_toggled.connect(self._on_include_toggled)
        # 注意：RoomCard 没有 size_changed 信号（该信号属于其内部的 _SizeToggleButton，
        # 见 room_card.py）。卡片尺寸变化通过 resizeEvent 处理，无需在此连接；
        # _schedule_container_resize 仍由 showEvent 在页面可见时触发。
        self._cards[room.room_id] = card

        # FlowLayout 自动按卡片自身宽度换行,无需手动指定行列。
        self._card_layout.addWidget(card)
        # 关键修复:新创建的 QWidget 默认是 hidden 状态,而 FlowLayout.doLayout
        # 会用 QWidgetItem.isEmpty()(返回 widget->isHidden())过滤掉 hidden 的
        # 卡片。若不显式 show(),新卡片会被 FlowLayout 跳过、永不排布,表现为
        # 「添加房间后卡片不显示,切到其他页面再切回来才出现」(切页触发的 show
        # 级联让 isHidden() 变 False,FlowLayout 才纳入它)。显式 show() 一行解决。
        card.show()
        card.restore_saved_size()
        self._card_layout.invalidate()
        self._card_container.updateGeometry()
        self._update_card_container_min_height()
        # Deferred sync: wait for Qt to process the layout before calculating
        # scroll area heights. layout.activate() alone is insufficient because
        # child widget size hints may not be resolved yet.
        QTimer.singleShot(50, lambda c=card: self._deferred_after_add(c))

        # 自动选中第一个房间
        if self._selected_room_id is None:
            self._on_room_selected(room.room_id)
        self._sync_mute_all_button()

    def _deferred_after_add(self, card: RoomCard) -> None:
        """Called after layout settles: sync heights, update columns."""
        self._update_grid_columns()
        # 布局稳定后重算容器高度(此时卡片宽度已就绪)
        self._update_card_container_min_height()

    def _save_timeline_marks_to_room(self, room_id: str | None) -> None:
        """将当前 timeline 的入/出点保存到指定房间（选区独立化的关键）。"""
        if not room_id:
            return
        room = self._manager.get_room(room_id)
        if room is None:
            return
        tl = self._controls.timeline
        room.mark_in = tl.get_in_point()
        room.mark_out = tl.get_out_point()

    def _on_room_selected(self, room_id: str) -> None:
        """Handle card selection with Ctrl/Shift multi-select support.

        - Plain click: single-select (clears previous selection)
        - Ctrl+click: toggle this room in the selection set
        - Shift+click: range-select from the last anchor to this room
        """
        # 切房前：把当前 timeline 选区保存到旧房间
        old_primary = self._selected_room_id

        from PySide6.QtGui import QGuiApplication
        mods = QGuiApplication.keyboardModifiers()
        ctrl = bool(mods & Qt.ControlModifier)
        shift = bool(mods & Qt.ShiftModifier)

        if ctrl:
            # Toggle membership in the multi-selection set.
            if room_id in self._selected_room_ids:
                self._selected_room_ids.discard(room_id)
                # 若取消的是当前主选房间，且多选集合非空，重置主选为集合中的另一个房间
                if self._selected_room_id == room_id:
                    self._selected_room_id = (
                        next(iter(self._selected_room_ids))
                        if self._selected_room_ids else None
                    )
            else:
                self._selected_room_ids.add(room_id)
                # Anchor stays the last-clicked room for Shift-range.
                self._selected_room_id = room_id
        elif shift and self._selected_room_id is not None:
            # Range-select from anchor to clicked room (by insertion order).
            ordered = list(self._cards.keys())
            try:
                start_idx = ordered.index(self._selected_room_id)
                end_idx = ordered.index(room_id)
            except ValueError:
                start_idx, end_idx = 0, 0
            lo, hi = min(start_idx, end_idx), max(start_idx, end_idx)
            self._selected_room_ids = set(ordered[lo:hi + 1])
        else:
            # Plain click — single selection.
            self._selected_room_ids = {room_id}
            self._selected_room_id = room_id

        # If only one room is selected, treat it as the focused room so the
        # detail panel and timeline show its data.
        if len(self._selected_room_ids) == 1:
            self._selected_room_id = next(iter(self._selected_room_ids))
        elif len(self._selected_room_ids) == 0:
            self._selected_room_id = None

        # 选区独立化：将旧房间的 timeline 选区保存，然后恢复新房间的选区
        self._save_timeline_marks_to_room(old_primary)

        # Update card visual states.
        multi = len(self._selected_room_ids) > 1
        for rid, card in self._cards.items():
            card.set_selected(rid in self._selected_room_ids, multi=multi)

        # Update sync mode indicator in control bar.
        self._controls.set_sync_count(len(self._selected_room_ids))

        # Detail panel shows the anchor room (or None if multi-selected).
        room = self._manager.get_room(self._selected_room_id) if self._selected_room_id else None
        self._detail.show_room(room)
        self._clip_list.set_current_room_url(room.room_url if room else None)
        self._update_timeline()
        self._update_multi_select_badges()
        self._update_analysis_buttons()
        self.room_selected.emit(room_id)

    def _update_analysis_buttons(self) -> None:
        """根据当前选中房间状态更新分析按钮的可用性。"""
        room = self._manager.get_room(self._selected_room_id) if self._selected_room_id else None
        has_recording = room is not None and bool(room.record_output_path)
        has_highlights = room is not None and bool(room.analysis_highlights)
        not_busy = room is not None and not room.analysis_in_progress and not room.export_in_progress
        self._record_analyze_btn.setEnabled(has_recording and not_busy)
        self._record_export_analysis_btn.setEnabled(has_highlights and not_busy)

    def _on_analyze_current(self) -> None:
        """分析当前选中房间的录制文件。"""
        if not self._selected_room_id:
            return
        room = self._manager.get_room(self._selected_room_id)
        if room is None or not room.record_output_path:
            return
        output_dir, _enc, _crf = self._get_recording_settings()
        highlights_dir = os.path.join(output_dir, "analysis")
        self._record_analyze_btn.setEnabled(False)
        self._record_analyze_btn.setText("分析中...")
        self._manager.start_analysis(
            room.room_id, "default", highlights_dir,
            on_done=self._on_analysis_done,
        )

    def _on_analysis_done(self, success: bool, result_path: str, error: str, count: int) -> None:
        """分析完成回调。"""
        self._record_analyze_btn.setText("分析当前录制")
        if success:
            self._record_analyze_btn.setToolTip(f"分析完成，发现 {count} 个高光片段")
        else:
            self._record_analyze_btn.setToolTip(f"分析失败: {error}")
        self._update_analysis_buttons()

    def _on_export_analysis_results(self) -> None:
        """批量导出当前选中房间的分析高光片段。"""
        if not self._selected_room_id:
            return
        room = self._manager.get_room(self._selected_room_id)
        if room is None or not room.analysis_highlights:
            return
        output_dir, _enc, _crf = self._get_recording_settings()
        highlights_dir = os.path.join(output_dir, "highlights")
        self._record_export_analysis_btn.setEnabled(False)
        self._record_export_analysis_btn.setText("导出中...")
        self._manager.start_export_all(
            room.room_id, highlights_dir,
            on_done=self._on_export_analysis_done,
        )

    def _on_export_analysis_done(self, success: bool, exported_count: int,
                                 total_count: int, error: str, results: object) -> None:
        """批量导出完成回调。"""
        self._record_export_analysis_btn.setText("导出分析高光")
        if success:
            self._record_export_analysis_btn.setToolTip(
                f"导出完成: {exported_count}/{total_count} 个片段成功"
            )
        else:
            self._record_export_analysis_btn.setToolTip(f"导出失败: {error}")
        self._update_analysis_buttons()

    def _update_multi_select_badges(self) -> None:
        """多选时在所有选中卡片上显示同步时间戳徽章。"""
        if len(self._selected_room_ids) <= 1:
            for card in self._cards.values():
                card.hide_sync_badge()
            return
        # 取主房间的当前位置作为同步时间戳
        pos = 0.0
        if self._selected_room_id:
            pos = self._manager.get_preview_position(self._selected_room_id)
        time_text = fmt_time(pos)
        for rid in self._selected_room_ids:
            card = self._cards.get(rid)
            if card:
                card.show_sync_badge(time_text)
        # 未选中的卡片隐藏徽章
        for rid, card in self._cards.items():
            if rid not in self._selected_room_ids:
                card.hide_sync_badge()

    def _on_align_live(self) -> None:
        """Align all active previews to their live edge in one click."""
        count = self._manager.align_previews_to_live()
        if count:
            msg = f"已对齐 {count} 路预览到直播画面"
            self._statusbar.show_message(msg)
            self._toast(msg, toast_type="success")
        else:
            self._statusbar.show_message("没有正在预览的房间")
            self._toast("没有正在预览的房间", toast_type="warning", title="无法对齐")
        self._update_timeline()

    def _on_connect(self, room_id: str) -> None:
        """Start async connection to avoid blocking the UI thread."""
        room = self._manager.get_room(room_id)
        if room is None:
            return
        room.is_connecting = True
        room.last_error = ""
        self._refresh_card(room_id)
        quality_widget = getattr(self, "_record_quality", None)
        quality = quality_widget.selected if quality_widget is not None else "原画"
        self._manager.connect_room(room_id, async_mode=True, quality_preset=quality)
        self._statusbar.show_message("正在连接...")

    def _on_room_connect_finished(self, room_id: str, success: bool, error: str) -> None:
        """Callback when async room connection completes."""
        room = self._manager.get_room(room_id)
        if room is None:
            return
        if success:
            name = room.streamer_name or room.room_url
            self._statusbar.show_message(f"连接成功: {name}")
            self._toast(f"连接成功: {name}", toast_type="success")
        else:
            from lsc.utils.error_messages import humanize_error
            friendly = humanize_error(error)
            self._statusbar.show_message(f"连接失败: {friendly}")
            self._toast(friendly, toast_type="error", title="连接失败")
        self._refresh_card(room_id)

    def _on_disconnect(self, room_id: str) -> None:
        card = self._cards.get(room_id)
        if card:
            card.remove_preview_widget()
        self._manager.disconnect_room(room_id)
        self._refresh_card(room_id)
        self._statusbar.show_message("已断开连接")

    def _on_record(self, room_id: str) -> None:
        room = self._manager.get_room(room_id)
        if room is not None:
            room.last_error = ""
        output_dir, encoder, crf, param_mode, bitrate, bitrate_unit = self._get_recording_profile()
        # 异步启动录制，不阻塞主线程；结果通过 recording_started 信号回传
        submitted = self._manager.start_recording(
            room_id,
            output_dir,
            encoder,
            crf,
            param_mode=param_mode,
            bitrate=bitrate,
            bitrate_unit=bitrate_unit,
        )
        if submitted:
            self._statusbar.show_message("正在启动录制...")
            self._refresh_card(room_id)
        else:
            room = self._manager.get_room(room_id)
            err = room.friendly_error if room and room.last_error else "录制启动失败"
            self._statusbar.show_message(f"录制失败: {err}")
            self._toast(err, toast_type="error", title="录制失败")

    def _on_recording_started(self, room_id: str, success: bool, error: str) -> None:
        """异步录制启动完成回调（主线程执行）。"""
        self._refresh_card(room_id)
        room = self._manager.get_room(room_id)
        if success and room and room.is_recording:
            self._statusbar.show_message("录制已开始")
            self._toast("录制已开始", toast_type="success")
        elif error:
            self._statusbar.show_message(f"录制失败: {error}")
            self._toast(error, toast_type="error", title="录制失败")
        else:
            self._statusbar.show_message("录制启动失败")
            self._toast("录制启动失败", toast_type="error")

    def _on_recording_stopped(self, room_id: str, error: str) -> None:
        """异步录制停止回调 - 刷新卡片状态避免按钮脱节。"""
        self._refresh_card(room_id)
        if error:
            self._statusbar.show_message(f"录制已停止: {error}")
        else:
            self._statusbar.show_message("录制已停止")

    def _on_stop(self, room_id: str) -> None:
        # 若录制正在异步启动中，先中断启动 worker
        if room_id in self._manager._single_record_workers:
            worker = self._manager._single_record_workers.get(room_id)
            if worker is not None and worker.isRunning():
                worker.requestInterruption()
                worker.wait(2000)  # 最多等 2 秒
            self._manager._single_record_workers.pop(room_id, None)
            room = self._manager.get_room(room_id)
            if room:
                room.last_error = "录制启动已取消"
            self._refresh_card(room_id)
            self._statusbar.show_message("录制启动已取消")
            return
        self._manager.stop_recording(room_id)
        self._refresh_card(room_id)
        self._statusbar.show_message("录制已停止")

    def _on_remove(self, room_id: str) -> None:
        """删除房间，并压入撤销栈。"""
        room = self._manager.get_room(room_id)
        if room is None:
            return
        # 若该房间正在全屏预览中，先退出全屏，避免回调引用已删除的对象导致崩溃
        # 全屏关闭会触发 on_restore，将 widget reparent 回 card，随后 card 被正常清理
        if self._fullscreen_window is not None:
            try:
                fullscreen_widget = self._fullscreen_window._get_widget()
                room_widget = room.preview_widget
                if room_widget is not None and fullscreen_widget is room_widget:
                    self._fullscreen_window._close()
            except (RuntimeError, AttributeError):
                # widget 已销毁或属性不存在，强制关闭全屏窗口
                try:
                    win = self._fullscreen_window.window()
                    if win is not None:
                        win.close()
                except Exception:
                    pass
                self._fullscreen_window = None

        # 保存可重建状态
        saved_url = room.room_url
        saved_include = room.include_in_cut
        # 闭包共享的可变 room_id：删除后恢复会产生新的 room_id，
        # 后续 redo 必须针对当前实际的 room_id 才能正确删除。
        state = {"room_id": room_id}

        def _do_remove() -> None:
            # 兜底：若全屏窗口仍存在（_close 未成功触发 restore），强制清理
            if self._fullscreen_window is not None:
                try:
                    self._fullscreen_window._close()
                except Exception:
                    pass
                self._fullscreen_window = None
                self._controls.set_fullscreen(False)
            current_id = state["room_id"]
            card = self._cards.pop(current_id, None)
            if card:
                card.remove_preview_widget()
                self._card_layout.removeWidget(card)
                card.deleteLater()
            self._manager.remove_room(current_id)
            # Clean up position cache
            self._last_positions.pop(current_id, None)
            # 从多选集合中移除被删除的房间
            self._selected_room_ids.discard(current_id)
            if self._selected_room_id == current_id:
                self._selected_room_id = None
                self._detail.show_room(None)
            self._rebuild_grid()
            self._refresh()

        def _do_restore() -> None:
            new_room = self._manager.add_room(saved_url)
            if new_room is not None:
                # 更新共享状态，使后续 redo 能定位到恢复后的新房间
                state["room_id"] = new_room.room_id
                new_room.include_in_cut = saved_include
                self._add_card(new_room)
                self._refresh()
                self._statusbar.show_message(f"已撤销删除: {saved_url}")

        cmd = Command(
            description=f"删除房间 {room.streamer_name or room.room_url}",
            undo=_do_restore,
            redo=_do_remove,
        )
        self._undo.execute(cmd)

    def _on_preview(self, room_id: str) -> None:
        ok = self._manager.start_preview(room_id)
        if ok:
            room = self._manager.get_room(room_id)
            card = self._cards.get(room_id)
            if room and card and room.preview_widget is not None:
                # 先嵌入 widget：reparent 触发 rebind_video_output 重建 mpv
                # 实例并绑定到卡片内稳定的新 HWND。
                card.set_preview_widget(room.preview_widget)
                # 嵌入+rebind 完成后再延迟播放：mpv.play 必须绑定到稳定句柄才
                # 能正常渲染首帧，否则在 Windows 上 reparent 改变 HWND 会让早期
                # 的播放请求失效，表现为预览黑屏。
                from PySide6.QtCore import QTimer
                QTimer.singleShot(
                    150,
                    lambda rid=room_id: self._manager.play_preview_stream(rid),
                )
        self._refresh_card(room_id)
        if not ok:
            room = self._manager.get_room(room_id)
            msg = room.preview_error if room and room.preview_error else "无法开启预览"
            self._statusbar.show_message(msg)
            self._toast(msg, toast_type="error", title="预览失败")

    def _on_pause(self, room_id: str) -> None:
        self._manager.pause_preview(room_id)
        self._refresh_card(room_id)

    def _on_resume(self, room_id: str) -> None:
        self._manager.resume_preview(room_id)
        self._refresh_card(room_id)

    def _on_include_toggled(self, room_id: str, checked: bool) -> None:
        room = self._manager.get_room(room_id)
        if room:
            room.include_in_cut = checked
            # 持久化选区偏好，避免重启后丢失
            self._manager.save_rooms()

    def _on_batch_record(self) -> None:
        rooms = self._manager.list_rooms()
        candidates = [r for r in rooms if r.is_connected and not r.is_recording]
        if not candidates:
            self._statusbar.show_message("没有可录制的房间")
            self._toast("没有可录制的房间", toast_type="warning", title="无法录制")
            return
        # 显示即将录制的房间数作为反馈
        self._statusbar.show_message(f"正在启动 {len(candidates)} 路录制...")
        self._toast(f"正在启动 {len(candidates)} 路录制", toast_type="info")
        # Run batch recording in a background thread to avoid blocking the UI
        # while FFmpeg starts up for each room.
        output_dir, encoder, crf, param_mode, bitrate, bitrate_unit = self._get_recording_profile()
        started = self._manager.start_recording_all_async(
            output_dir,
            encoder,
            crf,
            param_mode=param_mode,
            bitrate=bitrate,
            bitrate_unit=bitrate_unit,
        )
        if not started:
            self._statusbar.show_message("批量录制启动失败（磁盘空间不足或已在进行中）")
            self._toast("批量录制启动失败", toast_type="error")

    def _on_batch_record_progress(self, room_id: str, success: bool) -> None:
        self._refresh_card(room_id)

    def _on_batch_record_finished(self, started_count: int, total_count: int) -> None:
        self._refresh_all()
        msg = f"批量录制: {started_count}/{total_count} 路已启动"
        self._statusbar.show_message(msg)
        if started_count == total_count:
            self._toast(msg, toast_type="success")
        elif started_count > 0:
            self._toast(msg, toast_type="warning")
        else:
            self._toast(msg, toast_type="error")

    def _on_batch_stop(self) -> None:
        count = 0
        for room in self._manager.list_rooms():
            rid = room.room_id
            # 先处理异步启动中的房间：中断 worker，避免 FFmpeg 孤儿进程
            if rid in self._manager._single_record_workers:
                worker = self._manager._single_record_workers.get(rid)
                if worker is not None and worker.isRunning():
                    worker.requestInterruption()
                    worker.wait(2000)
                self._manager._single_record_workers.pop(rid, None)
                room.last_error = "录制启动已取消"
                count += 1
                self._refresh_card(rid)
                continue
            if room.is_recording:
                self._manager.stop_recording(rid)
                count += 1
        self._refresh_all()
        msg = f"批量停止: {count} 路已停止"
        self._statusbar.show_message(msg)
        self._toast(msg, toast_type="success" if count > 0 else "warning")

    def _on_mute_all(self) -> None:
        all_muted = all(r.preview_muted for r in self._manager.list_rooms())
        new_muted = not all_muted
        for room in self._manager.list_rooms():
            self._manager.set_preview_muted(room.room_id, new_muted)
        self._refresh_all()
        self._mute_all_btn.setText("取消静音" if new_muted else "全部静音")
        self._mute_all_btn.setChecked(new_muted)
        self._statusbar.show_message("全部静音" if new_muted else "已取消全部静音")
        self._manager.save_rooms()

    def _on_mute_toggled(self, room_id: str, muted: bool) -> None:
        """单个卡片静音切换：更新 manager 状态并同步全部静音按钮。"""
        self._manager.set_preview_muted(room_id, muted)
        self._sync_mute_all_button()
        self._manager.save_rooms()

    def _sync_mute_all_button(self) -> None:
        """根据所有房间的静音状态同步「全部静音」按钮。"""
        rooms = self._manager.list_rooms()
        if not rooms:
            return
        all_muted = all(r.preview_muted for r in rooms)
        self._mute_all_btn.setText("取消静音" if all_muted else "全部静音")
        self._mute_all_btn.setChecked(all_muted)

    # ── Timeline ─────────────────────────────────────────────

    def _update_timeline(self) -> None:
        room = self._manager.get_room(self._selected_room_id) if self._selected_room_id else None
        if room and room.controller:
            # Duration: prefer the preview widget's reported duration, fall
            # back to recording elapsed time or the recorded file's actual duration.
            duration = self._manager.get_preview_duration(room.room_id)
            if duration <= 0:
                duration = getattr(room.controller, "total_sec", 0) or 0
            if duration <= 0:
                duration = self._room_video_duration(room)
            position = self._manager.get_preview_position(room.room_id)
            # 选区独立化：从房间状态恢复入/出点到 timeline
            self._controls.timeline.set_data(
                duration=duration, position=position,
                start=room.mark_in, end=room.mark_out
            )
            self._controls.set_time(position, duration)
            # 只要有可导出的录制文件，就启用 timeline 控制（入/出点、导出）
            has_video = self._room_has_exportable_video(room)
            self._controls.set_recording(room.is_recording or has_video)
        else:
            self._controls.timeline.set_data(duration=0, position=0)
            self._controls.set_time(0, 0)
            self._controls.set_recording(False)
        self._sync_range_state()

    @staticmethod
    def _room_has_exportable_video(room) -> bool:
        # 优先使用 room.record_output_path（多房间录制路径）
        path = getattr(room, "record_output_path", "") or ""
        if path and os.path.isfile(path):
            return True
        # 回退到 controller.video_path（单房间录制页路径）
        controller = room.controller if room else None
        if controller is None:
            return False
        path = getattr(controller, "video_path", "") or ""
        return bool(path and os.path.isfile(path))

    def _room_video_duration(self, room) -> float:
        controller = room.controller if room else None
        if controller is None:
            return 0.0
        path = getattr(controller, "video_path", "") or ""
        if not path or not os.path.isfile(path):
            return 0.0
        return controller.probe_video_duration()

    def _on_timeline_seek(self, position: float) -> None:
        # When multiple rooms are selected, seek all of them in lockstep so
        # the user can scrub several streams at the same timestamp.
        if len(self._selected_room_ids) > 1:
            self._manager.seek_selected_previews(list(self._selected_room_ids), position)
            for rid in self._selected_room_ids:
                self._check_return_live(rid)
        else:
            room = self._manager.get_room(self._selected_room_id) if self._selected_room_id else None
            if room and room.controller:
                self._manager.seek_preview(room.room_id, position)
                self._check_return_live(room.room_id)
        self._update_timeline()

    def _on_card_timeline_seek(self, room_id: str, position: float) -> None:
        room = self._manager.get_room(room_id)
        if room is None or not room.preview_enabled:
            return
        self._manager.seek_preview(room_id, position)
        if self._selected_room_id == room_id:
            self._update_timeline()
        self._update_card_timeline(room_id)
        self._check_return_live(room_id)

    def _on_return_live(self, room_id: str) -> None:
        """跳转到最新直播画面。

        对于直播流（duration 通常为 0），回退到 controller.total_sec
        作为候选位置，确保回直播功能对直播流也有效。
        """
        duration = self._manager.get_preview_duration(room_id)
        if duration > 0:
            self._manager.seek_preview(room_id, duration)
        else:
            # 直播流回退：使用 controller.total_sec 作为最新位置
            room = self._manager.get_room(room_id)
            if room and room.controller:
                total = getattr(room.controller, "total_sec", 0) or 0
                if total > 0:
                    self._manager.seek_preview(room_id, float(total))
        card = self._cards.get(room_id)
        if card:
            card.set_return_live_visible(False)
        if self._selected_room_id == room_id:
            self._update_timeline()
        self._update_card_timeline(room_id)

    def _check_return_live(self, room_id: str) -> None:
        """偏离直播边缘超过 5 秒时显示回直播浮窗。

        对于直播流（duration 为 0），使用 controller.total_sec
        作为直播边缘的候选位置。
        """
        card = self._cards.get(room_id)
        if card is None:
            return
        duration = self._manager.get_preview_duration(room_id)
        position = self._manager.get_preview_position(room_id)
        # 直播流回退：duration 为 0 时使用 controller.total_sec
        if duration <= 0:
            room = self._manager.get_room(room_id)
            if room and room.controller:
                duration = float(getattr(room.controller, "total_sec", 0) or 0)
        if duration <= 0:
            return
        behind = duration - position
        card.set_return_live_visible(behind > 5.0)

    def _on_card_seek_relative(self, room_id: str, delta: float) -> None:
        """卡片控制栏的相对跳转（后退/前进 10s）。"""
        room = self._manager.get_room(room_id)
        if room is None or not room.preview_enabled:
            return
        current = self._manager.get_preview_position(room_id)
        if current <= 0 and room.controller:
            current = float(getattr(room.controller, "current_sec", 0) or 0)
        new_pos = max(0, current + delta)
        self._manager.seek_preview(room_id, new_pos)
        if self._selected_room_id == room_id:
            self._update_timeline()
        self._update_card_timeline(room_id)
        self._check_return_live(room_id)

    def _on_mark_in(self) -> None:
        room = self._manager.get_room(self._selected_room_id) if self._selected_room_id else None
        if not room or not room.controller:
            return
        pos = self._manager.get_preview_position(room.room_id)
        tl = self._controls.timeline
        end = tl.get_out_point()
        if end is not None and pos > end:
            # 入点超过出点时，自动交换
            tl.set_data(start=end, end=pos)
            # 写回所有选中房间
            for rid in self._selected_room_ids:
                r = self._manager.get_room(rid)
                if r:
                    r.mark_in, r.mark_out = end, pos
        else:
            tl.set_in_point(pos)
            for rid in self._selected_room_ids:
                r = self._manager.get_room(rid)
                if r:
                    r.mark_in = pos
        self._sync_range_state()
        # 持久化选区，避免重启后丢失
        self._manager.save_rooms()

    def _on_mark_out(self) -> None:
        room = self._manager.get_room(self._selected_room_id) if self._selected_room_id else None
        if not room or not room.controller:
            return
        pos = self._manager.get_preview_position(room.room_id)
        tl = self._controls.timeline
        start = tl.get_in_point()
        if start is not None and pos < start:
            # 出点小于入点时，自动交换
            tl.set_data(start=pos, end=start)
            for rid in self._selected_room_ids:
                r = self._manager.get_room(rid)
                if r:
                    r.mark_in, r.mark_out = pos, start
        else:
            tl.set_out_point(pos)
            for rid in self._selected_room_ids:
                r = self._manager.get_room(rid)
                if r:
                    r.mark_out = pos
        self._sync_range_state()
        # 持久化选区，避免重启后丢失
        self._manager.save_rooms()

    def _sync_range_state(self) -> None:
        """同步入/出点状态到控制栏和切片列表的"添加选区"按钮。"""
        tl = self._controls.timeline
        has_in = tl.get_in_point() is not None
        has_out = tl.get_out_point() is not None
        self._controls.set_range_state(has_in, has_out)
        self._controls.set_export_enabled(has_in and has_out)
        self._clip_list.set_add_enabled(has_in and has_out)

    # ── Export progress ──────────────────────────────────────

    def _on_export_progress(self, task_key: tuple, percent: float, elapsed: float, total: float) -> None:
        """单个导出任务进度回调，聚合后更新状态栏进度条。"""
        # 忽略已完成任务的迟到进度回调，避免重复计数导致进度回退
        if task_key in self._export_completed_rooms:
            return
        self._export_progress[task_key] = percent
        self._refresh_export_progress()

    def _refresh_export_progress(self) -> None:
        """根据当前任务状态刷新进度条。

        进度 = (已完成任务数 + 进行中任务的百分比之和) / 总任务数 * 100
        单个任务完成时立即推进进度，避免视觉停滞。
        """
        if self._export_total_tasks <= 0:
            return
        completed_ratio = self._export_completed_tasks / self._export_total_tasks
        in_progress_ratio = sum(self._export_progress.values()) / (self._export_total_tasks * 100)
        overall = min((completed_ratio + in_progress_ratio) * 100, 100.0)
        self._statusbar.show_progress(overall)

    def _finish_export_progress(self, task_key: tuple) -> None:
        """某个导出任务完成后从跟踪中移除，全部完成则隐藏进度条。"""
        # 幂等保护：同一任务多次调用只处理一次（_on_done 可能被触发多次）
        if task_key in self._export_completed_rooms:
            return
        self._export_completed_rooms.add(task_key)
        self._export_progress.pop(task_key, None)
        self._export_completed_tasks += 1
        # 完成时立即刷新进度条，避免等待下一个进度回调造成视觉停滞
        if self._export_completed_tasks >= self._export_total_tasks:
            # 全部完成：先显示 100% 再隐藏，给用户视觉确认
            self._statusbar.show_progress(100)
            self._statusbar.hide_progress()
            # 重置计数器
            self._export_total_tasks = 0
            self._export_completed_tasks = 0
            self._export_completed_rooms.clear()
        else:
            self._refresh_export_progress()

    def _on_add_clip(self) -> None:
        """将当前入/出点选区添加为切片列表中的一个片段。"""
        tl = self._controls.timeline
        start = tl.get_in_point()
        end = tl.get_out_point()
        if start is None or end is None:
            self._toast("请先设置入点和出点", toast_type="warning", title="无法添加片段")
            return
        index = self._clip_list.add_segment(start, end)
        if index >= 0:
            self._toast(
                f"已添加片段 #{index + 1} ({fmt_time(end - start)})",
                toast_type="success",
            )
            # 添加后清除当前选区，方便标记下一段
            tl.clear_selection()
            self._sync_range_state()

    def _on_stop_clicked(self) -> None:
        """停止当前选中房间的录制。"""
        if not self._selected_room_id:
            return
        room = self._manager.get_room(self._selected_room_id)
        if room is None or not room.is_recording:
            return
        self._on_stop(self._selected_room_id)

    def _on_clear_selection(self) -> None:
        """清除当前 timeline 的入/出点选区。"""
        tl = self._controls.timeline
        tl.clear_selection()
        self._sync_range_state()
        # 同步清除所有选中房间的 mark_in/mark_out
        for rid in self._selected_room_ids:
            r = self._manager.get_room(rid)
            if r:
                r.mark_in = None
                r.mark_out = None
        self._manager.save_rooms()

    def _on_clip_preview(self, start: float, end: float) -> None:
        """打开弹窗预览指定切片片段。"""
        room = self._manager.get_room(self._selected_room_id) if self._selected_room_id else None
        if room is None:
            self._toast("请先选择一个房间", toast_type="warning", title="无法预览")
            return
        video_path = getattr(room, "record_output_path", "") or ""
        if not video_path and room.controller:
            video_path = getattr(room.controller, "video_path", "") or ""
        if not video_path or not os.path.isfile(video_path):
            self._toast("没有录制文件，无法预览切片", toast_type="warning", title="无法预览")
            return
        self._open_clip_preview_dialog(video_path, start, end)

    def _open_clip_preview_dialog(self, video_path: str, start: float, end: float) -> None:
        """使用 mpv 播放器弹窗预览切片。"""
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QDialog, QVBoxLayout

        dialog = QDialog(self)
        dialog.setWindowTitle(f"切片预览 — {fmt_time(start)} ~ {fmt_time(end)}")
        dialog.setMinimumSize(800, 500)
        dialog.resize(960, 580)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(0, 0, 0, 0)

        try:
            from lsc.gui.components.mpv_widget import MpvWidget
            player = MpvWidget(dialog)
            layout.addWidget(player)
            dialog.show()

            def _load_and_seek():
                try:
                    player.play(video_path)
                    QTimer.singleShot(300, lambda: player.seek_to(start))
                    duration = end - start
                    QTimer.singleShot(int(duration * 1000) + 500, lambda: player.pause() if dialog.isVisible() else None)
                except Exception as exc:
                    _log.warning("切片预览加载失败: %s", exc)

            QTimer.singleShot(200, _load_and_seek)
        except ImportError:
            self._toast("mpv 组件不可用，无法预览", toast_type="error", title="预览失败")
            return

        dialog.exec()

    def _on_export_all_clips(self) -> None:
        """导出切片列表中的所有片段。"""
        segments = self._clip_list.segments()
        if not segments:
            self._toast("切片列表为空", toast_type="warning", title="无法导出")
            return
        rooms = self._manager.get_rooms_for_cut()
        if not rooms:
            self._toast("没有参与批量导出的房间", toast_type="warning", title="无法导出")
            return

        # 导出前弹出确认对话框（含编码参数配置）
        room_names = [r.streamer_name or r.platform or r.room_url for r in rooms]
        dlg = ExportConfirmDialog(segments, room_names, parent=self)
        if dlg.exec() != ExportConfirmDialog.Accepted:
            return
        export_profile = dlg.get_profile()

        total_segments = len(segments)
        succeeded: list[tuple[str, int, str, float]] = []
        failed: list[tuple[str, int, str]] = []
        # 使用计数器代替列表，避免竞态条件
        remaining_count = 0

        # 先统计将要提交的任务数，提前设置进度计数器，避免异步回调竞态
        pending_tasks: list[tuple] = []
        for seg_idx, seg in enumerate(segments):
            for room in rooms:
                controller = room.controller
                # 优先使用 room.record_output_path（多房间录制路径）
                video_path = getattr(room, "record_output_path", "") or ""
                if not video_path:
                    video_path = controller.video_path if controller else ""
                if not video_path:
                    continue
                pending_tasks.append((seg_idx, seg, room))

        if not pending_tasks:
            self._statusbar.show_message("没有可导出的片段")
            return

        remaining_count = len(pending_tasks)
        # 提前设置进度计数器，避免异步回调在计数器设置前触发导致提前完成
        if not self._export_progress and not self._export_completed_rooms:
            self._export_total_tasks = remaining_count
            self._export_completed_tasks = 0
        else:
            self._export_total_tasks += remaining_count

        msg = f"正在导出 {remaining_count} 个片段（{total_segments} 段 × {len(rooms)} 房间）..."
        self._statusbar.show_message(msg)
        self._toast(msg, toast_type="info", title="开始导出")

        for seg_idx, seg, room in pending_tasks:
            controller = room.controller
            fallback_output, _encoder, _crf = self._get_recording_settings()
            output_dir = controller.output_dir if controller and controller.output_dir else fallback_output
            title = f"{room.streamer_name or room.platform}_clip{seg_idx + 1}_{seg.start:.0f}s-{seg.end:.0f}s"
            task_key = (room.room_id, seg_idx)

            def _on_done(success, path, error, size_mb, _key=task_key):
                nonlocal remaining_count
                with self._export_lock:
                    if success:
                        succeeded.append((_key[0], _key[1], path, size_mb))
                    else:
                        failed.append((_key[0], _key[1], error))
                    self._finish_export_progress(_key)
                    remaining_count -= 1
                    current_rem = remaining_count
                if current_rem > 0:
                    return
                # 全部完成
                with self._export_lock:
                    succeeded_copy = list(succeeded)
                    failed_copy = list(failed)
                total = len(succeeded_copy) + len(failed_copy)
                if not failed_copy:
                    total_size = sum(s for _, _, _, s in succeeded_copy)
                    msg = f"批量导出完成: {len(succeeded_copy)}/{total} 成功 ({total_size:.1f}MB)"
                    self._statusbar.show_message(msg)
                    toast = self._toast(msg, toast_type="success", title="导出完成")
                    if toast and succeeded_copy:
                        first_path = succeeded_copy[0][2]
                        toast.add_action("打开文件夹", lambda: self._open_in_explorer(first_path))
                else:
                    msg = (
                        f"批量导出: {len(succeeded_copy)}/{total} 成功, "
                        f"{len(failed_copy)} 失败（首条: {failed_copy[0][2]}）"
                    )
                    self._statusbar.show_message(msg)
                    self._toast(msg, toast_type="warning", title="导出完成")

            if self._manager.start_export(
                room.room_id, seg.start, seg.end, output_dir, title,
                on_done=_on_done, profile=export_profile,
                on_progress=lambda p, e, t, _key=task_key: self._on_export_progress(_key, p, e, t),
            ):
                pass
            else:
                # start_export 同步失败，立即处理
                with self._export_lock:
                    failed.append((room.room_id, seg_idx, "无法启动导出任务"))
                    self._finish_export_progress(task_key)
                    remaining_count -= 1

        # 处理所有任务都同步失败的情况
        with self._export_lock:
            current_rem = remaining_count
            succeeded_copy = list(succeeded)
            failed_copy = list(failed)
        if current_rem <= 0 and (succeeded_copy or failed_copy):
            total = len(succeeded_copy) + len(failed_copy)
            msg = f"批量导出: {len(succeeded_copy)}/{total} 成功, {len(failed_copy)} 失败"
            self._statusbar.show_message(msg)
            self._toast(msg, toast_type="warning" if failed_copy else "success", title="导出完成")

    def _on_export(self) -> None:
        """Export selected time range from all rooms marked for cut.

        选区独立化后，每个房间使用自己的 mark_in/mark_out。
        若房间未设置选区，回退到 timeline 的当前值。
        """
        tl = self._controls.timeline
        # 默认使用当前 timeline 的入/出点（作为回退值）
        default_start = tl.get_in_point()
        default_end = tl.get_out_point()
        if default_start is None or default_end is None:
            self._statusbar.show_message("请先设置入点和出点")
            self._toast("请先设置入点和出点", toast_type="warning", title="无法导出")
            return

        rooms = self._manager.get_rooms_for_cut()
        if not rooms:
            self._statusbar.show_message("没有参与批量导出的房间")
            self._toast("没有参与批量导出的房间", toast_type="warning", title="无法导出")
            return

        if default_end <= default_start:
            self._statusbar.show_message("出点必须在入点之后")
            self._toast("出点必须在入点之后", toast_type="warning", title="无法导出")
            return

        # 导出前弹出确认对话框（含编码参数配置）
        single_segment = ClipSegment(start=default_start, end=default_end)
        room_names = [r.streamer_name or r.platform or r.room_url for r in rooms]
        dlg = ExportConfirmDialog([single_segment], room_names, parent=self)
        if dlg.exec() != ExportConfirmDialog.Accepted:
            return
        export_profile = dlg.get_profile()

        # Accumulate per-room export results and report a single summary
        # once all exports finish, instead of overwriting the status bar
        # with each completion.
        succeeded: list[tuple[str, str, float]] = []  # room_id, path, size_mb
        failed: list[tuple[str, str]] = []  # room_id, error
        # 使用计数器代替列表，避免竞态条件
        remaining_count = 0

        # 先统计将要提交的任务数，提前设置进度计数器
        pending_rooms: list = []
        for room in rooms:
            controller = room.controller
            # 优先使用 room.record_output_path（多房间录制路径）
            video_path = getattr(room, "record_output_path", "") or ""
            if not video_path:
                video_path = controller.video_path if controller else ""
            if not video_path:
                continue
            # 选区独立化：优先使用每个房间自己的入/出点，回退到 timeline 的默认值
            room_start = room.mark_in if room.mark_in is not None else default_start
            room_end = room.mark_out if room.mark_out is not None else default_end
            if room_end <= room_start:
                continue  # 跳过选区无效的房间
            pending_rooms.append((room, room_start, room_end))

        if not pending_rooms:
            self._statusbar.show_message("没有可导出的录制文件")
            return

        remaining_count = len(pending_rooms)
        # 提前设置进度计数器，避免异步回调在计数器设置前触发
        if not self._export_progress and not self._export_completed_rooms:
            self._export_total_tasks = remaining_count
            self._export_completed_tasks = 0
        else:
            self._export_total_tasks += remaining_count

        self._statusbar.show_message(f"正在导出 {remaining_count} 个片段...")

        for room, room_start, room_end in pending_rooms:
            controller = room.controller
            fallback_output, _encoder, _crf = self._get_recording_settings()
            output_dir = controller.output_dir if controller and controller.output_dir else fallback_output
            title = f"{room.streamer_name or room.platform}_{room_start:.0f}s-{room_end:.0f}s"
            task_key = (room.room_id, 0)  # _on_export 每房间只有一个片段，seg_idx 固定为 0

            def _on_done(success, path, error, size_mb, _key=task_key):
                nonlocal remaining_count
                with self._export_lock:
                    if success:
                        succeeded.append((_key[0], path, size_mb))
                    else:
                        failed.append((_key[0], error))
                    self._finish_export_progress(_key)
                    remaining_count -= 1
                    current_rem = remaining_count
                if current_rem > 0:
                    return
                # All done — emit a single consolidated message.
                with self._export_lock:
                    succeeded_copy = list(succeeded)
                    failed_copy = list(failed)
                total = len(succeeded_copy) + len(failed_copy)
                if not failed_copy:
                    total_size = sum(s for _, _, s in succeeded_copy)
                    summary = (
                        f"批量导出完成: {len(succeeded_copy)}/{total} 成功 "
                        f"({total_size:.1f}MB)"
                    )
                    self._statusbar.show_message(summary)
                    toast = self._toast(summary, toast_type="success", title="导出完成")
                    if toast and succeeded_copy:
                        first_path = succeeded_copy[0][1]
                        toast.add_action("打开文件夹", lambda: self._open_in_explorer(first_path))
                else:
                    first_err = failed_copy[0][1]
                    summary = (
                        f"批量导出: {len(succeeded_copy)}/{total} 成功, "
                        f"{len(failed_copy)} 失败（首条错误: {first_err}）"
                    )
                    self._statusbar.show_message(summary)
                    self._toast(summary, toast_type="warning", title="导出完成")

            if self._manager.start_export(
                room.room_id, room_start, room_end, output_dir, title,
                on_done=_on_done, profile=export_profile,
                on_progress=lambda p, e, t, _key=task_key: self._on_export_progress(_key, p, e, t),
            ):
                pass
            else:
                # start_export returned False synchronously — treat as failure.
                with self._export_lock:
                    failed.append((room.room_id, "无法启动导出任务"))
                    self._finish_export_progress(task_key)
                    remaining_count -= 1

        # 处理所有任务都同步失败的情况
        with self._export_lock:
            current_rem = remaining_count
            succeeded_copy = list(succeeded)
            failed_copy = list(failed)
        if current_rem <= 0 and (succeeded_copy or failed_copy):
            total = len(succeeded_copy) + len(failed_copy)
            self._statusbar.show_message(
                f"批量导出: {len(succeeded_copy)}/{total} 成功"
            )

    def _on_play_pause(self) -> None:
        """Toggle playback on the currently selected room's preview.

        Previously this only flipped a local boolean and never reached the
        mpv widget, so the button was purely decorative. Now it delegates
        to the manager so the video actually pauses/resumes.
        """
        room = self._manager.get_room(self._selected_room_id) if self._selected_room_id else None
        if room is None or not room.preview_enabled:
            self._statusbar.show_message("请先开启预览")
            # Keep the control bar icon in sync with reality.
            self._controls.set_playing(False)
            return
        if room.preview_paused:
            self._manager.resume_preview(room.room_id)
            self._controls.set_playing(True)
        else:
            self._manager.pause_preview(room.room_id)
            self._controls.set_playing(False)
        self._refresh_card(room.room_id)

    def _on_seek_back(self) -> None:
        # Multi-select: move every selected preview back 10 seconds.
        if len(self._selected_room_ids) > 1:
            for rid in self._selected_room_ids:
                pos = max(0.0, self._manager.get_preview_position(rid) - 10.0)
                self._manager.seek_preview(rid, pos)
            self._update_timeline()
            return
        room = self._manager.get_room(self._selected_room_id) if self._selected_room_id else None
        if room is None or not room.preview_enabled:
            return
        pos = max(0.0, self._manager.get_preview_position(room.room_id) - 10.0)
        self._manager.seek_preview(room.room_id, pos)
        self._update_timeline()

    def _on_seek_fwd(self) -> None:
        # Multi-select: move every selected preview forward 10 seconds.
        if len(self._selected_room_ids) > 1:
            for rid in self._selected_room_ids:
                duration = self._manager.get_preview_duration(rid)
                if duration <= 0:
                    r = self._manager.get_room(rid)
                    duration = float(getattr(r.controller, "total_sec", 0) or 0) if r and r.controller else 0.0
                pos = min(duration, self._manager.get_preview_position(rid) + 10.0)
                self._manager.seek_preview(rid, pos)
            self._update_timeline()
            return
        room = self._manager.get_room(self._selected_room_id) if self._selected_room_id else None
        if room is None or not room.preview_enabled:
            return
        duration = self._manager.get_preview_duration(room.room_id)
        if duration <= 0:
            duration = getattr(room.controller, "total_sec", 0) or 0
        pos = min(duration, self._manager.get_preview_position(room.room_id) + 10.0)
        self._manager.seek_preview(room.room_id, pos)
        self._update_timeline()

    def _on_fullscreen(self) -> None:
        """Toggle a fullscreen preview for the currently selected room.

        Reparents the room's MpvWidget into a temporary top-level window so
        the user gets an immersive view of a single stream. On exit the
        widget is reparented back into its RoomCard. This avoids creating a
        second libmpv instance / stream connection.
        """
        room = self._manager.get_room(self._selected_room_id) if self._selected_room_id else None
        if room is None or room.preview_widget is None:
            self._statusbar.show_message("请先选择一个有预览的房间")
            return
        self._enter_fullscreen(room.room_id)

    def _enter_fullscreen(self, room_id: str) -> None:
        """Enter fullscreen preview for the given room with player controls."""
        if self._fullscreen_window is not None:
            self._fullscreen_window.close()
            return

        room = self._manager.get_room(room_id)
        card = self._cards.get(room_id)
        if room is None or card is None or room.preview_widget is None:
            return

        # Remove widget from card before reparenting
        widget = room.preview_widget
        card.remove_preview_widget()

        # Create title
        title = f"全屏预览 - {room.streamer_name or room.platform_name or room.room_url}"

        # Define callbacks for the shared FullscreenPreview
        def get_widget():
            return widget

        def get_controls():
            return None  # Use builtin controls

        def get_position():
            # Try widget's position first (works for mpv preview)
            pos_fn = getattr(widget, "position_sec", None)
            if callable(pos_fn):
                pos = pos_fn()
                if pos > 0:
                    return pos
            return self._manager.get_preview_position(room_id)

        def get_duration():
            # Try widget's duration first (works for mpv preview)
            dur_fn = getattr(widget, "duration_sec", None)
            if callable(dur_fn):
                dur = dur_fn()
                if dur > 0:
                    return dur
            return self._manager.get_preview_duration(room_id)

        def is_paused():
            return bool(room.preview_paused)

        def is_muted():
            return bool(room.preview_muted)

        def on_toggle_play():
            if room.preview_paused:
                self._manager.resume_preview(room_id)
            else:
                self._manager.pause_preview(room_id)
            self._refresh_card(room_id)

        def on_toggle_mute(muted):
            self._manager.set_preview_muted(room_id, muted)
            self._refresh_card(room_id)

        def on_seek(value):
            # Seek the widget directly
            seek_fn = getattr(widget, "seek_to", None)
            if callable(seek_fn):
                seek_fn(float(value))
            # Also update controller position
            if room.controller is not None:
                room.controller.current_sec = float(value)
            self._update_card_timeline(room_id)

        def on_restore(w, c):
            card.set_preview_widget(w)
            self._fullscreen_window = None
            self._controls.set_fullscreen(False)
            # 全屏退出后预览恢复由 set_preview_widget 内部的 singleShot(100ms)
            # rebind_video_output 完成；若 widget 当时不可见会标记
            # _needs_rebind_on_show，showEvent 时会自动 rebind。
            # 不再额外触发延迟 rebuild，避免与 set_preview_widget 的 rebind
            # 重复执行导致画面闪烁/双重绑定。

        # Create and enter fullscreen
        fp = FullscreenPreview(
            self,
            get_widget=get_widget,
            get_controls=get_controls,
            get_position=get_position,
            get_duration=get_duration,
            is_paused=is_paused,
            is_muted=is_muted,
            on_toggle_play=on_toggle_play,
            on_toggle_mute=on_toggle_mute,
            on_seek=on_seek,
            on_restore=on_restore,
            title=title,
        )
        fp.enter()

        if not fp.is_active():
            return

        self._fullscreen_window = fp
        self._controls.set_fullscreen(True)

    def _on_global_tick(self) -> None:
        """Refresh timeline and statusbar on every manager heartbeat.

        Replaces the old behaviour where elapsed time only updated when
        the user clicked a card.

        Optimized: only update timelines for rooms that are actively
        recording or previewing, and throttle updates when position
        hasn't changed significantly.
        """
        # Update selected room timeline (always, for responsiveness)
        if self._selected_room_id is not None:
            self._update_timeline()
            # 刷新右侧录制信息面板
            room = self._manager.get_room(self._selected_room_id)
            if room:
                self._detail.show_room(room)

        # Update card timelines only for active rooms with throttling
        for room_id, room in self._manager._rooms.items():
            if not (room.is_recording or room.preview_enabled):
                continue

            # Get current position
            position = self._manager.get_preview_position(room_id)
            if position <= 0 and room.controller:
                position = float(getattr(room.controller, "current_sec", 0) or 0)

            # Throttle: only update if position changed significantly
            last_pos = self._last_positions.get(room_id, -1)
            if abs(position - last_pos) >= self._POSITION_THRESHOLD:
                self._update_card_timeline(room_id)
                self._last_positions[room_id] = position
                # 检查是否需要显示/隐藏回直播浮窗
                self._check_return_live(room_id)

        # Update status bar (lightweight, always run)
        self._update_statusbar()

        # Multi-select badge update (only when needed)
        if len(self._selected_room_ids) > 1:
            self._update_multi_select_badges()

    # ── Grid rebuild ─────────────────────────────────────────

    def _rebuild_grid(self) -> None:
        """FlowLayout 自身负责换行,这里只需触发一次重排。"""
        self._card_layout.invalidate()
        self._update_card_container_min_height()

    def _schedule_container_resize(self) -> None:
        """防抖触发直播间区域高度重算。

        卡片尺寸(小/中/大预设)切换、增删房间时调用,避免拖拽或频繁切换
        导致重复计算。定时器到期后由 _update_card_container_min_height 完成。
        """
        self._resize_debounce.start()

    def _update_card_container_min_height(self) -> None:
        """根据直播间数量与每张卡片尺寸实时计算容器最小高度。

        高度 = FlowLayout.heightForWidth(容器宽度),FlowLayout 内部按卡片
        自身宽度换行(每行最多 2 张),并累加各行高度。因此房间数量变化或
        卡片预设(小/中/大)变化都会即时反映到容器高度上。
        """
        w = self._card_container.width()
        if w <= 0:
            # 容器尚未布局(如页面未显示),延迟到下一轮事件循环再算;
            # showEvent 会在页面可见后再次触发重算,避免空转
            if self._card_container.isVisible():
                QTimer.singleShot(0, self._update_card_container_min_height)
            return
        h = self._card_layout.heightForWidth(w)
        self._card_container.setMinimumHeight(max(h, 120))

    def _update_grid_columns(self) -> None:
        """计算「名义列数」,仅供方向键导航使用。

        实际换行由 FlowLayout 按每张卡片自身宽度完成,与该值无关。
        保留该方法以兼容响应式测试。
        """
        container = self._card_container
        if container is None:
            return
        card_width = 440
        if self._selected_room_id:
            selected_card = self._cards.get(self._selected_room_id)
            if selected_card and selected_card.width() > 0:
                card_width = selected_card.width()
        available = container.width() - _GRID_HMARGIN * 2
        new_columns = max(1, min(2, available // (card_width + _GRID_H_SPACING)))
        if new_columns != self._grid_columns:
            self._grid_columns = new_columns

    # ── Refresh ──────────────────────────────────────────────

    def _refresh_card(self, room_id: str) -> None:
        card = self._cards.get(room_id)
        if card:
            card.refresh()
            self._update_card_timeline(room_id)
        if self._selected_room_id == room_id:
            room = self._manager.get_room(room_id)
            self._detail.show_room(room)
            self._update_timeline()
        self._update_statusbar()

    def _refresh_all(self) -> None:
        for room_id, card in self._cards.items():
            card.refresh()
            self._update_card_timeline(room_id)
        if self._selected_room_id:
            room = self._manager.get_room(self._selected_room_id)
            self._detail.show_room(room)
        self._update_statusbar()
        self._sync_mute_all_button()

    def _update_card_timeline(self, room_id: str) -> None:
        room = self._manager.get_room(room_id)
        card = self._cards.get(room_id)
        if room is None or card is None:
            return
        duration = self._manager.get_preview_duration(room_id)
        if duration <= 0 and room.controller:
            duration = float(getattr(room.controller, "total_sec", 0) or 0)
        if duration <= 0:
            duration = self._room_video_duration(room)
        position = self._manager.get_preview_position(room_id)
        card.set_timeline_data(position, duration)

    def _refresh(self) -> None:
        has_cards = bool(self._cards)
        single_room = len(self._cards) <= 1
        self._empty_label.setVisible(not has_cards)
        if not has_cards:
            self._update_empty_label_geometry()
        self._card_container.setVisible(True)
        # 单房间降级：隐藏批量操作 UI（"全部静音"保留，单房间时仍有意义）
        self._batch_record_btn.setVisible(not single_room)
        self._batch_stop_btn.setVisible(not single_room)
        self._align_live_btn.setVisible(not single_room)
        self._update_room_limit_label()
        self._update_statusbar()
        self._sync_mute_all_button()

    def _update_empty_label_geometry(self) -> None:
        container = self._card_container
        if container is None:
            return
        margin = 40
        w = max(container.width() - margin * 2, 200)
        h = self._empty_label.sizeHint().height()
        x = (container.width() - w) // 2
        y = (container.height() - h) // 2
        self._empty_label.setGeometry(x, y, w, h)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._update_grid_columns()
        # 页面显示时容器宽度才有效,此时重算直播间区域高度
        self._schedule_container_resize()
        try:
            self._manager.global_tick.disconnect(self._on_global_tick)
        except RuntimeError:
            pass
        self._manager.global_tick.connect(self._on_global_tick)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        try:
            self._manager.global_tick.disconnect(self._on_global_tick)
        except RuntimeError:
            pass

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # 使用防抖定时器，拖拽窗口时仅在停止 150ms 后更新网格列数
        self._grid_debounce.start()
        # 容器宽度变化后实时重算直播间区域高度(防抖 60ms)
        self._resize_debounce.start()
        if self._empty_label.isVisible():
            self._update_empty_label_geometry()

    def _update_room_limit_label(self) -> None:
        count = self._manager.room_count()
        limit = self._manager.max_rooms()
        self._room_limit_label.setText(f"{count}/{limit}")

    def _update_statusbar(self) -> None:
        rooms = self._manager.list_rooms()
        self._statusbar.update_stats(
            total=len(rooms),
            connected=sum(1 for r in rooms if r.is_connected),
            recording=sum(1 for r in rooms if r.is_recording),
            previewing=sum(1 for r in rooms if r.preview_enabled and not r.preview_paused),
            errors=sum(1 for r in rooms if r.last_error),
        )

    def _refresh_theme(self) -> None:
        self._detail.refresh_theme()
        # 主题切换时若详情面板正在显示房间信息，则重建以刷新颜色
        if self._selected_room_id:
            self._detail.show_room(self._manager.get_room(self._selected_room_id))
        self._bottom_bar.refresh_theme()
        for card in self._cards.values():
            card.refresh_theme()
        self._clip_list.refresh_theme()
        self.update()

    @property
    def manager(self) -> MultiRoomManager:
        return self._manager

    # ── Keyboard navigation ────────────────────────────────────

    def keyPressEvent(self, event) -> None:
        key = event.key()
        mods = event.modifiers()
        if key == Qt.Key_Delete and self._selected_room_id:
            self._on_remove(self._selected_room_id)
        elif key == Qt.Key_Z and mods & Qt.ControlModifier:
            if self._undo.undo():
                desc = self._undo.redo_description()
                self._statusbar.show_message(f"已撤销: {desc}" if desc else "已撤销")
        elif key == Qt.Key_Y and mods & Qt.ControlModifier:
            if self._undo.redo():
                desc = self._undo.undo_description()
                self._statusbar.show_message(f"已重做: {desc}" if desc else "已重做")
        elif key == Qt.Key_Escape:
            # 取消多选，恢复单选当前房间
            if len(self._selected_room_ids) > 1:
                self._selected_room_ids = {self._selected_room_id} if self._selected_room_id else set()
                for rid, card in self._cards.items():
                    card.set_selected(rid in self._selected_room_ids)
                self._controls.set_sync_count(len(self._selected_room_ids))
        elif key in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
            self._navigate_cards(key)
        else:
            super().keyPressEvent(event)

    def _navigate_cards(self, key: int) -> None:
        """用方向键在卡片间导航。

        基于卡片实际渲染几何位置计算导航目标，避免硬编码列数与
        用户调整卡片大小后的实际排布不一致。
        """
        if not self._cards:
            return
        ordered = list(self._cards.keys())
        if self._selected_room_id is None:
            # 选中第一个
            self._on_room_selected(ordered[0])
            return

        current_card = self._cards.get(self._selected_room_id)
        if current_card is None:
            self._on_room_selected(ordered[0])
            return

        cur_center = current_card.geometry().center()
        best_idx = None
        best_score = None

        for idx, rid in enumerate(ordered):
            if rid == self._selected_room_id:
                continue
            card = self._cards.get(rid)
            if card is None:
                continue
            c = card.geometry().center()
            dx = c.x() - cur_center.x()
            dy = c.y() - cur_center.y()

            if key == Qt.Key_Right and dx > 0 and abs(dy) < current_card.height() // 2:
                score = dx + abs(dy)
            elif key == Qt.Key_Left and dx < 0 and abs(dy) < current_card.height() // 2:
                score = -dx + abs(dy)
            elif key == Qt.Key_Down and dy > 0 and abs(dx) < current_card.width() // 2:
                score = dy + abs(dx)
            elif key == Qt.Key_Up and dy < 0 and abs(dx) < current_card.width() // 2:
                score = -dy + abs(dx)
            else:
                continue

            if best_score is None or score < best_score:
                best_score = score
                best_idx = idx

        # 回退：若未找到符合条件的卡片，使用旧的顺序索引方式
        if best_idx is None:
            try:
                idx = ordered.index(self._selected_room_id)
            except ValueError:
                idx = 0
            if key == Qt.Key_Right:
                idx = min(idx + 1, len(ordered) - 1)
            elif key == Qt.Key_Left:
                idx = max(idx - 1, 0)
            elif key == Qt.Key_Down:
                idx = min(idx + max(1, self._grid_columns), len(ordered) - 1)
            elif key == Qt.Key_Up:
                idx = max(idx - max(1, self._grid_columns), 0)
            best_idx = idx

        if ordered[best_idx] != self._selected_room_id:
            self._on_room_selected(ordered[best_idx])
