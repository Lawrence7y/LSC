"""GUI tests for the multi-room workbench page and room cards."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from lsc.gui.multi_room.session import RoomSession


def _qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_room_card_reflects_basic_room_state() -> None:
    _qapp()

    from lsc.gui.components.room_card import RoomCard

    room = RoomSession(
        room_id="room-1",
        room_url="https://live.douyin.com/123",
        platform="douyin",
    )
    room.preview_muted = True
    room.is_connected = True

    card = RoomCard(room)

    assert "douyin" in card._platform_tag._text.lower()
    assert card._mute_btn.isChecked() is True
    assert "已连接" in card._status_text.text()


def test_room_card_emits_mute_toggle_for_own_room() -> None:
    _qapp()

    from lsc.gui.components.room_card import RoomCard

    room = RoomSession(room_id="room-1", room_url="https://live.douyin.com/123")
    card = RoomCard(room)
    hits: list[tuple[str, bool]] = []
    card.mute_toggled.connect(lambda room_id, muted: hits.append((room_id, muted)))

    card._mute_btn.click()

    assert hits[-1] == ("room-1", False)


def test_room_card_connecting_state() -> None:
    _qapp()

    from lsc.gui.components.room_card import RoomCard

    room = RoomSession(room_id="room-1", room_url="https://live.douyin.com/123")
    room.is_connecting = True
    card = RoomCard(room)
    card.refresh()

    assert card._connect_btn.text() == "连接中..."
    assert card._connect_btn.isEnabled() is False


def test_room_card_preview_widget_embedding() -> None:
    _qapp()

    from PySide6.QtWidgets import QSizePolicy, QWidget
    from lsc.gui.components.room_card import RoomCard

    room = RoomSession(room_id="room-1", room_url="https://live.douyin.com/123")
    card = RoomCard(room)

    preview = QWidget()
    card.set_preview_widget(preview)
    assert card._embedded_preview is preview
    assert preview.parent() is card._preview_area
    assert card._preview_placeholder.isHidden()
    assert preview.sizePolicy().horizontalPolicy() == QSizePolicy.Expanding
    assert preview.sizePolicy().verticalPolicy() == QSizePolicy.Expanding
    assert int(card._preview_layout.alignment()) == 0

    card.remove_preview_widget()
    assert card._embedded_preview is None
    assert card._preview_placeholder.isVisible() or not card._preview_placeholder.isHidden()


def test_room_card_rebinds_native_video_widget_after_embedding() -> None:
    _qapp()

    from PySide6.QtWidgets import QWidget
    from lsc.gui.components.room_card import RoomCard

    class NativePreview(QWidget):
        def __init__(self):
            super().__init__()
            self.rebound_to: list[int] = []

        def rebind_video_output(self):
            self.rebound_to.append(int(self.winId()))

    room = RoomSession(room_id="room-1", room_url="https://live.douyin.com/123")
    card = RoomCard(room)
    preview = NativePreview()

    card.set_preview_widget(preview)

    assert preview.rebound_to == [int(preview.winId())]


def test_room_card_has_preview_corner_fullscreen_and_timeline() -> None:
    _qapp()

    from lsc.gui.components.room_card import RoomCard

    room = RoomSession(room_id="room-1", room_url="https://live.douyin.com/123")
    card = RoomCard(room)
    fullscreen_hits: list[str] = []
    timeline_hits: list[tuple[str, float]] = []
    card.fullscreen_clicked.connect(fullscreen_hits.append)
    card.timeline_seek_requested.connect(lambda room_id, sec: timeline_hits.append((room_id, sec)))

    assert card._fullscreen_btn.parent() is card._preview_area
    assert card._timeline.objectName() == "roomCardTimeline"

    card._fullscreen_btn.click()
    card._timeline.seek_requested.emit(12.5)

    assert fullscreen_hits == ["room-1"]
    assert timeline_hits == [("room-1", 12.5)]


def test_room_card_preview_controls_sit_below_video() -> None:
    _qapp()

    from lsc.gui.components.room_card import RoomCard

    room = RoomSession(room_id="room-1", room_url="https://live.douyin.com/123")
    card = RoomCard(room)

    assert card._preview_controls.parent() is card
    assert card._preview_btn.parent() is card._preview_controls
    assert card._pause_btn.parent() is card._preview_controls
    assert card._mute_btn.parent() is card._preview_controls
    assert card._fullscreen_btn.parent() is card._preview_area
    assert card._preview_area._controls_widget is None


def test_room_card_preset_size_and_updates_preview_size() -> None:
    _qapp()

    from lsc.gui.components.room_card import RoomCard

    room = RoomSession(room_id="room-1", room_url="https://live.douyin.com/123")
    card = RoomCard(room)

    # 预设尺寸切换按钮
    assert hasattr(card, '_size_btn')
    assert hasattr(card, '_preset_index')

    # 应用大号预设
    card.set_preset(2)  # 大号: 560×300
    assert card.maximumWidth() == 560
    assert card.minimumWidth() == 560
    assert card._preview_area.height() == 300

    # 循环切换预设
    card.cycle_preset()  # 大→小
    assert card.maximumWidth() == 340

    # reset_card_width 恢复为默认预设(中号)
    card.reset_card_width()
    assert card.maximumWidth() == 440
    assert card._preview_area.height() == 200


def test_room_card_size_toggle_button() -> None:
    _qapp()

    from lsc.gui.components.room_card import RoomCard

    room = RoomSession(room_id="room-1", room_url="https://live.douyin.com/123")
    card = RoomCard(room)

    assert hasattr(card, '_size_btn')
    # 按钮默认隐藏,hover 时显示
    assert not card._size_btn.isVisible()


def test_room_card_preset_cycle() -> None:
    app = _qapp()

    from lsc.gui.components.room_card import RoomCard, _CARD_PRESETS

    room = RoomSession(room_id="room-1", room_url="https://live.douyin.com/123")
    card = RoomCard(room)

    # 依次切换所有预设
    for i, (name, w, h) in enumerate(_CARD_PRESETS):
        card.set_preset(i)
        assert card.maximumWidth() == w
        assert card._preview_area.height() == h


def test_room_card_mute_and_include_use_custom_checked_indicator() -> None:
    _qapp()

    from lsc.gui.components.room_card import RoomCard

    room = RoomSession(room_id="room-1", room_url="https://live.douyin.com/123")
    card = RoomCard(room)

    assert card._mute_btn.uses_custom_indicator is True
    assert card._include_cb.uses_custom_indicator is True
    assert card._mute_btn.isChecked() is True
    assert card._include_cb.isChecked() is True

    card._mute_btn.setChecked(False)
    card._include_cb.setChecked(False)

    assert card._mute_btn.isChecked() is False
    assert card._include_cb.isChecked() is False


def test_multi_room_fullscreen_preview_has_bottom_controls() -> None:
    _qapp()

    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QWidget
    from lsc.gui.pages.multi_room import MultiRoomPage
    from lsc.gui.multi_room.manager import MultiRoomManager

    class FakePreview(QWidget):
        def __init__(self):
            super().__init__()
            self.paused = []
            self.seeked = []
            self.muted = []

        def toggle_play_pause(self):
            self.paused.append(True)

        def seek_to(self, value):
            self.seeked.append(value)

        def set_muted(self, value):
            self.muted.append(value)

        def position_sec(self):
            return 5.0

        def duration_sec(self):
            return 20.0

    manager = MultiRoomManager(controller_factory=lambda: type("FakeCtrl", (), {"cleanup": lambda s: None})())
    page = MultiRoomPage(manager=manager)
    page._url_input.setText("https://live.douyin.com/123")
    page._on_add_room()
    room = manager.list_rooms()[0]
    card = page._cards[room.room_id]
    preview = FakePreview()
    room.preview_widget = preview
    room.preview_enabled = True
    card.set_preview_widget(preview)

    page._enter_fullscreen(room.room_id)

    # _fullscreen_window 现在是 FullscreenPreview 实例(封装了顶层窗口)
    assert page._fullscreen_window is not None
    fp = page._fullscreen_window
    assert fp.is_active()
    win = fp.window()
    assert win is not None
    # 内置极简播放条与进度条/按钮存在(通过 objectName 查询,不依赖内部属性)
    controls = win.findChild(QWidget, "fullscreenPlayerControls")
    assert controls is not None
    progress = win.findChild(QWidget, "fullscreenProgressSlider")
    exit_btn = win.findChild(QWidget, "fullscreenExitButton")
    minimize_btn = win.findChild(QWidget, "fullscreenMinimizeButton")
    play_btn = win.findChild(QWidget, "fullscreenPlayButton")
    assert progress is not None and exit_btn is not None
    assert minimize_btn is not None and play_btn is not None

    # 进度条 seek 委托到 widget.seek_to
    progress.setValue(10)
    assert preview.seeked[-1] == 10

    # Esc 退出全屏
    esc = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    win.keyPressEvent(esc)
    assert page._fullscreen_window is None


def test_multi_room_fullscreen_controls_auto_collapse_and_restore() -> None:
    _qapp()

    from PySide6.QtWidgets import QWidget
    from lsc.gui.pages.multi_room import MultiRoomPage
    from lsc.gui.multi_room.manager import MultiRoomManager

    manager = MultiRoomManager(controller_factory=lambda: type("FakeCtrl", (), {"cleanup": lambda s: None})())
    page = MultiRoomPage(manager=manager)
    page._url_input.setText("https://live.douyin.com/123")
    page._on_add_room()
    room = manager.list_rooms()[0]
    card = page._cards[room.room_id]
    preview = QWidget()
    room.preview_widget = preview
    room.preview_enabled = True
    card.set_preview_widget(preview)

    page._enter_fullscreen(room.room_id)
    fp = page._fullscreen_window
    win = fp.window()
    controls = win.findChild(QWidget, "fullscreenPlayerControls")
    assert controls is not None
    preview_size = preview.size()

    # 鼠标活动唤醒 → 自动隐藏:直接驱动 FullscreenPreview 的自动隐藏定时器
    assert controls.isVisible()
    fp._auto_hide_timer.stop()
    fp._auto_hide_timer.timeout.emit()  # 触发隐藏
    assert not controls.isVisible()
    assert preview.size() == preview_size  # 隐藏控制条不影响预览尺寸

    # 唤醒(模拟鼠标活动回调)
    fp._show_controls(controls)
    assert controls.isVisible()
    assert preview.size() == preview_size
    win.close()


def test_multi_room_fullscreen_escape_shortcut_is_registered() -> None:
    _qapp()

    from PySide6.QtGui import QShortcut
    from PySide6.QtWidgets import QWidget
    from lsc.gui.pages.multi_room import MultiRoomPage
    from lsc.gui.multi_room.manager import MultiRoomManager

    manager = MultiRoomManager(controller_factory=lambda: type("FakeCtrl", (), {"cleanup": lambda s: None})())
    page = MultiRoomPage(manager=manager)
    page._url_input.setText("https://live.douyin.com/123")
    page._on_add_room()
    room = manager.list_rooms()[0]
    card = page._cards[room.room_id]
    preview = QWidget()
    room.preview_widget = preview
    room.preview_enabled = True
    card.set_preview_widget(preview)

    page._enter_fullscreen(room.room_id)

    win = page._fullscreen_window.window()
    shortcuts = win.findChildren(QShortcut)
    assert shortcuts
    win.close()


def test_multi_room_page_can_be_instantiated_with_manager() -> None:
    _qapp()

    from lsc.gui.pages.multi_room import MultiRoomPage
    from lsc.gui.multi_room.manager import MultiRoomManager

    manager = MultiRoomManager()
    page = MultiRoomPage(manager=manager)

    assert page._manager is manager


def test_multi_room_page_exposes_output_dir_setting(tmp_path) -> None:
    _qapp()

    from PySide6.QtCore import QSettings
    from lsc.gui.pages.multi_room import MultiRoomPage
    from lsc.gui.multi_room.manager import MultiRoomManager

    settings = QSettings("LSC", "LiveStreamClipper")
    settings.clear()
    settings.setValue("output_dir", str(tmp_path / "saved"))

    manager = MultiRoomManager()
    page = MultiRoomPage(manager=manager)

    assert page._output_input.text() == str(tmp_path / "saved")

    chosen = tmp_path / "chosen"
    page._output_input.setText(str(chosen))
    output_dir, _encoder, _crf = page._get_recording_settings()

    assert output_dir == str(chosen)
    assert settings.value("output_dir") == str(chosen)


def test_multi_room_clip_list_moves_below_timeline_and_right_side_shows_record_settings() -> None:
    _qapp()

    from lsc.gui.pages.multi_room import MultiRoomPage
    from lsc.gui.multi_room.manager import MultiRoomManager

    page = MultiRoomPage(manager=MultiRoomManager())

    assert page._left_layout.indexOf(page._clip_card) > page._left_layout.indexOf(page._bottom_bar)
    assert page._clip_list.parent() is page._clip_card
    assert page._record_settings_card is not None
    assert page._record_quality.selected == "原画"
    assert page._record_encoder.selected == "H.264 NVENC"
    assert page._record_param.selected == "CRF 质量"
    assert page._record_start_btn.text() == "开始录制"


def test_multi_room_record_settings_are_forwarded_to_manager(tmp_path) -> None:
    _qapp()

    from lsc.gui.multi_room.manager import MultiRoomManager
    from lsc.platforms.base import StreamInfo

    calls: list[dict] = []

    class FakeController:
        stream_url = "https://example.com/live.m3u8"
        input_args = ["-headers", "Referer: https://example.com/\r\n"]

        def start_recording_with_crf(self, stream_url, output_dir, encoder, crf, **kwargs):
            calls.append(
                {
                    "stream_url": stream_url,
                    "output_dir": output_dir,
                    "encoder": encoder,
                    "crf": crf,
                    "kwargs": kwargs,
                }
            )
            return True, str(tmp_path / "recording.mp4"), encoder, ""

    manager = MultiRoomManager(controller_factory=FakeController)
    room = manager.add_room("https://live.douyin.com/123")
    assert room is not None
    room.is_connected = True

    def fake_parse_stream(url: str, *, force_refresh: bool = False):
        assert force_refresh is True
        return StreamInfo(
            platform="douyin",
            room_url=url,
            stream_url="https://example.com/live.m3u8",
            is_live=True,
            headers={"Referer": "https://example.com/"},
        )

    import lsc.gui.multi_room.manager as manager_module
    old_parse_stream = manager_module.parse_stream
    manager_module.parse_stream = fake_parse_stream

    try:
        ok = manager.start_recording(
            room.room_id,
            str(tmp_path),
            "H.264 CPU",
            31,
            param_mode="码率限制",
            bitrate="5000",
            bitrate_unit="kbps",
        )
    finally:
        manager_module.parse_stream = old_parse_stream

    assert ok is True
    assert calls[-1]["kwargs"]["param_mode"] == "码率限制"
    assert calls[-1]["kwargs"]["bitrate"] == "5000"
    assert calls[-1]["kwargs"]["bitrate_unit"] == "kbps"
    assert calls[-1]["kwargs"]["input_args"] == ["-headers", "Referer: https://example.com/\r\n"]


def test_detail_panel_uses_record_info_grid_labels() -> None:
    _qapp()

    from PySide6.QtWidgets import QLabel
    from lsc.gui.pages.multi_room import DetailPanel

    room = RoomSession(room_id="room-1", room_url="https://live.douyin.com/123")
    room.selected_quality = "原画"
    room.record_size_mb = 7.5
    panel = DetailPanel()
    panel.show_room(room)

    labels = [w.text() for w in panel.findChildren(QLabel) if w.objectName() == "info_label"]

    assert labels == ["分辨率", "帧率", "编码", "编码参数", "文件大小", "输出路径", "分析结果", "结果文件"]


def test_multi_room_page_add_room_and_refresh_grid() -> None:
    _qapp()

    from lsc.gui.pages.multi_room import MultiRoomPage
    from lsc.gui.multi_room.manager import MultiRoomManager

    manager = MultiRoomManager(controller_factory=lambda: type("FakeCtrl", (), {"cleanup": lambda s: None})())
    page = MultiRoomPage(manager=manager)

    page._url_input.setText("https://live.douyin.com/123")
    page._on_add_room()

    assert len(page._cards) == 1
    assert manager.room_count() == 1


def test_multi_room_page_room_limit_enforced() -> None:
    _qapp()

    from lsc.gui.pages.multi_room import MultiRoomPage
    from lsc.gui.multi_room.manager import MAX_ROOMS, MultiRoomManager

    manager = MultiRoomManager(controller_factory=lambda: type("FakeCtrl", (), {"cleanup": lambda s: None})())
    page = MultiRoomPage(manager=manager)

    # Fill up to limit
    for i in range(MAX_ROOMS):
        page._url_input.setText(f"https://live.douyin.com/{i}")
        page._on_add_room()

    assert manager.room_count() == MAX_ROOMS

    # Try to add one more
    page._url_input.setText(f"https://live.douyin.com/{MAX_ROOMS}")
    page._on_add_room()

    # Should still be at limit
    assert manager.room_count() == MAX_ROOMS


def test_start_preview_does_not_play_before_reparent() -> None:
    """Regression: 预览的 mpv.play 必须在 widget reparent/rebind 之后触发。

    早期实现中 ``start_preview`` 在 widget 刚创建、尚未嵌入卡片时就调
    ``play_live``，随后 reparent 改变 HWND 让首帧渲染丢失，表现为黑屏。
    本测试断言：``start_preview`` 不直接播放，``play_preview_stream`` 才播放。
    """
    _qapp()

    from lsc.gui.multi_room.manager import MultiRoomManager

    play_calls: list[str] = []

    class FakeWidget:
        def __init__(self):
            self._muted = True

        def is_available(self):
            return True

        def set_stream_headers(self, headers):
            pass

        def play_live(self, url):
            play_calls.append(url)

        def set_muted(self, muted):
            self._muted = muted

    manager = MultiRoomManager(
        controller_factory=lambda: type("FakeCtrl", (), {"cleanup": lambda s: None})(),
        preview_factory=FakeWidget,
    )
    room = manager.add_room("https://live.douyin.com/123")
    assert room is not None
    # 模拟连接成功（绕过网络）
    room.is_connected = True
    from lsc.platforms.base import StreamInfo

    info = StreamInfo(
        platform="douyin",
        room_url="https://live.douyin.com/123",
        stream_url="https://cdn.example.com/live.flv",
        is_live=True,
    )
    room.apply_stream_info(info)

    # start_preview 只建 widget + 置状态，不应触发播放
    ok = manager.start_preview(room.room_id)
    assert ok is True
    assert play_calls == [], "start_preview 不应在 reparent 前播放"

    # play_preview_stream 才真正播放
    manager.play_preview_stream(room.room_id)
    assert play_calls == ["https://cdn.example.com/live.flv"]


def test_clip_list_undo_redo_for_add_remove_clear() -> None:
    """Regression: 切片增删/清空可通过 UndoStack 撤销重做。"""
    _qapp()

    from lsc.gui.components.clip_list import ClipListWidget
    from lsc.gui.undo import UndoStack

    stack = UndoStack(limit=50)
    clip_list = ClipListWidget()
    clip_list.set_undo_stack(stack)

    idx = clip_list.add_segment(1.0, 5.0)
    assert idx == 0
    assert clip_list.count() == 1

    # 撤销添加 -> 应清空
    assert stack.undo() is True
    assert clip_list.count() == 0
    # 重做 -> 恢复
    assert stack.redo() is True
    assert clip_list.count() == 1

    # 添加第二段后删除第一段，再撤销删除
    clip_list.add_segment(6.0, 10.0)
    assert clip_list.count() == 2
    clip_list.remove_segment(0)
    assert clip_list.count() == 1
    assert stack.undo() is True  # 撤销删除
    assert clip_list.count() == 2

    # 清空并撤销
    clip_list.clear()
    assert clip_list.count() == 0
    assert stack.undo() is True
    assert clip_list.count() == 2


def test_dashboard_page_emits_multi_room_navigation_request() -> None:
    _qapp()

    from lsc.gui.pages.dashboard import DashboardPage

    page = DashboardPage()
    hits: list[bool] = []
    page.multi_room_requested.connect(lambda: hits.append(True))

    page._multi_room_btn.click()

    assert hits == [True]


def test_room_card_has_no_front_screen_or_popup_entry_points() -> None:
    _qapp()

    from lsc.gui.components.room_card import RoomCard

    room = RoomSession(room_id="room-1", room_url="https://live.douyin.com/123")
    card = RoomCard(room)

    assert not hasattr(card, "_front_btn")
    assert not hasattr(card, "front_screen_clicked")


def test_multi_room_page_responsive_grid_switches_to_one_column() -> None:
    _qapp()

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from lsc.gui.pages.multi_room import MultiRoomPage
    from lsc.gui.multi_room.manager import MultiRoomManager

    manager = MultiRoomManager(controller_factory=lambda: type("FakeCtrl", (), {"cleanup": lambda s: None})())
    page = MultiRoomPage(manager=manager)

    # Add two rooms
    page._url_input.setText("https://live.douyin.com/1")
    page._on_add_room()
    page._url_input.setText("https://live.douyin.com/2")
    page._on_add_room()

    # Wide viewport should allow two columns
    page.resize(1440, 900)
    page._card_container.resize(1000, 600)
    page._update_grid_columns()
    QApplication.processEvents()
    assert page._grid_columns == 2

    # Narrow viewport should switch to one column
    page._card_container.resize(600, 600)
    page._update_grid_columns()
    QApplication.processEvents()
    assert page._grid_columns == 1
    assert page._page_scroll.widget() is page._page_body
    assert page._splitter.parent() is page._page_body
    assert page._right_scroll.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff


def test_detail_panel_does_not_nest_inner_scroll_area() -> None:
    _qapp()

    from PySide6.QtWidgets import QScrollArea
    from lsc.gui.pages.multi_room import DetailPanel

    panel = DetailPanel()

    assert panel.findChildren(QScrollArea) == []


def test_multi_room_page_many_rooms_keep_distinct_card_geometries() -> None:
    _qapp()

    from PySide6.QtWidgets import QApplication
    from lsc.gui.pages.multi_room import MultiRoomPage
    from lsc.gui.multi_room.manager import MultiRoomManager

    page = MultiRoomPage(manager=MultiRoomManager())
    for i in range(6):
        page._url_input.setText(f"https://live.douyin.com/{i}")
        page._on_add_room()

    page.resize(1440, 1000)
    page.show()
    QApplication.processEvents()

    rects = [card.geometry() for card in page._cards.values()]

    assert len(rects) == 6
    assert len({(r.x(), r.y(), r.width(), r.height()) for r in rects}) == len(rects)


def test_added_room_card_becomes_visible_without_page_switch() -> None:
    """Regression: a newly added RoomCard must be laid out by FlowLayout immediately.

    Root cause: a freshly created QWidget is hidden by default.
    ``FlowLayout.doLayout`` filters items with ``not it.isEmpty()``, and
    ``QWidgetItem.isEmpty()`` returns ``widget->isHidden()`` — so a hidden new
    card is skipped by the layout, never placed, and stays at its initial
    (0,0,440,30) geometry until a page switch fires Qt's show cascade (which
    flips isHidden to False). The fix is an explicit ``card.show()`` in
    ``_add_card``.

    This test verifies the mechanism directly: after adding a room on a shown
    page, the new card must NOT be hidden, and FlowLayout's heightForWidth
    must account for it (height grows when a second card is added).
    """
    from PySide6.QtWidgets import QApplication
    from lsc.gui.pages.multi_room import MultiRoomPage
    from lsc.gui.multi_room.manager import MultiRoomManager

    _qapp()
    page = MultiRoomPage(
        manager=MultiRoomManager(
            controller_factory=lambda: type("FakeCtrl", (), {"cleanup": lambda s: None})()
        )
    )
    page.resize(1440, 900)
    page.show()
    QApplication.processEvents()

    # Add first room.
    page._url_input.setText("https://live.douyin.com/1")
    page._on_add_room()
    QApplication.processEvents()

    first_id = next(iter(page._cards))
    first_card = page._cards[first_id]
    assert not first_card.isHidden(), "first card must be shown after _add_card"
    width = max(340, page._card_container.width())
    height_one = page._card_layout.heightForWidth(width)

    # Add second room — must increase the layout's wrapped height.
    page._url_input.setText("https://live.douyin.com/2")
    page._on_add_room()
    QApplication.processEvents()

    second_id = next(rid for rid in page._cards if rid != first_id)
    second_card = page._cards[second_id]
    # The core assertion: the new card is not hidden, so FlowLayout does NOT
    # skip it via isEmpty().
    assert not second_card.isHidden(), (
        "newly added card is hidden → FlowLayout.isEmpty() skips it → card never laid out. "
        "Fix: call card.show() in _add_card."
    )
    # Verify FlowLayout actually accounts for the 2nd card: at a width that
    # only fits one 440px card, two cards must wrap to two rows, so the
    # wrapped height with two cards must exceed one card's row height.
    narrow_w = 460  # fits one 440 card per row
    height_two = page._card_layout.heightForWidth(narrow_w)
    # A single card row is ~375px; two stacked rows must be noticeably larger.
    assert height_two > height_one, (
        f"FlowLayout heightForWidth({narrow_w})={height_two} did not grow beyond "
        f"single-row {height_one}; the 2nd card was skipped by doLayout."
    )


