"""LSC - LiveStreamClipper 直播切片系统启动入口。"""
from __future__ import annotations

import sys
import os
import logging
import logging.handlers
import traceback

_root = os.path.dirname(os.path.abspath(__file__))
if _root not in sys.path:
    sys.path.insert(0, _root)

from PySide6.QtCore import QSettings, Qt, QTimer, qInstallMessageHandler, QtMsgType
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from lsc.gui.components.sidebar import Sidebar
from lsc.gui.components.widgets import ToastManager
from lsc.gui.pages.dashboard import DashboardPage
from lsc.gui.pages.multi_room import MultiRoomPage
from lsc.gui.pages.settings import SettingsPage
from lsc.gui.main_window import _apply_saved_theme
from lsc.gui.theme import connect_theme_changed, get_theme, generate_stylesheet, is_dark, set_dark


PAGE_MAP = {
    "dashboard": 0,
    "workbench": 1,
    "settings": 2,
}

# 全局 Toast 管理器引用，供各页面通过 show_toast() 调用。
_toast_manager: ToastManager | None = None


def _setup_logging() -> None:
    """配置全局日志：控制台 + 滚动文件，确保崩溃信息可追溯。

    文件日志写入项目根目录下的 ``logs/lsc.log``，单文件 2MB，保留 5 个备份。
    在 ``lsc.__init__`` 的基础配置上追加文件 handler，不覆盖已有配置。
    """
    log_dir = os.path.join(_root, "logs")
    try:
        os.makedirs(log_dir, exist_ok=True)
    except OSError:
        return  # 日志目录创建失败不阻断启动

    log_path = os.path.join(log_dir, "lsc.log")
    try:
        file_handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
    except (OSError, PermissionError):
        return  # 无写入权限不阻断启动

    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root_logger = logging.getLogger()
    # 避免重复添加
    has_file = any(
        isinstance(h, logging.handlers.RotatingFileHandler)
        and getattr(h, "baseFilename", "") == file_handler.baseFilename
        for h in root_logger.handlers
    )
    if not has_file:
        root_logger.addHandler(file_handler)


def _install_exception_hook() -> None:
    """安装全局未捕获异常钩子，将崩溃信息写入日志而非静默丢失。

    保留默认钩子的终端输出行为，但在退出前确保日志被 flush。
    """
    _log = logging.getLogger("lsc.crash")

    def _excepthook(exc_type, exc_value, exc_tb):
        # KeyboardInterrupt 属正常退出，不记为崩溃
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        _log.critical(
            "未捕获的异常: %s",
            "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
        )
        # 同时输出到终端（默认行为），便于开发期调试
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook


def _install_qt_message_handler() -> None:
    """捕获 Qt 内部警告（如信号连接类型错误），写入日志。

    避免 Qt 层面的错误（如之前全屏静音按钮的 TypeError）被静默吞掉。
    """

    _log = logging.getLogger("lsc.qt")

    def _handler(msg_type, context, message):
        level = logging.WARNING
        if msg_type == QtMsgType.QtDebugMsg:
            level = logging.DEBUG
        elif msg_type == QtMsgType.QtInfoMsg:
            level = logging.INFO
        elif msg_type == QtMsgType.QtWarningMsg:
            level = logging.WARNING
        elif msg_type in (QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
            level = logging.ERROR
        _log.log(level, message)

    qInstallMessageHandler(_handler)


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
        self.resize(1520, 920)
        self.setMinimumSize(1360, 800)

        try:
            from PySide6.QtGui import QIcon
            icon = QIcon()
            self.setWindowIcon(icon)
        except Exception:
            pass

        central = QWidget(self)
        central.setObjectName("mainCentralWidget")
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._sidebar = Sidebar()
        root.addWidget(self._sidebar)

        content_container = QWidget()
        content_container.setObjectName("contentContainer")
        content_layout = QVBoxLayout(content_container)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        root.addWidget(content_container, 1)

        self._stack = QStackedWidget()
        content_layout.addWidget(self._stack, 1)

        self._dashboard = DashboardPage()
        self._multi_room = MultiRoomPage()
        self._settings = SettingsPage()

        self._stack.addWidget(self._dashboard)
        self._stack.addWidget(self._multi_room)
        self._stack.addWidget(self._settings)

        self._status = QStatusBar(self)
        self.setStatusBar(self._status)
        self._status.showMessage("就绪")
        self._status.setMaximumHeight(28)

        self._sidebar.page_changed.connect(self._on_page_changed)
        self._dashboard.navigate_to.connect(self._on_navigate)

        global _toast_manager
        self._toast_manager = ToastManager(central)
        _toast_manager = self._toast_manager

        self._apply_theme()
        connect_theme_changed(self._apply_theme)
        self._install_shortcuts()
        self._dash_refresh_timer = QTimer(self)
        self._dash_refresh_timer.setInterval(5000)
        self._dash_refresh_timer.timeout.connect(self._refresh_dashboard)
        self._dash_refresh_timer.start()
        self._refresh_dashboard()

    def _on_page_changed(self, page_key: str) -> None:
        idx = PAGE_MAP.get(page_key, 0)
        self._stack.setCurrentIndex(idx)
        titles = {"dashboard": "仪表盘", "workbench": "多房间工作台", "settings": "设置"}
        self._status.showMessage(titles.get(page_key, ""))
        if page_key == "dashboard":
            self._refresh_dashboard()

    def _on_navigate(self, page_key: str) -> None:
        """处理来自仪表盘等内部页面的导航请求。"""
        idx = PAGE_MAP.get(page_key, 0)
        self._stack.setCurrentIndex(idx)
        # 同步更新侧栏导航按钮选中状态
        self._sidebar.set_current_page(page_key)
        titles = {"dashboard": "仪表盘", "workbench": "多房间工作台", "settings": "设置"}
        self._status.showMessage(titles.get(page_key, ""))

    def _apply_theme(self) -> None:
        # 不再设置 MainWindow 的样式表，避免与应用程序全局样式表冲突
        pass

    def _install_shortcuts(self) -> None:
        """注册全局快捷键:Ctrl+1..3 切页,Ctrl+T 切主题。"""
        for idx, key in enumerate(("dashboard", "workbench", "settings")):
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
        self._dashboard.set_sessions(sessions)

    def closeEvent(self, event):
        """关闭窗口时清理所有资源，防止 FFmpeg 孤儿进程和资源泄漏。"""
        import logging
        _log = logging.getLogger(__name__)

        # 1. 停止所有多房间录制和预览
        try:
            manager = self._multi_room.manager
            for room in manager.list_rooms():
                # 录制中或重连等待中都需要停止，避免 FFmpeg 进程泄漏
                if room.is_recording or room.is_reconnect_pending:
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

        # 2. 清理 Toast
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
    # 先配置日志和异常钩子，确保启动早期的错误也能被捕获
    _setup_logging()
    _install_exception_hook()
    _log = logging.getLogger("lsc.main")
    _log.info("LSC 启动中...")

    app = QApplication(sys.argv)
    _install_qt_message_handler()
    app.setStyle("Fusion")
    app.setApplicationName("LSC")
    app.setApplicationDisplayName("直播切片系统")
    # 设置全局默认字体族(保留系统字号,使其随 DPI 缩放),确保中文回退正确。
    default_font = app.font()
    default_font.setFamilies(["SF Pro Display", "SF Pro Text", "Helvetica Neue", "PingFang SC", "Noto Sans SC", "Microsoft YaHei UI", "Microsoft YaHei", "SimHei"])
    default_font.setStyleHint(QFont.StyleHint.SansSerif)
    app.setFont(default_font)
    _apply_saved_theme()
    app.styleHints().colorSchemeChanged.connect(_on_system_color_scheme_changed)
    window = MainWindow()

    window.show()

    def _ensure_button_styled_background():
        """Ensure all QPushButtons have WA_StyledBackground set so QSS background
        colors paint correctly on Windows (transparent parent workaround)."""
        from PySide6.QtWidgets import QPushButton
        for w in app.allWidgets():
            if isinstance(w, QPushButton):
                w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
                w.style().unpolish(w)
                w.style().polish(w)

    QTimer.singleShot(500, _ensure_button_styled_background)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
