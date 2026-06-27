"""
MSE (Media Source Extensions) streamer for Electron preview.

Transcodes a live stream to fragmented MP4 and pushes segments
through a callback, allowing the Electron frontend to assemble
them via MediaSource API for smooth <video> playback.

Key design:
- FFmpeg outputs fragmented MP4 to a pipe
- Init segment (ftyp+moov) is captured first, sent once
- Subsequent moof+mdat segments are sent as they arrive
- Segment boundaries detected by ftyp/moof box markers
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
from typing import Callable

from lsc.utils.process_launcher import prepare_launch

_log = logging.getLogger(__name__)

# MP4 box type markers
_FTYP_MARKER = b'ftyp'
_MOOV_MARKER = b'moov'
_MOOF_MARKER = b'moof'
_MDAT_MARKER = b'mdat'

# Max segment size before forcing a split (512KB)
_MAX_SEGMENT_BYTES = 512 * 1024


class MseStreamer:
    """Streams a live source as fragmented MP4 segments via callback.

    Usage::

        streamer = MseStreamer(
            url="http://example.com/live.m3u8",
            on_init_segment=lambda data: ws.send(data),
            on_media_segment=lambda data: ws.send(data),
        )
        streamer.start()
        # ... later ...
        streamer.stop()
    """

    def __init__(
        self,
        url: str,
        on_init_segment: Callable[[bytes], None],
        on_media_segment: Callable[[bytes], None],
        on_error: Callable[[str], None] | None = None,
        width: int = 0,
        height: int = 0,
        fps: int = 0,
    ):
        self._url = url
        self._on_init = on_init_segment
        self._on_segment = on_media_segment
        self._on_error = on_error
        self._width = width
        self._height = height
        self._fps = fps
        self._ffmpeg_path = shutil.which("ffmpeg") or "ffmpeg"
        self._process: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._running = False
        self._init_sent = False
        # 缓存最近一次 init 段，支持前端错过 init 后通过 replay_init() 补发
        self._last_init_segment: bytes | None = None
        # 缓存 FFmpeg 最近 stderr 输出，异常退出时上报具体原因
        self._last_stderr = ""
        # 防止 _on_error 被多次调用（启动探测和 _read_segments 可能同时检测到退出）
        self._error_reported = False

    @property
    def is_running(self) -> bool:
        return self._running

    def replay_init(self) -> bool:
        """重发缓存的 init 段。返回 True 表示有缓存可发，False 表示尚无 init 段。

        用于消除 mse_init 早于 rooms_updated 到达前端导致的竞态：
        前端挂载 VideoPreview 后主动调用 request_mse_init，后端据此补发。
        _last_init_segment 为 bytes（不可变），只读访问线程安全。
        """
        if self._last_init_segment is None:
            return False
        self._on_init(self._last_init_segment)
        return True

    def start(self) -> bool:
        """Start the FFmpeg transcoding process and segment reader thread."""
        if self._running:
            _log.warning("MseStreamer already running")
            return False

        # Build scale filter if resolution specified
        vf_parts: list[str] = []
        if self._width > 0 and self._height > 0:
            vf_parts.append(f"scale={self._width}:{self._height}")

        vf_arg = [f"fps={self._fps}"] if self._fps > 0 else []

        # FFmpeg command for low-latency fMP4 output.
        # 直接映射直播流的音视频轨（-map 0:v / -map 0:a?），保留真实音频。
        # -map 0:a? 的 ? 后缀表示音频轨可选，无音频轨的直播流不会报错。
        # 增加 -re/-fflags +genpts/-thread_queue_size 以兼容直播平台 HLS/FLV 流；
        # HLS 直播从最新分片开始读取，避免等待过期分片。
        # 添加 timeout/rw_timeout 防止网络层面长时间挂起；
        # reconnect 选项在网络中断时自动重连。
        cmd = [
            self._ffmpeg_path,
            "-loglevel", "error",
            "-re",
            "-fflags", "+genpts",
            "-thread_queue_size", "1024",
            # 网络超时：连接超时 10s，读写超时 15s
            "-timeout", "10000000",
            "-rw_timeout", "15000000",
            # 断流自动重连（适用于 HLS/RTMP 流）
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
            "-i", self._url,
            "-map", "0:v",
            "-map", "0:a?",  # 音频轨可选，无音频时不报错
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-pix_fmt", "yuv420p",
            "-g", "30",  # keyframe every 30 frames (~1s at 30fps)
            "-c:a", "aac",
            "-b:a", "128k",
            "-ar", "44100",
            "-ac", "2",
            "-shortest",
            "-f", "mp4",
            "-movflags", "frag_keyframe+empty_moov+default_base_moof",
            "-frag_duration", "500000",  # 500ms fragments
            "pipe:1",
        ]
        if self._url.lower().endswith('.m3u8'):
            # 从 HLS 直播最新分片开始读取；插入到唯一的 -i 之前
            # 由于 FFmpeg 按位置解析选项，-live_start_index -1 必须在对应 -i 之前
            i_indices = [i for i, arg in enumerate(cmd) if arg == '-i']
            if len(i_indices) >= 1:
                insert_idx = i_indices[0]  # 唯一的 -i 位置
                # 注意 list.insert(idx, item) 是在 idx 前插入。
                # 要得到正确的顺序 [-live_start_index, -1, -i, url]，
                # 必须先 insert -1（它会在 idx 前），再 insert -live_start_index
                # （它会在 -1 前）。否则顺序会反过来变成 [-1, -live_start_index]。
                cmd.insert(insert_idx, '-1')
                cmd.insert(insert_idx, '-live_start_index')

        # Insert scale filter if needed
        if vf_parts:
            scale_str = ",".join(vf_parts)
            # 在 -c:v 之前插入 -vf scale_str
            insert_idx = -1
            for i, arg in enumerate(cmd):
                if arg == "-c:v":
                    insert_idx = i
                    break
            if insert_idx >= 0:
                cmd.insert(insert_idx, scale_str)
                cmd.insert(insert_idx, "-vf")

        try:
            env, creation_flags, cwd = prepare_launch(self._ffmpeg_path)
            # 诊断：打印完整 FFmpeg 命令，便于排查参数解析错误
            _log.info("FFmpeg command: %s", [str(c) for c in cmd])
            popen_kwargs: dict = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "env": env,
            }
            if creation_flags:
                popen_kwargs["creationflags"] = creation_flags
            if cwd:
                popen_kwargs["cwd"] = cwd
            self._process = subprocess.Popen(cmd, **popen_kwargs)
        except Exception as exc:
            _log.error("Failed to start FFmpeg: %s", exc)
            if self._on_error:
                self._on_error(f"MSE 流启动失败: {exc}")
            return False

        self._running = True
        self._init_sent = False
        self._last_stderr = ""
        self._thread = threading.Thread(target=self._read_segments, daemon=True)
        self._thread.start()
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stderr_thread.start()
        _log.info("MseStreamer started for %s", self._url[:80])

        # 启动探测：等 2 秒检查 FFmpeg 是否已退出（URL 无效/流离线时会快速退出）
        time.sleep(2)
        if self._process.poll() is not None:
            # FFmpeg 已退出，等 _read_stderr 线程收集完错误输出
            time.sleep(0.5)
            self._running = False
            if not self._error_reported:
                self._error_reported = True
                err_msg = self._last_stderr.strip()[:500] if self._last_stderr else "FFmpeg 进程异常退出"
                _log.error("FFmpeg exited immediately: %s", err_msg)
                if self._on_error:
                    self._on_error(f"流连接失败: {err_msg}")
            return False

        return True

    def _read_stderr(self) -> None:
        """读取 FFmpeg stderr，保留最近错误输出用于诊断。"""
        if self._process is None or self._process.stderr is None:
            return
        buf: list[str] = []
        try:
            while self._running:
                line = self._process.stderr.readline()
                if not line:
                    break
                buf.append(line.decode("utf-8", errors="replace").rstrip())
                if len(buf) > 100:
                    buf.pop(0)
        except Exception as exc:
            _log.warning("Error reading FFmpeg stderr: %s", exc)
        finally:
            self._last_stderr = "\n".join(buf[-20:])

    def stop(self) -> None:
        """Stop the streamer and clean up resources."""
        self._running = False
        proc = self._process
        if proc is not None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
            except Exception as exc:
                _log.warning("Error stopping FFmpeg: %s", exc)
            finally:
                self._process = None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2)
        if self._stderr_thread is not None and self._stderr_thread.is_alive():
            self._stderr_thread.join(timeout=2)
        _log.info("MseStreamer stopped")

    def _read_segments(self) -> None:
        """Read fMP4 output from FFmpeg stdout and emit segments."""
        if self._process is None or self._process.stdout is None:
            return

        # Buffer for incomplete reads
        buf = bytearray()
        # Track seen box types to detect init segment boundaries
        seen_ftyp = False

        try:
            while self._running:
                chunk = self._process.stdout.read(4096)
                if not chunk:
                    if self._process.poll() is not None:
                        # Process exited
                        stderr_output = b""
                        if self._process.stderr:
                            try:
                                stderr_output = self._process.stderr.read(4096)
                            except Exception:
                                pass
                        if self._running and not self._error_reported and self._on_error:
                            self._error_reported = True
                            # 优先使用 stderr 守护线程收集的完整日志，回退到一次性读取
                            err_msg = (self._last_stderr or stderr_output.decode("utf-8", errors="replace"))[:500]
                            _log.error("FFmpeg exited unexpectedly: %s", err_msg)
                            self._on_error(f"流编码异常终止: {err_msg}" if err_msg else "流编码异常终止")
                        break
                    time.sleep(0.01)
                    continue

                buf.extend(chunk)

                # Process complete segments from buffer
                while self._running and len(buf) > 8:
                    # Look for ftyp (init segment marker) or moof (media segment marker)
                    ftyp_idx = buf.find(_FTYP_MARKER)
                    moof_idx = buf.find(_MOOF_MARKER)

                    if not seen_ftyp and ftyp_idx != -1:
                        # Found ftyp - extract init segment
                        # The init segment starts at ftyp and goes through moov
                        init_start = max(0, ftyp_idx - 4)  # Include box size
                        moov_idx = buf.find(_MOOV_MARKER, ftyp_idx + 4)

                        if moov_idx != -1:
                            # Find the end of moov box
                            if moov_idx >= 4:
                                moov_size = int.from_bytes(buf[moov_idx - 4:moov_idx], "big")
                                init_end = moov_idx - 4 + moov_size

                                if init_end <= len(buf):
                                    init_data = bytes(buf[init_start:init_end])
                                    self._last_init_segment = init_data
                                    self._on_init(init_data)
                                    self._init_sent = True
                                    seen_ftyp = True
                                    buf = buf[init_end:]
                                    _log.info("Init segment sent (%d bytes)", len(init_data))
                                    continue

                    if seen_ftyp and moof_idx != -1 and moof_idx >= 4:
                        # Found moof - extract media segment (moof + mdat)
                        seg_start = moof_idx - 4
                        mdat_idx = buf.find(_MDAT_MARKER, moof_idx + 4)

                        if mdat_idx != -1 and mdat_idx > 4:
                            mdat_size = int.from_bytes(buf[mdat_idx - 4:mdat_idx], "big")
                            seg_end = mdat_idx - 4 + mdat_size

                            if seg_end <= len(buf):
                                seg_data = bytes(buf[seg_start:seg_end])
                                self._on_segment(seg_data)
                                buf = buf[seg_end:]
                                continue

                    # If buffer is too large without a valid segment, trim
                    if len(buf) > _MAX_SEGMENT_BYTES * 2:
                        next_ftyp = buf.find(_FTYP_MARKER, 4)
                        next_moof = buf.find(_MOOF_MARKER, 4)
                        trim_to = min(
                            next_ftyp if next_ftyp != -1 else len(buf),
                            next_moof if next_moof != -1 else len(buf),
                        )
                        if trim_to > 4 and trim_to < len(buf):
                            buf = buf[trim_to:]
                        break

                    break  # No complete segment yet, wait for more data

        except Exception as exc:
            _log.error("Segment reader error: %s", exc)
            if self._running and not self._error_reported and self._on_error:
                self._error_reported = True
                self._on_error(f"分段读取错误: {exc}")
        finally:
            self._running = False
