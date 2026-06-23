"""
Record page - 1:1 replica of ui-design-prototype.html
"""

import math
import os
import time as _time
from datetime import datetime

from lsc import get_logger
_log = get_logger(__name__)

from PySide6.QtCore import (
    QSettings,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from lsc.utils.helpers import fmt_time as _fmt_time

from lsc.gui.components.control_bar import ControlBar as SharedControlBar
from lsc.gui.components.fullscreen_preview import FullscreenPreview
from lsc.gui.components.mpv_widget import MpvWidget
from lsc.gui.components.preview_surface import PreviewSurface
from lsc.gui.components.timeline import InlineTimeline as SharedTimeline
from lsc.gui.components.widgets import Card, ChipGroup, EmptyState, FadeInWidget, InputField, ParamPanel
from lsc.gui.theme import get_option_button_palette, get_theme, is_dark
from lsc.gui.pages.recording_controller import RecordingController
from .video_preview import VideoPreview
from .icon_widgets import (
    IconButton,
    ExportedCard,
    ExportedClipsGrid,
    AnalysisResultsGrid,
    _icon_seek_back,
    _icon_seek_fwd,
    _icon_stop,
    _icon_play,
    _icon_pause,
)
from .config_panel import ConfigPanel

_ESTIMATED_MB_PER_SEC = 0.45  # Rough estimate for 1080p H.264


class RecordPage(QWidget):
    """Record page — UI layer only. Business logic lives in RecordingController."""

    status_changed = Signal(str, str)  # text, type
    stats_changed = Signal(int, int, int)  # active recordings, seconds, exported clips

    def __init__(self, parent=None):
        super().__init__(parent)
        # Delegate all business logic to the controller
        self._ctrl = RecordingController()
        self._ctrl.timer.timeout.connect(self._tick)
        self._ctrl.watchdog.timeout.connect(self._watchdog_check)
        self._ctrl.init_capture(on_status_cb=self._on_capture_status)
        self._ctrl.init_exporter()

        # UI-only state
        self._has_start = False
        self._start_sec = None
        self._end_sec = None
        self._live_cursor_sec = None
        self._fullscreen_window = None
        self._pending_reconnect_reason = ""
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 3
        self._reconnect_token = 0
        self._analysis_video_path = ""
        self._analysis_highlights: list[dict] = []
        self._preview_was_playing_before_hide = False

        self._build()

    def _build(self):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(20)

        # Left side - player stays responsive, results scroll independently.
        left_widget = QWidget()
        left_widget.setStyleSheet("background:transparent;")
        left = QVBoxLayout(left_widget)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(16)

        player_section = QWidget()
        player_section.setStyleSheet("background:transparent;")
        player_section.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        player_layout = QVBoxLayout(player_section)
        player_layout.setContentsMargins(0, 0, 0, 0)
        player_layout.setSpacing(16)
        self._player_layout = player_layout

        self._preview = VideoPreview()
        self._preview.fullscreen_clicked.connect(self._on_fullscreen_toggle)
        # 底部 hover 覆盖层与下方 SharedControlBar 联动(两处都保留播放控制)
        self._preview.play_pause_clicked.connect(self._on_play_pause)
        self._preview.mute_toggled.connect(self._on_preview_mute)
        player_layout.addWidget(self._preview, 1)

        self._controls = SharedControlBar()
        self._controls.play_pause.connect(self._on_play_pause)
        self._controls.mark_in_clicked.connect(self._on_mark_in)
        self._controls.mark_out_clicked.connect(self._on_mark_out)
        self._controls.export_clicked.connect(self._on_export)
        self._controls.seek_back.connect(self._on_seek_back)
        self._controls.seek_fwd.connect(self._on_seek_fwd)
        self._controls.timeline.position_changed.connect(self._on_timeline_seek)
        self._controls.return_live_clicked.connect(self._on_return_to_live)
        player_layout.addWidget(self._controls)

        left.addWidget(player_section, 3)

        # Export progress bar (hidden by default)
        self._export_progress_bar = QProgressBar()
        self._export_progress_bar.setFixedHeight(20)
        self._export_progress_bar.setRange(0, 100)
        self._export_progress_bar.setTextVisible(True)
        self._export_progress_bar.setFormat("导出中 %p%")
        self._export_progress_bar.setVisible(False)
        left.addWidget(self._export_progress_bar)

        results_scroll = QScrollArea()
        results_scroll.setWidgetResizable(True)
        results_scroll.setFrameShape(QScrollArea.NoFrame)
        results_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        results_scroll.setStyleSheet("QScrollArea { background:transparent; border:none; }")

        results_widget = QWidget()
        results_widget.setStyleSheet("background:transparent;")
        results_layout = QVBoxLayout(results_widget)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.setSpacing(16)

        self._analysis_results = AnalysisResultsGrid()
        self._analysis_results.result_clicked.connect(self._on_analysis_result_clicked)
        results_layout.addWidget(self._analysis_results)

        self._clips = ExportedClipsGrid()
        self._clips.clip_clicked.connect(self._on_clip_clicked)
        results_layout.addWidget(self._clips)

        results_layout.addStretch()
        results_scroll.setWidget(results_widget)
        left.addWidget(results_scroll, 2)
        lay.addWidget(left_widget, 1)

        # Right side - scrollable config
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QScrollArea.NoFrame)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        right_scroll.setMinimumWidth(400)
        right_scroll.setMaximumWidth(420)
        right_scroll.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

        self._config = ConfigPanel()
        self._config.connect_requested.connect(self._on_connect)
        self._config.start_record_requested.connect(self._on_record_toggle)
        self._config.analyze_requested.connect(self._on_analyze_current)
        self._config.export_analysis_requested.connect(self._on_export_analysis_results)
        self._config.setMaximumWidth(380)
        fade_config = FadeInWidget(delay_ms=100)
        fade_config.addWidget(self._config)
        fade_config.setMaximumWidth(380)
        right_scroll.setWidget(fade_config)
        lay.addWidget(right_scroll)

    def set_video_path(self, path):
        self._ctrl.video_path = path
        self._live_cursor_sec = None
        if path and os.path.isfile(path):
            probe = getattr(self._ctrl, "probe_video_duration", None)
            dur = probe() if callable(probe) else 0
            if dur > 0:
                self._ctrl.total_sec = dur
                if hasattr(self._controls, "timeline"):
                    self._controls.timeline.set_data(duration=dur, position=0)
                if hasattr(self._controls, "set_time"):
                    self._controls.set_time(0, dur)
            self._preview.play_video(path)
            self._controls.set_playing(True)
            config_panel = getattr(self, "_config", None)
            if config_panel is not None and hasattr(config_panel, "set_analyze_enabled"):
                config_panel.set_analyze_enabled(True)
            timer = getattr(self._ctrl, "timer", None)
            if timer is not None and hasattr(timer, "start"):
                timer.start(1000)

    def _emit_stats(self):
        recording = 1 if getattr(self._ctrl, "is_recording", False) else 0
        duration = int(getattr(self._ctrl, "total_sec", 0) or 0)
        clips = len(getattr(self._ctrl, "exported", []))
        try:
            self.stats_changed.emit(recording, duration, clips)
        except RuntimeError:
            pass

    def _on_fullscreen_toggle(self):
        if self._fullscreen_window is None:
            self._enter_fullscreen()
        else:
            self._exit_fullscreen(close_window=True)

    def _enter_fullscreen(self):
        if self._fullscreen_window is not None:
            return
        # 惰性确保 MpvWidget 已创建(全屏 reparent 的是 MpvWidget 而非整个预览容器)
        mpv = self._preview._ensure_mpv_widget() if hasattr(self._preview, "_ensure_mpv_widget") else None
        if mpv is None:
            self._status_changed.emit("预览未就绪,无法全屏", "warning")
            return
        # detach 控制栏;FullscreenPreview 会把 MpvWidget + 控制栏 reparent 进全屏窗口
        self._controls.setParent(None)
        self._controls.set_fullscreen(True)
        self._preview.set_fullscreen_mode(True)

        def _on_restore(_widget, controls):
            # widget 已 reparent 回 controls 的父级之外;放回 player_layout
            if controls is not None:
                controls.setParent(None)
            self._player_layout.insertWidget(0, self._preview, 1)
            self._player_layout.insertWidget(1, self._controls)
            self._controls.set_fullscreen(False)
            self._preview.set_fullscreen_mode(False)
            self._fullscreen_window = None

        fp = FullscreenPreview(
            self,
            get_widget=lambda: self._preview._mpv_widget,
            get_controls=lambda: self._controls,
            get_position=lambda: self._preview.position_sec(),
            get_duration=lambda: self._preview.duration_sec(),
            is_paused=lambda: not self._preview.is_playing(),
            is_muted=lambda: bool(self._preview._mpv_widget and getattr(self._preview._mpv_widget, '_muted', False)),
            on_toggle_play=lambda: self._preview.toggle_play_pause(),
            on_toggle_mute=lambda: self._preview._mpv_widget.set_muted(not getattr(self._preview._mpv_widget, '_muted', False)) if self._preview._mpv_widget else None,
            on_seek=lambda v: self._preview.seek_to(float(v)),
            on_restore=_on_restore,
            title="LSC 全屏播放器",
        )
        fp.enter()
        self._fullscreen_window = fp

    def _exit_fullscreen(self, close_window=True):
        fp = self._fullscreen_window
        if fp is None:
            return
        win = fp.window()
        if close_window and win is not None:
            win.close()
        else:
            # closeEvent 已触发 on_restore;手动触发兜底
            fp._close()

    # ── Connect ─────────────────────────────────────────────────

    def _on_connect(self, url):
        url = (url or "").strip()
        if not url:
            self.status_changed.emit("请输入直播间链接", "warning")
            return

        # 新连接会重置所有 pending 的重连状态，使旧重连回调失效
        self._reconnect_token += 1
        self._pending_reconnect_reason = ""
        self._reconnect_attempts = 0

        self._ctrl.page_url = url
        self._ctrl.stream_url = url
        self._config.set_connecting()
        self._controls.set_live_available(bool(self._ctrl.page_url))
        self.status_changed.emit("连接中...", "info")
        self._ctrl.start_url_parse(url, self._on_url_parsed)

    def _on_url_parsed(self, info, token=None):
        # token 用于区分重连会话；若 token 不匹配说明用户在解析完成前已停止/重置，应忽略旧回调
        if token is not None and token != getattr(self, "_reconnect_token", 0):
            _log.debug("Stale reconnect callback ignored (token=%s, current=%s)", token, self._reconnect_token)
            return

        if info.get('isLive'):
            stream_url, _selected_quality = self._ctrl.select_stream_url(
                info,
                self._config.quality_selection,
            )
            self._ctrl.stream_url = stream_url or info.get('streamUrl', '')
            streamer = info.get('streamerName', '未知')
            title = info.get('title', '')
            self._config.set_connected(True)
            pending_reconnect_reason = getattr(self, "_pending_reconnect_reason", "")
            if pending_reconnect_reason:
                # 再次检查 token，因为 pending 状态可能在解析期间被其他操作重置
                if token is not None and token != getattr(self, "_reconnect_token", 0):
                    _log.debug("Reconnect state changed during parse, ignoring callback")
                    return
                reason = pending_reconnect_reason
                self._pending_reconnect_reason = ""
                self.status_changed.emit(f"直播流已恢复，正在继续录制: {streamer} - {title}", "success")
                restarted = self._start_recording()
                if not restarted:
                    self.status_changed.emit(f"自动恢复失败: {reason}", "error")
                    self._config.set_recording(False)
                return

            self.status_changed.emit(f"已连接: {streamer} - {title}", "success")
        else:
            error = info.get('error', '直播未开播或链接无效')
            if getattr(self, "_pending_reconnect_reason", ""):
                if token is not None and token != getattr(self, "_reconnect_token", 0):
                    _log.debug("Reconnect state changed during parse, ignoring callback")
                    return
                self._pending_reconnect_reason = ""
                self.status_changed.emit(f"自动恢复失败: {error}", "error")
                self._stop_recording()
                self._config.set_recording(False)
                return

            self.status_changed.emit(f"连接失败: {error}", "error")
            self._config.set_connected(False)
            self._controls.set_live_available(False)

    def _refresh_stream_info(self, source: str) -> None:
        """Probe stream metadata and update the info panel."""
        resolution, fps = self._ctrl.probe_stream_metadata(source)
        self._config.set_info("res", resolution or "--")
        self._config.set_info("fps", fps or "--")

    # ── Record toggle ──────────────────────────────────────────

    def _on_record_toggle(self):
        if self._ctrl.is_recording:
            self._stop_recording()
            self._config.set_recording(False)
        else:
            ok = self._start_recording()
            if ok:
                self._config.set_recording(True)

    def _start_recording(self):
        if not self._ctrl.stream_url:
            self.status_changed.emit("请先输入直播间链接并连接", "warning")
            return False

        # Reset UI state first (before starting any recording)
        self._live_cursor_sec = None
        self._preview.stop_video()
        self._preview.set_state(recording=True, connected=True, time="00:00:00")
        self._controls.set_recording(True)
        self._controls.set_range_state(False, False)
        self._controls.set_export_enabled(False)
        self._controls.timeline.set_cursor_mode(SharedTimeline.CURSOR_WHITE)

        # NOTE: Do NOT set is_recording=True or start timers here.
        # Let the controller handle state — it sets is_recording and
        # record_start_mono atomically inside start_recording_with_crf().
        # Starting timers before the capture is ready caused FFmpeg to be
        # force-killed and restarted on every tick (the "immediate stop" bug).

        if not self._ctrl.capture:
            self._preview.set_state(recording=False, connected=False)
            self._controls.set_recording(False)
            reason = getattr(self._ctrl, "capture_init_error", "") or "未找到 FFmpeg 或录制器初始化失败"
            self.status_changed.emit(f"录制启动失败: {reason}", "error")
            return False

        output_dir = self._config.output_path or self._ctrl.output_dir or os.path.join(os.path.expanduser("~"), "LSC", "recordings")
        encoder = self._config.encoder_selection
        crf = self._config.crf_value
        param_mode = self._config.param_mode_selection
        bitrate_value = self._config.bitrate_value
        bitrate_unit = self._config.bitrate_unit

        success, output_path, encoder_used, error_msg = self._ctrl.start_recording_with_crf(
            self._ctrl.stream_url,
            output_dir,
            encoder,
            crf,
            param_mode=param_mode,
            bitrate=bitrate_value,
            bitrate_unit=bitrate_unit,
            input_args=self._ctrl.input_args or None,
            on_status=lambda txt, typ: self.status_changed.emit(txt, typ),
        )

        if success:
            # Now that capture is confirmed running, start the timers
            self._pending_reconnect_reason = ""
            self._reconnect_attempts = 0
            self._ctrl.timer.start(1000)
            self._ctrl.watchdog.start(2000)
            self._ctrl.output_dir = output_dir
            self._ctrl.video_path = output_path
            self._config.set_info("path", output_path)
            self._config.set_info("codec", encoder_used)
            # Show encoding parameters in info panel
            crf = self._config.crf_value
            if encoder_used == "H.264 CPU":
                self._config.set_info("bitrate", f"CRF {crf}")
            elif encoder_used == "H.264 NVENC":
                self._config.set_info("bitrate", f"CQ {crf}")
            else:
                self._config.set_info("bitrate", "原始码率")
            self.status_changed.emit("录制中", "info")
            # Preview the live stream directly. Tailing a growing mp4 can hit
            # transient EOF/cache stalls after a few seconds.
            self._preview.play_live(self._ctrl.stream_url)
            self._controls.set_playing(True)
            self._emit_stats()
        else:
            detail = error_msg or "FFmpeg 无法连接直播流"
            self.status_changed.emit(f"录制启动失败: {detail}", "error")
            # Reset UI — no need to stop capture since it never started
            self._preview.set_state(recording=False, connected=False)
            self._controls.set_recording(False)
            return False

        self._config.set_info("res", "探测中...")
        self._config.set_info("fps", "探测中...")
        return True

    def _stop_recording(self):
        self._ctrl.is_recording = False
        self._live_cursor_sec = None
        self._pending_reconnect_reason = ""
        self._ctrl.timer.stop()
        self._ctrl.watchdog.stop()
        self._preview.stop_video()
        self._controls.set_playing(False)

        success, size_mb, output_path = self._ctrl.stop_recording()
        played_output = False
        if success:
            self._ctrl.video_path = output_path
            self._config.set_info("size", f"{size_mb:.1f} MB")
            self.status_changed.emit(f"录制完成: {size_mb:.1f} MB", "success")
            def _on_probed_success(dur):
                if dur > 0:
                    self._ctrl.total_sec = dur
                    self._controls.timeline.set_data(duration=dur, position=0)
            self._ctrl.probe_video_duration(on_probed=_on_probed_success)
            # Auto-play the completed recording
            if output_path and os.path.isfile(output_path):
                self._preview.play_video(output_path)
                played_output = True
        else:
            # Even if stop() reports failure (e.g. status was already ERROR
            # from check_and_handle_crash), the output file may still exist.
            # Don't lose the user's recording!
            actual_path = self._ctrl.video_path
            if actual_path and os.path.isfile(actual_path):
                size_mb = os.path.getsize(actual_path) / (1024 * 1024)
                self._config.set_info("size", f"{size_mb:.1f} MB")
                self.status_changed.emit(f"录制完成: {size_mb:.1f} MB", "success")
                def _on_probed_failure(dur):
                    if dur > 0:
                        self._ctrl.total_sec = dur
                        self._controls.timeline.set_data(duration=dur, position=0)
                self._ctrl.probe_video_duration(on_probed=_on_probed_failure)
                # Auto-play the completed recording
                self._preview.play_video(actual_path)
                played_output = True
            else:
                self.status_changed.emit("录制停止", "info")

        self._preview.set_state(recording=False, connected=False)
        self._controls.set_recording(False)
        self._controls.set_export_enabled(False)
        self._controls.set_range_state(False, False)
        self._controls.timeline.set_cursor_mode(SharedTimeline.CURSOR_WHITE)
        self._has_start = False
        self._start_sec = None
        self._end_sec = None
        self._config.set_recording(False)
        self._config.set_analyze_enabled(bool(self._ctrl.video_path and os.path.isfile(self._ctrl.video_path)))
        if hasattr(self._config, "set_export_analysis_enabled"):
            self._config.set_export_analysis_enabled(bool(getattr(self, "_analysis_highlights", [])))
        self._controls.set_playing(played_output)
        self._emit_stats()

    # ── Play / Pause ────────────────────────────────────────────

    def _on_play_pause(self):
        if self._ctrl.is_recording:
            if self._preview.is_playing():
                self._preview.toggle_play_pause()
                self._controls.set_playing(False)
            elif self._ctrl.stream_url:
                self._preview.play_live(self._ctrl.stream_url)
                self._controls.set_playing(True)
            return

        if not self._ctrl.is_recording and self._ctrl.video_path and os.path.isfile(self._ctrl.video_path):
            if self._preview.is_playing():
                self._preview.toggle_play_pause()
                self._controls.set_playing(False)
            else:
                self._preview.play_video(self._ctrl.video_path)
                self._controls.set_playing(True)
                self._ctrl.timer.start(1000)
            return

    def _on_preview_mute(self, muted: bool) -> None:
        """底部覆盖层静音切换:委托 mpv,与 SharedControlBar 状态独立。"""
        if self._preview._mpv_widget is not None:
            self._preview._mpv_widget.set_muted(muted)

    # ── Timer tick ──────────────────────────────────────────────

    def _tick(self):
        if self._ctrl.is_recording:
            # Use public API to check for FFmpeg crash
            exit_code = self._ctrl.check_capture_crash()
            if exit_code is not None:
                msg = f"FFmpeg 异常退出 (code {exit_code})"
                if self._attempt_stream_reconnect(msg):
                    self.status_changed.emit("录制异常，正在尝试恢复直播流...", "warning")
                    return

                self.status_changed.emit(msg, "error")
                self._stop_recording()
                self._config.set_recording(False)
                return

            total = self._ctrl.tick()
            self._preview.set_state(recording=True, connected=True, time=_fmt_time(total))
            self._controls.timeline.set_live_clock(datetime.now().strftime("%H:%M:%S"))
            current_pos = self._live_cursor_sec if self._live_cursor_sec is not None else total
            display_end = self._end_sec if self._end_sec is not None else (current_pos if self._has_start else None)
            cursor_mode = SharedTimeline.CURSOR_RED if self._has_start else SharedTimeline.CURSOR_WHITE
            self._controls.timeline.set_cursor_mode(cursor_mode)
            pos = current_pos if not self._has_start else display_end
            self._controls.timeline.set_data(
                duration=total,
                position=pos,
                start=self._start_sec,
                end=display_end,
            )
            self._controls.set_time(pos, total)
            self._emit_stats()

            # File size from actual file
            if self._ctrl.video_path and os.path.isfile(self._ctrl.video_path):
                size_mb = os.path.getsize(self._ctrl.video_path) / (1024 * 1024)
                self._config.set_info("size", f"{size_mb:.1f} MB")
            else:
                self._config.set_info("size", f"{total * _ESTIMATED_MB_PER_SEC:.1f} MB")
        else:
            if self._preview.is_playing():
                pos = self._preview.position_sec()
                dur = self._ctrl.total_sec
                self._controls.timeline.set_data(duration=dur, position=pos)
                self._controls.set_time(pos, dur)
            else:
                self._ctrl.timer.stop()
                self._controls.set_playing(False)

    # ── Capture status callback ─────────────────────────────────

    def _on_capture_status(self, status):
        from lsc.recorder.capture import CaptureStatus
        if status == CaptureStatus.ERROR:
            self.status_changed.emit("录制出错", "error")
        elif status == CaptureStatus.CONNECTING:
            self.status_changed.emit("连接中...", "info")

    # ── Watchdog ────────────────────────────────────────────────

    def _watchdog_check(self):
        msg = self._ctrl.watchdog_check()
        if msg:
            if self._attempt_stream_reconnect(msg):
                self.status_changed.emit("录制异常，正在尝试恢复直播流...", "warning")
                return
            self.status_changed.emit(f"录制异常: {msg}", "error")
            self._stop_recording()
            self._config.set_recording(False)

    def _attempt_stream_reconnect(self, reason: str) -> bool:
        page_url = getattr(self._ctrl, "page_url", "")
        if not page_url or getattr(self, "_pending_reconnect_reason", ""):
            return False
        if getattr(self, "_reconnect_attempts", 0) >= getattr(self, "_max_reconnect_attempts", 3):
            return False

        self._reconnect_token += 1
        token = self._reconnect_token
        self._pending_reconnect_reason = reason
        self._reconnect_attempts = getattr(self, "_reconnect_attempts", 0) + 1
        # 绑定本次重连的 token，回调中若 token 已过期则忽略，避免用户停止后旧回调仍启动录制
        # 重连时强制刷新缓存，避免 10 秒失败缓存导致用户点“重试”后仍返回旧错误
        self._ctrl._force_next_parse_refresh = True
        self._ctrl.start_url_parse(
            page_url, lambda info: self._on_url_parsed(info, token=token)
        )
        return True

    # ── Mark / Export ────────────────────────────────────────────

    def _current_range_time(self):
        if self._ctrl.is_recording:
            duration = float(self._ctrl.total_sec)
            live_cursor_sec = getattr(self, "_live_cursor_sec", None)
            if live_cursor_sec is not None:
                return float(live_cursor_sec), duration
            return duration, duration
        pos = float(self._preview.position_sec())
        dur = float(self._preview.duration_sec() or self._ctrl.total_sec)
        return pos, dur

    def _sync_range_controls(self):
        has_in = self._start_sec is not None
        has_out = self._end_sec is not None
        can_export = has_in and has_out and self._end_sec > self._start_sec
        self._has_start = has_in
        self._controls.set_range_state(has_in, has_out)
        self._controls.set_export_enabled(can_export)
        self._controls.timeline.set_cursor_mode(
            SharedTimeline.CURSOR_RED if has_in and not has_out else SharedTimeline.CURSOR_WHITE
        )

    def _apply_range_to_timeline(self, position=None, duration=None):
        if duration is None:
            duration = float(self._ctrl.total_sec)
        if position is None:
            position = self._end_sec if self._end_sec is not None else self._start_sec
        self._controls.timeline.set_data(
            duration=duration,
            position=position or 0,
            start=self._start_sec,
            end=self._end_sec,
        )

    def _on_mark_in(self):
        pos, dur = self._current_range_time()
        if self._end_sec is not None and pos > self._end_sec:
            # 入点超过出点时，自动交换（与多房间工作台行为一致）
            self._start_sec = self._end_sec
            self._end_sec = pos
        else:
            self._start_sec = pos
        self._sync_range_controls()
        preview_end = self._end_sec
        if preview_end is None and self._ctrl.is_recording:
            preview_end = dur
        self._controls.timeline.set_data(
            duration=max(dur, preview_end or pos, pos),
            position=pos,
            start=self._start_sec,
            end=preview_end,
        )

    def _on_mark_out(self):
        pos, dur = self._current_range_time()
        if self._start_sec is None:
            self._start_sec = max(0.0, pos - 10.0)
        elif pos < self._start_sec:
            # 出点小于入点时，自动交换（与多房间工作台行为一致）
            self._end_sec = self._start_sec
            self._start_sec = pos
            self._sync_range_controls()
            self._apply_range_to_timeline(position=pos, duration=max(dur, pos, self._end_sec or 0))
            return
        self._end_sec = pos
        self._sync_range_controls()
        self._apply_range_to_timeline(position=pos, duration=max(dur, pos, self._start_sec or 0))

    def _on_export(self):
        if not self._has_start or self._start_sec is None or self._end_sec is None:
            return
        if self._end_sec <= self._start_sec:
            return
        # NOTE: Export is allowed while recording.  The capture uses
        # fragmented MP4 (frag_keyframe+empty_moov+faststart), so FFmpeg
        # can read and clip from the growing file without issues.
        # Export is also allowed during playback of completed recordings.

        idx = len(self._ctrl.exported) + 1
        # 注意:不带 .mp4 后缀,ClipExporter.export_clip 会在拼输出路径时自动追加。
        name = f"clip_{idx:03d}_{_fmt_time(self._start_sec).replace(':', '')}"
        start_sec = self._start_sec
        end_sec = self._end_sec

        output_root = self._config.output_path or self._ctrl.output_dir or "./output"
        output_dir = os.path.join(output_root, "highlights")
        if self._ctrl.start_export(
            start_sec,
            end_sec,
            output_dir,
            name,
            on_done=lambda ok, path, err, sz, export_name=name, export_idx=idx, export_start=start_sec, export_end=end_sec:
                self._on_export_done(ok, path, err, sz, export_name, export_idx, export_start, export_end),
            on_progress=self._on_export_progress,
        ):
            self._controls.set_export_enabled(False)
            self._export_progress_bar.setValue(0)
            self._export_progress_bar.setVisible(True)
        else:
            reason = getattr(self._ctrl, "exporter_init_error", "") or "导出器不可用或源文件不存在"
            self.status_changed.emit(f"片段导出失败: {reason}", "error")

    def _on_export_done(self, success, path, error, size_mb, name, idx, start_sec, end_sec):
        self._export_progress_bar.setVisible(False)
        if success:
            size = f"{size_mb:.1f} MB"
            self._ctrl.exported.append((name, start_sec, end_sec, size, path))
            self._clips.add_clip(name, start_sec, end_sec, size)
            self._controls.timeline.add_marker(start_sec, end_sec, name)
            self.status_changed.emit(f"片段已导出: {path}", "success")
            self._emit_stats()
        elif error:
            self.status_changed.emit(f"片段导出失败: {error}", "error")
        self._controls.set_export_enabled(True)
        self._on_clear_range()

    def _on_export_progress(self, percent: float, elapsed: float, total: float) -> None:
        """Update export progress bar from ExportWorker."""
        self._export_progress_bar.setValue(int(min(percent, 100)))
        if total > 0:
            self._export_progress_bar.setFormat(f"导出中 {_fmt_time(elapsed)}/{_fmt_time(total)} · %p%")

    def _on_analyze_current(self):
        video_path = getattr(self._ctrl, "video_path", "")
        if not video_path or not os.path.isfile(video_path):
            self.status_changed.emit("分析失败: 当前没有可分析的录制文件", "error")
            return

        output_dir = os.path.dirname(video_path) or self._config.output_path or "."
        self._config.set_analyze_enabled(False)
        self.status_changed.emit("分析中...", "info")
        started = self._ctrl.start_analysis(
            video_path,
            self._config.analysis_profile,
            output_dir,
            self._on_analysis_done,
        )
        if not started:
            self._config.set_analyze_enabled(True)
            self.status_changed.emit("分析失败: 无法启动分析任务", "error")

    def _on_analysis_done(self, success, result_path, error, highlight_count):
        self._config.set_analyze_enabled(True)
        if success:
            highlights: list[dict] = []
            try:
                import json

                with open(result_path, encoding="utf-8") as f:
                    payload = json.load(f)
                highlights = payload.get("highlights", []) if isinstance(payload, dict) else []
            except Exception:
                highlights = []

            ctrl = getattr(self, "_ctrl", None)
            self._analysis_video_path = getattr(ctrl, "video_path", "") if ctrl is not None else ""
            self._analysis_highlights = highlights
            analysis_results = getattr(self, "_analysis_results", None)
            if analysis_results is not None and hasattr(analysis_results, "set_results"):
                analysis_results.set_results(highlights)
            if hasattr(self._config, "set_export_analysis_enabled"):
                self._config.set_export_analysis_enabled(bool(highlights))
            self._config.set_info("analysis", f"{highlight_count} 个高光")
            self._config.set_info("analysis_path", result_path)
            self.status_changed.emit(f"分析完成: 检测到 {highlight_count} 个高光", "success")
            return

        if hasattr(self._config, "set_export_analysis_enabled"):
            self._config.set_export_analysis_enabled(False)
        self.status_changed.emit(f"分析失败: {error}", "error")

    def _on_analysis_result_clicked(self, idx: int):
        highlights = getattr(self, "_analysis_highlights", [])
        if idx < 0 or idx >= len(highlights):
            return

        source_path = getattr(self, "_analysis_video_path", "")
        if source_path and os.path.isfile(source_path):
            self.set_video_path(source_path)

        highlight = highlights[idx]
        self._start_sec = float(highlight.get("start_sec", 0.0))
        self._end_sec = float(highlight.get("end_sec", self._start_sec))
        self._sync_range_controls()
        self._apply_range_to_timeline(position=self._start_sec, duration=float(getattr(self._ctrl, "total_sec", 0) or 0))

    def _on_export_analysis_results(self):
        source_path = getattr(self, "_analysis_video_path", "")
        highlights = getattr(self, "_analysis_highlights", [])
        if not source_path or not os.path.isfile(source_path) or not highlights:
            self.status_changed.emit("导出失败: 当前没有可导出的分析高光", "error")
            return

        output_root = self._config.output_path or self._ctrl.output_dir or "./output"
        output_dir = os.path.join(output_root, "highlights")
        self._config.set_export_analysis_enabled(False)
        self.status_changed.emit("正在批量导出分析高光...", "info")
        started = self._ctrl.start_export_all(
            source_path,
            highlights,
            output_dir,
            self._on_export_analysis_done,
        )
        if not started:
            self._config.set_export_analysis_enabled(True)
            self.status_changed.emit("导出失败: 无法启动批量导出任务", "error")

    def _on_export_analysis_done(self, success, exported_count, total_count, error, results):
        self._config.set_export_analysis_enabled(bool(getattr(self, "_analysis_highlights", [])))
        if success:
            for idx, result in enumerate(results):
                if not result.success:
                    continue
                highlight = self._analysis_highlights[idx] if idx < len(self._analysis_highlights) else {}
                start_sec = float(highlight.get("start_sec", 0.0))
                end_sec = float(highlight.get("end_sec", start_sec))
                size = f"{result.file_size_mb:.1f} MB"
                self._ctrl.exported.append(
                    (result.title, start_sec, end_sec, size, result.output_path)
                )
                self._clips.add_clip(result.title, start_sec, end_sec, size)
                self._controls.timeline.add_marker(start_sec, end_sec, result.title)
            self._emit_stats()
            self.status_changed.emit(f"批量导出完成: {exported_count}/{total_count}", "success")
            return

        self.status_changed.emit(f"导出失败: {error}", "error")

    def _on_clip_clicked(self, idx):
        if idx < 0 or idx >= len(self._ctrl.exported):
            return
        clip = self._ctrl.exported[idx]
        path = clip[4] if len(clip) >= 5 else ""
        start_sec = clip[1] if len(clip) >= 2 else 0
        end_sec = clip[2] if len(clip) >= 3 else 0
        if path and os.path.isfile(path):
            self.set_video_path(path)
            # Restore the original selection range so user can see where the clip came from
            if end_sec > start_sec:
                self._start_sec = start_sec
                self._end_sec = end_sec
                self._sync_range_controls()
                self._apply_range_to_timeline(
                    position=start_sec,
                    duration=float(self._ctrl.total_sec or end_sec),
                )

    def _on_clear_range(self):
        self._has_start = False
        self._start_sec = None
        self._end_sec = None
        self._controls.set_range_state(False, False)
        self._controls.set_export_enabled(False)
        self._controls.timeline.set_cursor_mode(SharedTimeline.CURSOR_WHITE)

    # ── Seek ─────────────────────────────────────────────────────

    def _on_timeline_seek(self, sec):
        """Handle timeline click/drag — seek video during playback."""
        if self._ctrl.is_recording:
            duration = float(self._ctrl.total_sec or 0)
            if duration <= 0:
                return
            self._live_cursor_sec = max(0.0, min(float(sec), duration))
            self._controls.timeline.set_data(
                duration=duration,
                position=self._live_cursor_sec,
                start=self._start_sec,
                end=self._end_sec,
            )
            self._controls.set_time(self._live_cursor_sec, duration)
            return

        if self._ctrl.video_path and os.path.isfile(self._ctrl.video_path):
            duration = float(self._preview.duration_sec() or self._ctrl.total_sec or 0)
            position = max(0.0, min(float(sec), duration if duration > 0 else float(sec)))
            self._preview.seek_to(position)
            self._controls.timeline.set_data(duration=duration, position=position)
            self._controls.set_time(position, duration)

    def _on_return_to_live(self):
        if not self._ctrl.stream_url:
            return
        self._live_cursor_sec = None
        self._preview.play_live(self._ctrl.stream_url)
        self._controls.set_playing(True)
        duration = float(self._ctrl.total_sec or 0)
        if duration > 0:
            self._controls.timeline.set_data(
                duration=duration,
                position=duration,
                start=self._start_sec,
                end=self._end_sec,
            )
            self._controls.set_time(duration, duration)

    def _on_seek_back(self):
        if self._has_start and self._end_sec:
            self._end_sec = max(self._start_sec, self._end_sec - 10)
            self._controls.timeline.set_data(
                duration=self._ctrl.total_sec, position=self._end_sec,
                start=self._start_sec, end=self._end_sec,
            )
        elif not self._ctrl.is_recording and self._preview.is_playing():
            # Seek video back 10 seconds during playback
            pos = max(0, self._preview.position_sec() - 10)
            self._preview.seek_to(pos)

    def _on_seek_fwd(self):
        if self._has_start and self._end_sec is not None:
            self._end_sec = min(self._ctrl.total_sec, self._end_sec + 10)
            self._controls.timeline.set_data(
                duration=self._ctrl.total_sec, position=self._end_sec,
                start=self._start_sec, end=self._end_sec,
            )
        elif not self._ctrl.is_recording and self._preview.is_playing():
            # Seek video forward 10 seconds during playback
            dur = self._preview.duration_sec() or self._ctrl.total_sec
            pos = min(dur, self._preview.position_sec() + 10)
            self._preview.seek_to(pos)

    def keyPressEvent(self, event):
        """Handle keyboard shortcuts for common actions."""
        if event.key() == Qt.Key_Space:
            self._on_play_pause()
            event.accept()
            return
        if event.key() == Qt.Key_I:
            self._on_mark_in()
            event.accept()
            return
        if event.key() == Qt.Key_O:
            self._on_mark_out()
            event.accept()
            return
        if event.key() == Qt.Key_Left:
            self._on_seek_back()
            event.accept()
            return
        if event.key() == Qt.Key_Right:
            self._on_seek_fwd()
            event.accept()
            return
        if event.key() == Qt.Key_E and event.modifiers() & Qt.ControlModifier:
            self._on_export()
            event.accept()
            return
        super().keyPressEvent(event)

    # ── Page visibility ──────────────────────────────────────────

    def showEvent(self, event):
        """Re-start preview when page becomes visible again."""
        super().showEvent(event)
        if self._preview_was_playing_before_hide:
            if self._ctrl.is_recording and self._ctrl.stream_url:
                self._preview.play_live(self._ctrl.stream_url)
                self._controls.set_playing(True)
            elif self._ctrl.video_path and os.path.isfile(self._ctrl.video_path):
                self._preview.play_video(self._ctrl.video_path)
                self._controls.set_playing(True)
                self._ctrl.timer.start(1000)
            self._preview_was_playing_before_hide = False

    def hideEvent(self, event):
        """Stop preview when page is hidden to save bandwidth/CPU."""
        super().hideEvent(event)
        if self._preview.is_playing():
            self._preview_was_playing_before_hide = True
            self._preview.stop_video()
            self._controls.set_playing(False)
            if not self._ctrl.is_recording:
                self._ctrl.timer.stop()

    # ── Cleanup ──────────────────────────────────────────────────

    def cleanup(self):
        self._ctrl.cleanup()
        self._preview.stop_video()
        self._preview.cleanup()
