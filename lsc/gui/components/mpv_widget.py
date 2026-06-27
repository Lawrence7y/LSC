"""基于 libmpv 的视频播放组件。"""
from __future__ import annotations

import logging
import os
from pathlib import Path
import shutil
import subprocess

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from lsc.gui.theme import connect_theme_changed, get_theme

_log = logging.getLogger(__name__)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


class MpvWidget(QWidget):
    """libmpv 视频播放窗口。

    如果系统未安装 libmpv，则降级为占位符显示。

    对外暴露两套等价 API：
    - 通用播放控制：play / pause / resume / stop / seek / set_muted
    - 录制页专用：play_video / play_live / stop_video / is_playing /
      toggle_play_pause / seek_to / position_sec / duration_sec
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self._prepare_libmpv_search_paths()
        self._mpv = None
        self._ffplay_path = ""
        self._ffplay_proc: subprocess.Popen | None = None
        self._stream_headers: dict[str, str] = {}
        self._playing = False
        self._live_mode = False
        self._current_path = ""
        self._muted = False
        self._bound_wid = ""
        self._init_error: str = ""
        self._init_error_detail: str = ""
        self._placeholder = QLabel("视频预览", self)
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setObjectName("label_tertiary")
        self._placeholder.setWordWrap(True)
        self._placeholder.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._apply_placeholder_style()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self._placeholder)
        self._try_init_mpv()
        connect_theme_changed(self._refresh_theme)
        # 在 Qt 对象销毁时自动释放 mpv，避免依赖 __del__ 导致 GC 阶段资源泄漏
        self.destroyed.connect(self._on_destroyed)

    def _on_destroyed(self, _obj=None) -> None:
        """QObject destroyed 信号槽：安全释放 mpv 资源。"""
        try:
            self.cleanup()
        except Exception:
            pass

    @staticmethod
    def _prepare_libmpv_search_paths() -> None:
        """Add bundled/project-local libmpv directories before importing mpv."""
        candidates = [
            _project_root() / ".runtime" / "libmpv",
            _project_root(),
        ]
        for path in candidates:
            if not path.is_dir():
                continue
            path_str = str(path)
            parts = os.environ.get("PATH", "").split(os.pathsep)
            if path_str not in parts:
                os.environ["PATH"] = path_str + os.pathsep + os.environ.get("PATH", "")
            if hasattr(os, "add_dll_directory"):
                try:
                    os.add_dll_directory(path_str)
                except OSError as exc:
                    _log.debug("add_dll_directory failed for %s: %s", path_str, exc)

    def _apply_placeholder_style(self) -> None:
        c = get_theme()
        self._placeholder.setStyleSheet(
            f"background:{c.bg_tertiary};font-size:13px;border-radius:8px;"
            f"color:{c.text_secondary};padding:12px;"
        )

    def _refresh_theme(self) -> None:
        self._apply_placeholder_style()

    def is_available(self) -> bool:
        """Return whether any preview backend is available."""
        return self._mpv is not None or bool(self._ffplay_path)

    def init_error(self) -> str:
        """Return a user-facing initialization error message, or empty string."""
        if self._init_error and self._init_error_detail:
            return f"{self._init_error}：{self._init_error_detail}"
        return self._init_error

    def _set_placeholder_error(self, short: str, detail: str) -> None:
        self._init_error = short
        self._init_error_detail = detail
        self._placeholder.setText(f"{short}\n{detail}")

    def _try_init_mpv(self):
        """尝试初始化 libmpv。"""
        try:
            self._mpv = self._create_mpv_instance()
            self._placeholder.hide()
            self._init_error = ""
            self._init_error_detail = ""
        except ImportError:
            _log.info("python-mpv 未安装，视频预览将使用占位符模式")
            self._mpv = None
            self._init_fallback_backend(
                "预览不可用",
                "未安装 python-mpv\n请运行：pip install python-mpv",
            )
        except Exception as exc:
            _log.warning("libmpv 初始化失败，尝试 ffplay 兜底: %s", exc)
            self._mpv = None
            err_text = str(exc)
            if "Cannot find mpv" in err_text or "dll" in err_text.lower():
                detail = "未找到 libmpv 动态库\nWindows 请放置 mpv-2.dll 到程序目录或 PATH"
            else:
                detail = f"{exc}\n请检查系统是否安装 libmpv"
            self._init_fallback_backend(
                "预览初始化失败",
                detail,
            )

    def _create_mpv_instance(self, headers: dict[str, str] | None = None):
        """Create libmpv bound to this widget's current native window.

        Args:
            headers: HTTP headers to pass as mpv startup options.
                     Must be set at creation time for reliable CDN access.
        """
        import mpv

        self._bound_wid = str(int(self.winId()))
        kwargs = dict(
            wid=self._bound_wid,
            vo="gpu",
            hwdec="auto",
            keepaspect=True,
            video_unscaled="no",
            autofit="100%x100%",
        )
        if headers:
            header_list = [f"{k}: {v}" for k, v in headers.items() if v]
            if header_list:
                kwargs["http_header_fields"] = header_list
        return mpv.MPV(**kwargs)

    def rebind_video_output(self) -> None:
        """Rebind libmpv after Qt reparents this widget.

        Multi-room preview widgets are created before they are embedded into
        a room card. On Windows, reparenting can change the native window
        handle; without rebinding, mpv keeps rendering into the old invisible
        handle and the card shows a blank area.
        """
        if self._mpv is None:
            return

        # 防御：rebind 必须绑定到有效的原生句柄，否则 mpv 会渲染到一个
        # 无效/不可见句柄而表现为静默黑屏。仍继续尝试，但记录以便排查。
        try:
            wid = self.winId()
        except Exception:
            wid = 0
        if not wid:
            _log.warning(
                "rebind_video_output: widget winId is 0 (not yet native); "
                "preview may render into a dead handle and show blank."
            )

        path = self._current_path
        live = self._live_mode
        was_playing = self._playing
        muted = self._muted
        try:
            self._mpv.terminate()
        except Exception as exc:
            _log.debug("mpv rebind terminate failed: %s", exc)

        try:
            self._mpv = self._create_mpv_instance()
            if self._stream_headers:
                self.set_stream_headers(self._stream_headers)
            if path and was_playing:
                self._play(path, live=live)
            self.set_muted(muted)
            self._placeholder.hide()
        except Exception as exc:
            _log.warning("mpv rebind failed: %s", exc)
            self._mpv = None
            self._playing = False
            self._set_placeholder_error("预览重绑失败", str(exc))

    def _init_fallback_backend(self, short: str, detail: str) -> None:
        """Use ffplay as an external preview fallback when libmpv is unavailable."""
        self._ffplay_path = shutil.which("ffplay") or ""
        if self._ffplay_path:
            fallback_detail = f"{detail}\n已启用 ffplay 外部预览兜底"
            self._set_placeholder_error(short, fallback_detail)
            return
        self._set_placeholder_error(short, detail)

    # ── 通用播放控制 API（multi_room manager 使用） ─────────────

    def play(self, path: str) -> None:
        """播放指定文件或流地址。"""
        self._play(path, live=False)

    def pause(self) -> None:
        """暂停播放。"""
        if self._mpv is not None:
            try:
                self._mpv.pause = True
                self._playing = False
            except Exception as exc:
                _log.debug("mpv pause 失败: %s", exc)

    def resume(self) -> None:
        """恢复播放。"""
        if self._mpv is not None:
            try:
                self._mpv.pause = False
                self._playing = True
            except Exception as exc:
                _log.debug("mpv resume 失败: %s", exc)

    def stop(self) -> None:
        """停止播放。"""
        self._stop_internal()

    def seek(self, seconds: float) -> None:
        """跳转到指定时间。"""
        if self._mpv is not None:
            try:
                self._mpv.seek(seconds, reference="absolute")
            except Exception as exc:
                _log.debug("mpv seek 失败: %s", exc)

    def time_pos(self) -> float:
        """获取当前播放位置（秒）。"""
        return self.position_sec()

    def duration(self) -> float:
        """获取视频总时长（秒）。"""
        return self.duration_sec()

    def set_muted(self, muted: bool) -> None:
        """Set mute state."""
        self._muted = muted
        if self._mpv is not None:
            try:
                self._mpv.mute = muted
            except Exception as exc:
                _log.debug("mpv set_mute 失败: %s", exc)

    def set_stream_headers(self, headers: dict[str, str]) -> None:
        """Set HTTP headers for the next stream playback.

        Required for platforms like Douyin/Bilibili/Huya whose CDN
        rejects requests without a valid Referer or User-Agent.
        """
        self._stream_headers = dict(headers or {})
        if self._mpv is not None and headers:
            try:
                header_list = [f"{k}: {v}" for k, v in headers.items() if v]
                self._mpv["http-header-fields"] = header_list
            except Exception as exc:
                _log.debug("mpv set_stream_headers 失败: %s", exc)

    def set_live_mode(self, live: bool) -> None:
        """设置实时模式（跟随写入头）。"""
        self._live_mode = live
        if self._mpv is not None:
            try:
                if live:
                    self._mpv["loop-file"] = "inf"
                else:
                    self._mpv["loop-file"] = "no"
            except Exception as exc:
                _log.debug("mpv set_live_mode 失败: %s", exc)

    def set_ab_loop(self, start_sec: float, end_sec: float) -> bool:
        """使用 mpv 原生 A-B 循环精确循环 [start_sec, end_sec]。"""
        if self._mpv is None:
            return False
        try:
            self._mpv["ab-loop-a"] = start_sec
            self._mpv["ab-loop-b"] = end_sec
            return True
        except Exception as exc:
            _log.debug("mpv set_ab_loop 失败: %s", exc)
            return False

    def clear_ab_loop(self) -> None:
        """清除 mpv A-B 循环。"""
        if self._mpv is None:
            return
        try:
            self._mpv["ab-loop-a"] = "no"
            self._mpv["ab-loop-b"] = "no"
        except Exception as exc:
            _log.debug("mpv clear_ab_loop 失败: %s", exc)

    # ── 录制页专用 API（VideoPreview 使用） ─────────────────────

    def play_video(self, path: str, live: bool = False) -> None:
        """播放一个已完成的（非增长）录像文件。"""
        self._play(path, live=live)

    def play_live(self, path: str) -> None:
        """播放录制中的增长文件（直播模式）。"""
        self._play(path, live=True)

    def stop_video(self) -> None:
        """停止视频播放。"""
        self._stop_internal()

    def is_playing(self) -> bool:
        """是否正在播放。"""
        return self._playing

    def toggle_play_pause(self) -> None:
        """切换播放/暂停状态。"""
        if self._playing:
            self.pause()
        else:
            self.resume()

    def seek_to(self, sec: float) -> None:
        """跳转到指定秒数。"""
        self.seek(sec)

    def position_sec(self) -> float:
        """当前播放位置（秒）。"""
        if self._mpv is not None:
            try:
                return float(self._mpv.time_pos or 0.0)
            except Exception as exc:
                _log.debug("mpv position_sec 失败: %s", exc)
        return 0.0

    def duration_sec(self) -> float:
        """视频总时长（秒）。"""
        if self._mpv is not None:
            try:
                return float(self._mpv.duration or 0.0)
            except Exception as exc:
                _log.debug("mpv duration_sec 失败: %s", exc)
        return 0.0

    def cleanup(self) -> None:
        """释放 mpv 资源。"""
        self._stop_internal()
        if self._mpv is not None:
            try:
                self._mpv.terminate()
            except Exception as exc:
                _log.warning("mpv terminate 失败: %s", exc)
            self._mpv = None

    def __del__(self) -> None:
        """Destructor: cleanup is handled by the destroyed() signal.

        Avoid touching libmpv/Qt objects here because destruction order
        during garbage collection is undefined.
        """
        pass

    # ── 内部实现 ───────────────────────────────────────────────

    def _play(self, path: str, *, live: bool) -> None:
        """统一播放入口。

        live/非 live 都设 ``loop-file=no``：直播流跟随写入头（mpv 自动跟进
        live edge，无需循环）；非直播文件单次播放。循环播放由
        ``set_live_mode``/``set_ab_loop`` 按需开启，不在此处处理。
        """
        self._live_mode = live
        self._current_path = path
        if self._mpv is not None:
            try:
                self._mpv["loop-file"] = "no"
                # Rebuild mpv instance if headers are set — ensures headers
                # are passed as startup options (required by some mpv versions
                # for reliable CDN access, especially Huya/Bilibili FLV streams).
                if self._stream_headers:
                    try:
                        self._mpv.terminate()
                    except Exception:
                        pass
                    self._mpv = self._create_mpv_instance(headers=self._stream_headers)
                self._mpv.play(path)
                self._mpv.pause = False
                self._playing = True
            except Exception as exc:
                _log.warning("mpv 播放失败 (%s): %s", path, exc)
                self._playing = False
        elif self._ffplay_path:
            self._play_with_ffplay(path)
        else:
            short = self._init_error or "预览不可用"
            detail = self._init_error_detail or "未检测到 libmpv"
            self._placeholder.setText(f"{short}\n{detail}")
            self._placeholder.show()

    def _play_with_ffplay(self, path: str) -> None:
        """Launch ffplay as an external fallback preview window."""
        self._stop_ffplay()
        cmd = [
            self._ffplay_path,
            "-loglevel", "warning",
            "-window_title", "LSC 直播预览",
            "-fflags", "nobuffer",
            "-flags", "low_delay",
        ]
        if self._stream_headers:
            header_blob = "".join(
                f"{key}: {value}\r\n"
                for key, value in self._stream_headers.items()
                if key and value
            )
            if header_blob:
                cmd += ["-headers", header_blob]
        cmd.append(path)

        try:
            from lsc.utils.process_launcher import prepare_launch

            env, creation_flags, cwd = prepare_launch(self._ffplay_path)
            self._ffplay_proc = subprocess.Popen(  # noqa: S603
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                cwd=cwd,
                creationflags=creation_flags,
            )
            self._playing = True
            self._placeholder.setText("正在使用 ffplay 外部窗口预览")
            self._placeholder.show()
        except Exception as exc:
            _log.warning("ffplay 预览启动失败 (%s): %s", path, exc)
            self._playing = False
            self._set_placeholder_error("预览启动失败", f"ffplay 启动失败：{exc}")

    def _stop_ffplay(self) -> None:
        proc = self._ffplay_proc
        self._ffplay_proc = None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception as exc:
            _log.debug("ffplay stop 失败: %s", exc)

    def _stop_internal(self) -> None:
        """内部停止实现。"""
        if self._mpv is not None:
            try:
                self._mpv.stop()
            except Exception as exc:
                _log.debug("mpv stop 失败: %s", exc)
        self._stop_ffplay()
        self._playing = False
        self._live_mode = False


__all__ = ["MpvWidget"]
