"""
MSE (Media Source Extensions) 流式传输服务 — 用于 Electron 预览。

通过 FFmpeg 将直播流转码为分片 MP4（fragmented MP4），
并通过 WebSocket 回调推送给 Electron 前端，
前端通过 MediaSource API 将片段组装到 <video> 元素实现平滑播放。

关键设计：
- FFmpeg 通过管道输出 fMP4 到 stdout
- 首先捕获 init segment（ftyp+moov），只发送一次
- 后续的 moof+mdat 片段实时推送到前端
- 通过 ftyp/moof 等 MP4 box 标记检测片段边界
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable

from lsc import get_logger
from lsc.config import load_config
from lsc.core.services.fmp4_segments import Fmp4SegmentParser
from lsc.platforms.base import headers_to_ffmpeg_input_args
from lsc.utils.process_launcher import prepare_launch, set_stream_nonblocking

_log = get_logger(__name__)

# Max segment size before forcing a split (512KB)
_MAX_SEGMENT_BYTES = 512 * 1024

# NVENC availability cache (checked once per process lifetime)
_nvenc_available: bool | None = None
_nvenc_lock = threading.Lock()


def _check_nvenc() -> bool:
    """Quick test: can FFmpeg use h264_nvenc on this system?"""
    global _nvenc_available
    with _nvenc_lock:
        if _nvenc_available is not None:
            return _nvenc_available
        try:
            cfg = load_config()
            ffmpeg = cfg.ffmpeg_path or shutil.which("ffmpeg") or "ffmpeg"
            if not ffmpeg or not os.path.isfile(ffmpeg):
                _nvenc_available = False
                return False
            env, creation_flags, cwd = prepare_launch(ffmpeg)
            run_kwargs: dict = {
                "capture_output": True,
                "text": True,
                "timeout": 10,
                "env": env,
            }
            if cwd:
                run_kwargs["cwd"] = cwd
            if creation_flags:
                run_kwargs["creationflags"] = creation_flags
            result = subprocess.run(
                [ffmpeg, "-y", "-loglevel", "error",
                 "-f", "lavfi", "-i", "testsrc=duration=1:size=256x256:rate=1",
                 "-c:v", "h264_nvenc", "-frames:v", "1",
                 "-f", "null", "-"],
                **run_kwargs,
            )
            _nvenc_available = result.returncode == 0
        except Exception:
            _nvenc_available = False
        return _nvenc_available


class MseStreamer:
    """将直播源以 fMP4 片段形式通过回调推送到前端。

    使用方式::

        streamer = MseStreamer(
            url="http://example.com/live.m3u8",
            on_init_segment=lambda data: ws.send(data),
            on_media_segment=lambda data: ws.send(data),
        )
        streamer.start()
        # ... later ...
        streamer.stop()

    fMP4（分片 MP4）格式说明：
    - init segment（ftyp + moov）：包含编解码器信息和样本表，只发送一次
    - media segment（moof + mdat）：包含实际的音视频帧，持续生成
    - 每个片段通过 MP4 box 标记（ftyp/moof/mdat）自动识别边界
    - 前端通过 MediaSource API 按序追加片段实现低延迟直播
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
        headers: dict[str, str] | None = None,
        video_bitrate: str = "",
        crf_value: int = 0,
    ):
        self._url = url
        self._on_init = on_init_segment
        self._on_segment = on_media_segment
        self._on_error = on_error
        self._width = width
        self._height = height
        self._fps = fps
        self._headers = headers
        self._video_bitrate = video_bitrate
        self._crf_value = crf_value
        cfg = load_config()
        self._ffmpeg_path = cfg.ffmpeg_path or shutil.which("ffmpeg") or "ffmpeg"
        self._process: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._watchdog_thread: threading.Thread | None = None
        self._running = False
        self._init_sent = False
        self._segment_parser = Fmp4SegmentParser(max_buffer_bytes=_MAX_SEGMENT_BYTES)
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

    def start(self, startup_probe_timeout: float = 2.0) -> bool:
        """启动 FFmpeg 转码进程和分段读取线程。

        构建 FFmpeg 命令行并启动，随后启动后台线程读取 fMP4 输出。
        包含启动探测逻辑，等待 init segment 产出或 FFmpeg 异常退出。

        Args:
            startup_probe_timeout: 启动探测超时时间（秒）

        Returns:
            True 表示启动成功，False 表示 FFmpeg 异常退出
        """
        if self._running:
            _log.warning("MseStreamer already running")
            return False

        # Build scale filter if resolution specified
        # 直播 URL 上不要强制 -hwaccel cuda / scale_cuda：CDN/HLS 硬解失败率高，
        # 会导致 MSE init 永远不就绪。预览仍用 NVENC 编码降 CPU。
        vf_parts: list[str] = []
        if self._width > 0 and self._height > 0:
            vf_parts.append(f"scale={self._width}:{self._height}:force_original_aspect_ratio=decrease")
        if self._fps > 0:
            vf_parts.append(f"fps={self._fps}")

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
        ]

        # 插入 HTTP headers（B站/虎牙/斗鱼 CDN 强制检查 Referer，缺少会 403）
        if self._headers:
            cmd += headers_to_ffmpeg_input_args(self._headers)

        cmd += [
            "-i", self._url,
            "-map", "0:v",
            "-map", "0:a?",  # 音频轨可选，无音频时不报错
        ]

        # 编码器选择：NVENC 硬件编码优先（大幅降低 CPU 占用），不可用时回退到 libx264 软编码
        use_nvenc = _check_nvenc()
        if use_nvenc:
            cmd += [
                "-c:v", "h264_nvenc",
                "-preset", "p4",       # 平衡速度与质量
                "-tune", "ll",          # 低延迟
                "-rc", "cbr",
                "-b:v", self._video_bitrate or "2500k",
                "-maxrate", str(int((self._video_bitrate or "2500k").replace("k", "")) * 12 // 10) + "k",
                "-bufsize", str(int((self._video_bitrate or "2500k").replace("k", "")) * 2) + "k",
            ]
        else:
            crf = self._crf_value or 28
            bitrate = self._video_bitrate or "1500k"
            max_bitrate = str(int(bitrate.replace("k", "")) * 4 // 3) + "k"
            cmd += [
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", str(crf),
                "-b:v", bitrate,
                "-maxrate", max_bitrate,
                "-bufsize", str(int(bitrate.replace("k", "")) * 2) + "k",
            ]

        cmd += [
            "-pix_fmt", "yuv420p",
            "-g", "30",  # keyframe every 30 frames (~1s at 30fps)
            "-c:a", "aac",
            "-b:a", "128k",
            "-ar", "44100",
            "-ac", "2",
            "-shortest",
            "-f", "mp4",
            "-movflags", "frag_keyframe+empty_moov+default_base_moof",
            "-frag_duration", "1000000",  # 1000ms fragments
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
            set_stream_nonblocking(self._process.stdout)
            set_stream_nonblocking(self._process.stderr)
        except Exception as exc:
            _log.error("Failed to start FFmpeg: %s", exc)
            if self._on_error:
                self._on_error(f"MSE 流启动失败: {exc}")
            return False

        self._running = True
        self._init_sent = False
        self._segment_parser = Fmp4SegmentParser(max_buffer_bytes=_MAX_SEGMENT_BYTES)
        self._last_stderr = ""
        self._thread = threading.Thread(target=self._read_segments, daemon=True)
        self._thread.start()
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stderr_thread.start()
        _log.info("MseStreamer started for %s", self._url[:80])

        # 启动探测：轮询检查 init segment 是否已产出或 FFmpeg 是否已退出
        # 比固定 sleep 更快——init 产出后立即返回，不必等满 probe_timeout
        deadline = time.monotonic() + startup_probe_timeout
        while time.monotonic() < deadline:
            if self._init_sent:
                return True
            proc = self._process
            if proc is not None and proc.poll() is not None:
                time.sleep(0.3)
                self._running = False
                if not self._error_reported:
                    self._error_reported = True
                    err_msg = self._last_stderr.strip()[:500] if self._last_stderr else "FFmpeg 进程异常退出"
                    _log.error("FFmpeg exited immediately: %s", err_msg)
                    if self._on_error:
                        self._on_error(f"流连接失败: {err_msg}")
                return False
            time.sleep(0.15)

        # 超时但未退出 → 假定成功（init 可能在探测后才产出）
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
                # 显式关闭管道，防止文件描述符泄漏（Windows 上尤为重要）
                try:
                    if proc.stdout:
                        proc.stdout.close()
                except Exception as exc:
                    _log.warning("MSE stdout pipe close failed: %s", exc)
                try:
                    if proc.stderr:
                        proc.stderr.close()
                except Exception as exc:
                    _log.warning("MSE stderr pipe close failed: %s", exc)
                self._process = None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2)
        if self._stderr_thread is not None and self._stderr_thread.is_alive():
            self._stderr_thread.join(timeout=2)
        if self._watchdog_thread is not None and self._watchdog_thread.is_alive():
            self._watchdog_thread.join(timeout=2)
        _log.info("MseStreamer stopped")

    def _read_segments(self) -> None:
        """从 FFmpeg stdout 读取 fMP4 输出并按片段分发。

        读取线程以 daemon 方式运行，持续从 FFmpeg 的 stdout 管道阻塞读取数据。
        通过识别 ftyp/moof/mdat 等 MP4 box 标记来分割片段：
        - ftyp + moov 组成 init segment，只发送一次
        - moof + mdat 组成 media segment，持续发送

        包含独立 watchdog 线程做无数据超时检测（15 秒），
        当 FFmpeg 停止产出数据时强杀进程以解除阻塞读取，并上报错误。
        """
        # 保存局部引用，避免 stop() 并发置 None 导致 AttributeError
        proc = self._process
        if proc is None or proc.stdout is None:
            return

        # Buffer for incomplete reads
        # Track seen box types to detect init segment boundaries
        # 上次收到数据的时间戳（用 list 包装以便 watchdog 闭包修改）
        # Python GIL 保证 float 赋值原子性，list[0] 读写同样安全
        last_data_time = [time.monotonic()]
        _NO_DATA_TIMEOUT = 15.0  # 15 秒无数据则认为 FFmpeg hang 住

        # ── Watchdog 线程：监控 stdout 数据流，超时则强杀 FFmpeg ──
        def _watchdog() -> None:
            while self._running:
                if proc.poll() is not None:
                    return
                if time.monotonic() - last_data_time[0] > _NO_DATA_TIMEOUT:
                    if not self._error_reported and self._on_error:
                        self._error_reported = True
                        _log.error(
                            "FFmpeg stdout read timeout (%ds): no data received",
                            _NO_DATA_TIMEOUT,
                        )
                        self._on_error("流编码无响应：读取超时")
                    # 强杀 FFmpeg → stdout 管道关闭 → 阻塞 read() 返回空 → 主线程退出
                    try:
                        proc.kill()
                    except Exception as exc:
                        _log.warning("MSE watchdog kill failed: %s", exc)
                    # D-4: 显式关闭 stdout 管道，确保即使 kill 信号未及时生效，
                    # 阻塞的 read() 也能立即返回，避免读取线程永久挂起
                    try:
                        if proc.stdout:
                            proc.stdout.close()
                    except Exception as exc:
                        _log.warning("MSE watchdog stdout close failed: %s", exc)
                    return
                time.sleep(1.0)

        self._watchdog_thread = threading.Thread(target=_watchdog, daemon=True)
        self._watchdog_thread.start()

        try:
            while self._running:
                # 阻塞读取：FFmpeg 产出数据时立即返回，被 kill 后返回空
                try:
                    chunk = proc.stdout.read(65536)
                except (OSError, ValueError):
                    break

                if not chunk:
                    # FFmpeg 退出或管道关闭
                    if proc.poll() is not None:
                        stderr_output = b""
                        if proc.stderr:
                            try:
                                stderr_output = proc.stderr.read(4096)
                            except Exception as exc:
                                _log.debug("操作异常（已忽略）: %s", exc)
                        if self._running and not self._error_reported and self._on_error:
                            self._error_reported = True
                            err_msg = (
                                self._last_stderr
                                or stderr_output.decode("utf-8", errors="replace")
                            )[:500]
                            _log.error("FFmpeg exited unexpectedly: %s", err_msg)
                            self._on_error(
                                f"流编码异常终止: {err_msg}" if err_msg else "流编码异常终止"
                            )
                    break

                last_data_time[0] = time.monotonic()
                for segment in self._segment_parser.feed(chunk):
                    if segment.kind == "init":
                        self._last_init_segment = segment.data
                        self._on_init(segment.data)
                        self._init_sent = True
                        _log.info("Init segment sent (%d bytes)", len(segment.data))
                    elif segment.kind == "media":
                        self._on_segment(segment.data)

        except Exception as exc:
            _log.error("Segment reader error: %s", exc)
            if self._running and not self._error_reported and self._on_error:
                self._error_reported = True
                self._on_error(f"分段读取错误: {exc}")
        finally:
            self._running = False
