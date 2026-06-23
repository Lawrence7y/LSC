"""LSC - LiveStreamClipper 直播切片系统启动入口。"""
from __future__ import annotations

import sys
import os

_root = os.path.dirname(os.path.abspath(__file__))
if _root not in sys.path:
    sys.path.insert(0, _root)

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QStackedWidget,
    QStatusBar,
    QWidget,
)

from lsc.gui.components.sidebar import Sidebar
from lsc.gui.components.widgets import ToastManager
from lsc.gui.pages.dashboard import DashboardPage
from lsc.gui.pages.multi_room import MultiRoomPage
from lsc.gui.pages.record import RecordPage
from lsc.gui.pages.settings import SettingsPage
from lsc.gui.main_window import _apply_saved_theme
from lsc.gui.theme import connect_theme_changed, get_theme, generate_stylesheet, is_dark, set_dark


PAGE_MAP = {
    "dashboard": 0,
    "workbench": 1,
    "record": 2,
    "settings": 3,
}

# 全局 Toast 管理器引用，供各页面通过 show_toast() 调用。
_toast_manager: ToastManager | None = None


def fmt_dashboard_elapsed(started_at) -> str:
    """格式化仪表盘最近动态的录制时长。"""
    from datetime import datetime
    try:
        elapsed = (datetime.now() - started_at).total_seconds()
    except Exception:
        return ""
    s = max(0, int(elapsed))
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if h:
        return f"录制中 {h:02d}:{m:02d}:{s:02d}"
    return f"录制中 {m:02d}:{s:02d}"


def show_toast(
    message: str,
    *,
    toast_type: str = "info",
    title: str = "",
    duration_ms: int = 0,
):
    """全局快捷函数：显示一个 Toast 通知。

    在 MainWindow 初始化后可用。若 ToastManager 未就绪则静默忽略，
    避免在启动早期或测试环境中报错。
    返回 Toast 对象（可用 add_action 添加动作按钮），或 None。
    """
    global _toast_manager
    if _toast_manager is None:
        return None
    return _toast_manager.show(
        message, toast_type=toast_type, title=title, duration_ms=duration_ms
    )


class MainWindow(QMainWindow):
    """LSC 主窗口。"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("LSC - 直播切片系统")
        self.resize(1440, 900)

        central = QWidget(self)
        central.setObjectName("mainCentralWidget")
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._sidebar = Sidebar()
        root.addWidget(self._sidebar)

        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)

        self._dashboard = DashboardPage()
        self._multi_room = MultiRoomPage()
        self._record = RecordPage()
        self._settings = SettingsPage()

        self._stack.addWidget(self._dashboard)
        self._stack.addWidget(self._multi_room)
        self._stack.addWidget(self._record)
        self._stack.addWidget(self._settings)

        self._status = QStatusBar(self)
        self.setStatusBar(self._status)
        self._status.showMessage("就绪")

        self._sidebar.page_changed.connect(self._on_page_changed)
        # Dashboard 内部按钮可以直接导航到其他页面
        self._dashboard.navigate_to.connect(self._on_navigate)

        # Toast 通知管理器：挂载到 central widget，浮于所有页面之上
        global _toast_manager
        self._toast_manager = ToastManager(central)
        _toast_manager = self._toast_manager

        self._apply_theme()
        connect_theme_changed(self._apply_theme)
        self._install_shortcuts()
        # 仪表盘统计与最近动态刷新:切页时 + 每 5 秒(轻量,仅读内存状态)
        self._dash_refresh_timer = QTimer(self)
        self._dash_refresh_timer.setInterval(5000)
        self._dash_refresh_timer.timeout.connect(self._refresh_dashboard)
        self._dash_refresh_timer.start()
        self._refresh_dashboard()

    def _on_page_changed(self, page_key: str) -> None:
        idx = PAGE_MAP.get(page_key, 0)
        self._stack.setCurrentIndex(idx)
        titles = {"dashboard": "仪表盘", "workbench": "多房间工作台", "record": "直播录制", "settings": "设置"}
        self._status.showMessage(titles.get(page_key, ""))
        if page_key == "dashboard":
            self._refresh_dashboard()

    def _on_navigate(self, page_key: str) -> None:
        """处理来自仪表盘等内部页面的导航请求。"""
        idx = PAGE_MAP.get(page_key, 0)
        self._stack.setCurrentIndex(idx)
        # 同步更新侧栏导航按钮选中状态
        self._sidebar.set_current_page(page_key)
        titles = {"dashboard": "仪表盘", "workbench": "多房间工作台", "record": "直播录制", "settings": "设置"}
        self._status.showMessage(titles.get(page_key, ""))

    def _apply_theme(self) -> None:
        self.setStyleSheet(generate_stylesheet(get_theme(), dark=is_dark()))

    def _install_shortcuts(self) -> None:
        """注册全局快捷键:Ctrl+1..4 切页,Ctrl+T 切主题。"""
        for idx, key in enumerate(("dashboard", "workbench", "record", "settings")):
            sc = QShortcut(QKeySequence(f"Ctrl+{idx + 1}"), self)
            sc.activated.connect(lambda k=key: self._on_navigate(k))
        sc_theme = QShortcut(QKeySequence("Ctrl+T"), self)
        sc_theme.activated.connect(self._sidebar._on_theme_toggle)

    def _refresh_dashboard(self) -> None:
        """从多房间管理器读取实时状态,刷新仪表盘统计卡与导航徽标。"""
        manager = getattr(self._multi_room, "manager", None)
        rooms = manager.list_rooms() if manager is not None else []
        recording = sum(1 for r in rooms if r.is_recording)
        connected = sum(1 for r in rooms if r.is_connected)
        errors = sum(1 for r in rooms if r.last_error)
        clip_count = 0
        clip_list = getattr(self._multi_room, "_clip_list", None)
        if clip_list is not None:
            clip_count = clip_list.count()
        # Also count RecordPage's recording state
        record_ctrl = getattr(self._record, "_ctrl", None)
        if record_ctrl is not None and getattr(record_ctrl, "is_recording", False):
            recording += 1
        self._dashboard.update_stats(recording, connected, clip_count)
        self._sidebar.update_workbench_badge(recording, errors)
        # 用当前房间状态填充「最近动态」,让仪表盘不再是空壳
        sessions = []
        for r in rooms:
            title = r.streamer_name or r.stream_title or r.platform_name or r.room_url
            if r.is_recording:
                status = "recording"
            elif r.is_connected:
                status = "connected"
            else:
                status = r.status_text() or "未连接"
            dur = ""
            if r.is_recording and r.record_started_at is not None:
                from datetime import datetime
                dur = fmt_dashboard_elapsed(r.record_started_at)
            sessions.append({
                "title": title,
                "status": status,
                "duration_text": dur or (r.friendly_error if r.last_error else "—"),
                "path": r.record_output_path or r.room_url,
            })
        # Add RecordPage's recording if active
        if record_ctrl is not None and getattr(record_ctrl, "is_recording", False):
            from datetime import datetime
            record_page = self._record
            page_url = getattr(record_ctrl, "page_url", "") or ""
            streamer = getattr(record_ctrl, "last_stream_info", None)
            streamer_name = getattr(streamer, "streamer", "") if streamer else ""
            title = streamer_name or page_url or "直播录制"
            dur = ""
            start_mono = getattr(record_ctrl, "record_start_mono", 0)
            if start_mono > 0:
                dur = fmt_dashboard_elapsed(datetime.fromtimestamp(
                    datetime.now().timestamp() - (getattr(record_ctrl, "total_sec", 0) or 0)
                ))
            sessions.append({
                "title": f"[录制页] {title}",
                "status": "recording",
                "duration_text": dur or "—",
                "path": getattr(record_ctrl, "video_path", "") or page_url,
            })
        self._dashboard.set_sessions(sessions)

    def closeEvent(self, event):
        """关闭窗口时清理所有资源，防止 FFmpeg 孤儿进程和资源泄漏。"""
        import logging
        _log = logging.getLogger(__name__)

        # 1. 停止所有多房间录制和预览
        try:
            manager = self._multi_room.manager
            for room in manager.list_rooms():
                if room.is_recording:
                    _log.info("Stopping recording for room %s", room.room_id)
                    manager.stop_recording(room.room_id)
                if room.preview_widget is not None:
                    cleanup = getattr(room.preview_widget, "cleanup", None)
                    if callable(cleanup):
                        try:
                            cleanup()
                        except Exception:
                            pass
        except Exception as exc:
            _log.warning("Error cleaning up multi-room: %s", exc)

        # 2. 清理录制页
        try:
            record_ctrl = getattr(self._record, "_ctrl", None)
            if record_ctrl is not None:
                cleanup = getattr(record_ctrl, "cleanup", None)
                if callable(cleanup):
                    cleanup()
            preview = getattr(self._record, "_preview", None)
            if preview is not None:
                cleanup = getattr(preview, "cleanup", None)
                if callable(cleanup):
                    cleanup()
        except Exception as exc:
            _log.warning("Error cleaning up record page: %s", exc)

        # 3. 清理 Toast
        if self._toast_manager is not None:
            self._toast_manager.clear()

        super().closeEvent(event)


def _on_system_color_scheme_changed() -> None:
    """当用户选择"跟随系统"时，响应操作系统主题变化。"""
    settings = QSettings("LSC", "LiveStreamClipper")
    if settings.value("theme", "深色") != "跟随系统":
        return
    set_dark(SettingsPage._system_prefers_dark())


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("LSC")
    app.setApplicationDisplayName("直播切片系统")
    # 设置全局默认字体族(保留系统字号,使其随 DPI 缩放),确保中文回退正确。
    default_font = app.font()
    default_font.setFamilies(["Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Noto Sans SC", "SimHei"])
    default_font.setStyleHint(QFont.StyleHint.SansSerif)
    app.setFont(default_font)
    _apply_saved_theme()
    app.styleHints().colorSchemeChanged.connect(_on_system_color_scheme_changed)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
