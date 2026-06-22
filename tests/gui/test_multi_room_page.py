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


def test_room_card_exposes_resize_handle_and_updates_preview_size() -> None:
    _qapp()

    from lsc.gui.components.room_card import RoomCard

    room = RoomSession(room_id="room-1", room_url="https://live.douyin.com/123")
    card = RoomCard(room)

    assert card._resize_handle.objectName() == "roomCardResizeHandle"
    assert card._resize_handle.cursor().shape().name == "SizeFDiagCursor"

    card.set_card_size(520, 210)

    assert card.maximumWidth() == 520
    assert card.minimumWidth() <= 520
    assert card._preview_area.height() == 210


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

    assert page._fullscreen_window is not None
    controls = page._fullscreen_window.findChild(QWidget, "fullscreenPlayerControls")
    assert controls is not None
    layout = page._fullscreen_window.layout()
    assert layout.indexOf(page._fullscreen_surface) == 0
    assert layout.indexOf(controls) == -1
    assert controls.parent() is page._fullscreen_surface
    assert page._fullscreen_progress.objectName() == "fullscreenProgressSlider"
    assert page._fullscreen_exit_btn.objectName() == "fullscreenExitButton"
    assert page._fullscreen_minimize_btn.objectName() == "fullscreenMinimizeButton"
    assert page._fullscreen_play_btn.text() == ""
    assert page._fullscreen_exit_btn.text() == ""
    assert page._fullscreen_minimize_btn.text() == ""
    assert page._fullscreen_play_btn.icon_kind() == "pause"
    assert page._fullscreen_exit_btn.icon_kind() == "exit_fullscreen"
    assert page._fullscreen_minimize_btn.icon_kind() == "minimize"
    assert page._fullscreen_progress.maximumHeight() <= 16

    page._fullscreen_play_btn.click()
    page._fullscreen_mute_btn.click()
    page._fullscreen_progress.setValue(10)

    assert preview.paused == [True]
    assert preview.muted[-1] is False
    assert preview.seeked[-1] == 10

    esc = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    page._fullscreen_window.keyPressEvent(esc)
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
    controls = page._fullscreen_controls
    preview_size = preview.size()

    assert controls.isVisible()
    page._fullscreen_hide_controls()
    assert not controls.isVisible()
    assert controls.maximumHeight() == page._fullscreen_controls_height
    assert preview.size() == preview_size

    page._fullscreen_show_controls()
    assert controls.isVisible()
    assert controls.maximumHeight() == page._fullscreen_controls_height
    assert preview.size() == preview_size
    page._fullscreen_window.close()


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

    shortcuts = page._fullscreen_window.findChildren(QShortcut)
    assert shortcuts
    page._fullscreen_window.close()


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
    page._scroll.viewport().resize(1000, 600)
    page._update_grid_columns()
    QApplication.processEvents()
    assert page._grid_columns == 2

    # Narrow viewport should switch to one column
    page._scroll.viewport().resize(600, 600)
    page._update_grid_columns()
    QApplication.processEvents()
    assert page._grid_columns == 1
