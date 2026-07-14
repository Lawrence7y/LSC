from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from lsc import get_logger
from lsc.config import ExportProfile, load_config, preferred_hw_video_codec
from lsc.core.services.fmp4_segments import Fmp4SegmentParser
from lsc.platforms.base import headers_to_ffmpeg_input_args
from lsc.utils.process_launcher import prepare_launch

_log = get_logger(__name__)
STARTUP_PROBE_TIMEOUT_SEC = 10.0
STARTUP_PROBE_INTERVAL_SEC = 0.2
TS_PACKET_SIZE = 188
_WRITE_TIMEOUT_SEC = 10.0


def _is_network_url(url: str) -> bool:
    return url.startswith(("http://", "https://"))


def _network_input_args(url: str) -> list[str]:
    if not _is_network_url(url):
        return []
    return [
        "-timeout", "10000000",
        "-rw_timeout", "15000000",
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
    ]


def _scaled_kbitrate(value: str, numerator: int, denominator: int = 1) -> str:
    return f"{int(value.replace('k', '')) * numerator // denominator}k"


@dataclass(frozen=True)
class SharedIngestStartResult:
    ok: bool
    use_legacy_fallback: bool = False
    error: str = ""


class PreviewSubscriber:
    def __init__(self, max_bytes: int, drop_policy: str = "drop_oldest"):
        self.max_bytes = max(1, int(max_bytes))
        self.drop_policy = drop_policy
        self.queue: deque[tuple[str, bytes]] = deque()
        self.queued_bytes = 0
        self.dropped_segments = 0
        self._lock = threading.RLock()
        self._drain_condition = threading.Condition(self._lock)

    def push(self, kind: str, data: bytes) -> None:
        with self._lock:
            data_len = len(data)
            if data_len > self.max_bytes:
                self.dropped_segments += 1
                return
            if self.drop_policy == "drop_newest" and self.queued_bytes + data_len > self.max_bytes:
                self.dropped_segments += 1
                return
            while self.queue and self.queued_bytes + data_len > self.max_bytes:
                _, old = self.queue.popleft()
                self.queued_bytes -= len(old)
                self.dropped_segments += 1
            self.queue.append((kind, data))
            self.queued_bytes += data_len
            self._drain_condition.notify()

    def drain(self) -> list[tuple[str, bytes]]:
        with self._lock:
            items = list(self.queue)
            self.queue.clear()
            self.queued_bytes = 0
            return items

    def wait_for_data(self, timeout: float = 0.1) -> bool:
        """等待新数据到达。返回 True 表示有数据，False 表示超时。"""
        with self._lock:
            if self.queue:
                return True
            self._drain_condition.wait(timeout=timeout)
            return bool(self.queue)


class SharedPreviewHandle:
    def __init__(
        self,
        ingest: SharedRoomIngest,
        on_init_segment: Callable[[bytes], None],
        on_media_segment: Callable[[bytes], None],
        on_error: Callable[[str], None] | None = None,
        pump_interval_sec: float = 0.05,
        auto_start: bool = False,
    ):
        self._ingest = ingest
        self._on_init = on_init_segment
        self._on_segment = on_media_segment
        self._on_error = on_error
        self._subscriber = ingest.attach_preview_subscriber()
        self._pump_interval_sec = max(0.005, float(pump_interval_sec))
        self._stop_event = threading.Event()
        self._pump_thread: threading.Thread | None = None
        self._stopped = False
        self._error_reported = False
        if auto_start:
            self.start()

    @property
    def is_running(self) -> bool:
        return (
            not self._stopped
            and not self._ingest.is_stopped
            and not self._ingest.preview_error
            and not self._ingest.upstream_error
        )

    def replay_init(self) -> bool:
        segment = self._ingest.last_init_segment
        if segment is None:
            return False
        self._on_init(segment)
        return True

    def drain(self) -> None:
        if self._stopped:
            return
        for kind, data in self._subscriber.drain():
            try:
                if kind == "init":
                    self._on_init(data)
                elif kind == "media":
                    self._on_segment(data)
            except Exception as exc:
                _log.debug("shared preview callback failed room=%s: %s", self._ingest.room_id, exc)
                self.stop()
                return

    def start(self) -> None:
        if self._stopped:
            return
        if self._pump_thread is not None and self._pump_thread.is_alive():
            return
        self._pump_thread = threading.Thread(
            target=self._pump_loop,
            name=f"shared-preview-pump-{self._ingest.room_id}",
            daemon=True,
        )
        self._pump_thread.start()

    def _pump_loop(self) -> None:
        """使用条件通知的 preview pump — 有新数据时立即唤醒，避免固定轮询开销。"""
        while not self._stop_event.is_set():
            if self._ingest.preview_error or self._ingest.upstream_error or self._ingest.is_stopped:
                self._report_error_if_needed()
                self.stop()
                return
            # 等待新数据或停止信号
            has_data = self._subscriber.wait_for_data(timeout=self._pump_interval_sec)
            if has_data:
                self.drain()

    def _report_error_if_needed(self) -> None:
        if self._error_reported or self._stopped or self._on_error is None:
            return
        error = self._ingest.preview_error or self._ingest.upstream_error or self._ingest.stop_reason
        if not error:
            return
        self._error_reported = True
        try:
            self._on_error(error)
        except Exception as exc:
            _log.debug("shared preview error callback failed room=%s: %s", self._ingest.room_id, exc)

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._stop_event.set()
        self._ingest.detach_preview_subscriber(self._subscriber)
        thread = self._pump_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1)


class SharedRoomIngest:
    def __init__(
        self,
        room_id: str,
        url: str,
        headers: dict[str, str] | None = None,
        preview_queue_bytes: int = 2 * 1024 * 1024,
        preview_drop_policy: str = "drop_oldest",
    ):
        self.room_id = room_id
        self.url = url
        self.headers = dict(headers or {})
        self.preview_queue_bytes = max(1, int(preview_queue_bytes))
        self.preview_drop_policy = preview_drop_policy
        self.is_stopped = False
        self.stop_reason = ""
        self.preview_error = ""
        self.recording_error = ""
        self.upstream_error = ""
        self.recording_active = False
        self.recording_media_start_mono = 0.0
        self.last_init_segment: bytes | None = None
        self.preview_dropped_bytes = 0
        self.preview_dropped_batches = 0

        self._lock = threading.RLock()
        self._preview_condition = threading.Condition(self._lock)
        self._preview_subscribers: list[PreviewSubscriber] = []
        self._preview_ts_queue: deque[bytes] = deque()
        self._preview_queued_bytes = 0

        self._process: Any | None = None
        self._recording_process: Any | None = None
        self._preview_process: Any | None = None
        self._recording_path = ""
        self._last_command: list[str] = []
        self._last_recording_command: list[str] = []
        self._last_preview_command: list[str] = []
        self._last_error = ""

        self._preview_options: dict[str, Any] = {
            "width": 0,
            "height": 0,
            "use_nvenc": False,
            "video_bitrate": "",
            "crf_value": 0,
            "preview_pipe": "pipe:1",
        }
        self._preview_requested = False
        self._preview_parser = Fmp4SegmentParser()
        self._upstream_thread: threading.Thread | None = None
        self._upstream_watch_thread: threading.Thread | None = None
        self._recording_watch_thread: threading.Thread | None = None
        self._preview_input_thread: threading.Thread | None = None
        self._preview_thread: threading.Thread | None = None
        self._preview_watch_thread: threading.Thread | None = None
        self._stderr_threads: list[threading.Thread] = []
        self._stderr_buffer: deque[str] = deque(maxlen=100)
        self._recording_stderr_buffer: deque[str] = deque(maxlen=100)
        self._preview_stderr_buffer: deque[str] = deque(maxlen=100)

    @property
    def preview_subscribers(self) -> int:
        with self._lock:
            return len(self._preview_subscribers)

    @property
    def process_id(self) -> int | None:
        with self._lock:
            proc = self._process
        return getattr(proc, "pid", None) if proc is not None else None

    @property
    def recording_process_id(self) -> int | None:
        with self._lock:
            proc = self._recording_process
        return getattr(proc, "pid", None) if proc is not None else None

    @property
    def preview_process_id(self) -> int | None:
        with self._lock:
            proc = self._preview_process
        return getattr(proc, "pid", None) if proc is not None else None

    @property
    def last_command(self) -> list[str]:
        with self._lock:
            return list(self._last_command)

    @property
    def last_upstream_command(self) -> list[str]:
        return self.last_command

    @property
    def last_recording_command(self) -> list[str]:
        with self._lock:
            return list(self._last_recording_command)

    @property
    def recording_last_command(self) -> list[str]:
        return self.last_recording_command

    @property
    def last_preview_command(self) -> list[str]:
        with self._lock:
            return list(self._last_preview_command)

    @property
    def preview_last_command(self) -> list[str]:
        return self.last_preview_command

    def configure_preview(
        self,
        width: int = 0,
        height: int = 0,
        use_nvenc: bool | None = None,
        video_bitrate: str = "",
        crf_value: int = 0,
        fps: int = 0,
    ) -> None:
        if use_nvenc is None:
            from lsc.core.services.mse_streamer import _check_nvenc
            use_nvenc = _check_nvenc()
        with self._lock:
            self._preview_options = {
                "width": width,
                "height": height,
                "use_nvenc": use_nvenc,
                "video_bitrate": video_bitrate,
                "crf_value": crf_value,
                "fps": fps,
                "preview_pipe": "pipe:1",
            }

    def attach_preview_subscriber(self) -> PreviewSubscriber:
        subscriber = PreviewSubscriber(
            max_bytes=self.preview_queue_bytes,
            drop_policy=self.preview_drop_policy,
        )
        with self._lock:
            self._preview_subscribers.append(subscriber)
            first = len(self._preview_subscribers) == 1
            should_start = first and (
                self._preview_requested
                or self._process is not None
                or self._recording_process is not None
            )
            options = dict(self._preview_options)
        if should_start:
            result = self.start_preview(**options)
            if not result.ok:
                _log.warning("shared preview start failed room=%s: %s", self.room_id, result.error)
        return subscriber

    def detach_preview_subscriber(self, subscriber: PreviewSubscriber) -> None:
        with self._lock:
            if subscriber in self._preview_subscribers:
                self._preview_subscribers.remove(subscriber)
            last = not self._preview_subscribers
        if last:
            self.stop_preview_sink(reason="last preview subscriber detached")

    def publish_preview_segment(self, data: bytes, kind: str = "media") -> None:
        with self._lock:
            if kind == "init":
                self.last_init_segment = data
            subscribers = list(self._preview_subscribers)
        for subscriber in subscribers:
            subscriber.push(kind=kind, data=data)

    def handle_preview_error(self, error: str) -> None:
        with self._lock:
            proc = self._preview_process
        self._handle_preview_process_exit(proc, error) if proc is not None else self._set_preview_error(error)

    def _set_preview_error(self, error: str) -> None:
        with self._lock:
            self.preview_error = error
        self._stop_upstream_if_idle(reason=error)

    def handle_upstream_error(self, error: str, proc: Any | None = None) -> None:
        with self._lock:
            if proc is not None and self._process is not proc:
                return
            tail = self._stderr_tail(self._stderr_buffer)
            self.upstream_error = f"{error} | stderr: {tail}" if tail else error
        self.stop(reason=self.upstream_error)

    def _get_stderr_tail(self, lines: int = 20) -> str:
        return self._stderr_tail(self._stderr_buffer, lines)

    @staticmethod
    def _stderr_tail(buffer: deque[str], lines: int = 20) -> str:
        return " | ".join(list(buffer)[-lines:]) if buffer else ""

    def _ffmpeg_path(self) -> str:
        cfg = load_config()
        return cfg.ffmpeg_path or shutil.which("ffmpeg") or "ffmpeg"

    def build_upstream_command(self) -> list[str]:
        command = [
            self._ffmpeg_path(),
            "-y",
            "-loglevel", "warning",
            "-fflags", "+genpts",
            "-thread_queue_size", "1024",
        ]
        command += _network_input_args(self.url)
        if self.headers:
            command += headers_to_ffmpeg_input_args(self.headers)
        command += [
            "-i", self.url,
            "-map", "0:v",
            "-map", "0:a?",
            "-c", "copy",
            "-f", "mpegts",
            "-mpegts_flags", "+resend_headers",
            "-pat_period", "0.1",
            "pipe:1",
        ]
        return command

    def build_recording_command(
        self,
        recording_path: str,
        profile: ExportProfile | None = None,
    ) -> list[str]:
        profile = profile or ExportProfile(codec="copy")
        filter_args = profile.ffmpeg_filter_args()
        if profile.is_copy and filter_args:
            # copy 无法叠加滤镜；优先 NVENC，避免默认 libx264 打满 CPU
            hw = preferred_hw_video_codec()
            effective_profile = replace(profile, codec=hw)
            _log.info(
                "shared ingest recording: copy+filter → reencode with %s",
                hw,
            )
        else:
            effective_profile = profile
        return [
            self._ffmpeg_path(),
            "-y",
            "-loglevel", "warning",
            "-fflags", "+genpts",
            "-thread_queue_size", "1024",
            "-f", "mpegts",
            "-i", "pipe:0",
            "-map", "0:v",
            "-map", "0:a?",
            *effective_profile.ffmpeg_video_args(),
            *effective_profile.ffmpeg_audio_args(),
            *filter_args,
            "-f", "mp4",
            "-movflags", "frag_keyframe+empty_moov+faststart",
            recording_path,
        ]

    def build_preview_command(
        self,
        width: int = 0,
        height: int = 0,
        use_nvenc: bool | None = None,
        video_bitrate: str = "",
        crf_value: int = 0,
        preview_pipe: str = "pipe:1",
    ) -> list[str]:
        cfg = load_config()
        if use_nvenc is None:
            from lsc.core.services.mse_streamer import _check_nvenc
            use_nvenc = _check_nvenc()
        effective_crf = crf_value or cfg.shared_ingest_preview_crf
        effective_preset = cfg.shared_ingest_preview_preset or "veryfast"
        # 注意：mpegts pipe 输入上不要加 -hwaccel cuda / scale_cuda。
        # 直播管道硬解极易失败，表现为预览进程秒退、mse_init 永远不就绪。
        command = [
            self._ffmpeg_path(),
            "-y",
            "-loglevel", "warning",
            "-fflags", "+genpts",
            "-thread_queue_size", "1024",
            "-f", "mpegts",
            "-i", "pipe:0",
            "-map", "0:v",
            "-map", "0:a?",
        ]
        if width > 0 and height > 0:
            command += [
                "-vf",
                f"scale={width}:{height}:force_original_aspect_ratio=decrease",
            ]
        if use_nvenc:
            bitrate = video_bitrate or "2500k"
            command += [
                "-c:v", "h264_nvenc",
                "-preset", "p4",
                "-tune", "ll",
                "-rc", "cbr",
                "-b:v", bitrate,
                "-maxrate", _scaled_kbitrate(bitrate, 12, 10),
                "-bufsize", _scaled_kbitrate(bitrate, 2),
            ]
        else:
            bitrate = video_bitrate or "1500k"
            command += [
                "-c:v", "libx264",
                "-preset", effective_preset,
                "-crf", str(effective_crf),
                "-b:v", bitrate,
                "-maxrate", _scaled_kbitrate(bitrate, 4, 3),
                "-bufsize", _scaled_kbitrate(bitrate, 2),
            ]
        command += [
            "-pix_fmt", "yuv420p",
            "-g", "30",
            "-c:a", "aac",
            "-b:a", "128k",
            "-ar", "44100",
            "-ac", "2",
            "-shortest",
            "-f", "mp4",
            "-movflags", "frag_keyframe+empty_moov+default_base_moof",
            "-frag_duration", "1000000",
            preview_pipe,
        ]
        return command

    def build_ffmpeg_command(
        self,
        recording_path: str,
        preview_pipe: str = "pipe:1",
        profile: ExportProfile | None = None,
    ) -> list[str]:
        del preview_pipe
        return self.build_recording_command(recording_path, profile)

    def build_preview_only_command(self, preview_pipe: str = "pipe:1", **kwargs) -> list[str]:
        return self.build_preview_command(preview_pipe=preview_pipe, **kwargs)

    def start_recording_and_preview(
        self,
        recording_path: str,
        preview_pipe: str = "pipe:1",
        profile: ExportProfile | None = None,
    ) -> SharedIngestStartResult:
        del preview_pipe
        return self.start_recording(recording_path, profile=profile)

    def start_recording(
        self,
        recording_path: str,
        profile: ExportProfile | None = None,
    ) -> SharedIngestStartResult:
        with self._lock:
            if self._recording_process is not None and self.recording_active:
                return SharedIngestStartResult(ok=True)
            self.recording_error = ""
            self.recording_media_start_mono = 0.0
        command = self.build_recording_command(recording_path, profile)
        try:
            proc = self._launch_process(command)
        except Exception as exc:
            return self._recording_start_failed(f"recording ffmpeg start failed: {exc}")
        return_code = self._poll(proc)
        if return_code is not None:
            self._terminate_process_object(proc)
            return self._recording_start_failed(
                f"recording ffmpeg exited immediately: code={return_code}"
            )

        with self._lock:
            self._recording_process = proc
            self._recording_path = recording_path
            self._last_recording_command = list(command)
            self.recording_active = True
            self.is_stopped = False
            self.stop_reason = ""
        self._start_stderr_reader(proc, self._recording_stderr_buffer, "recording")
        self._recording_watch_thread = self._start_thread(
            self._watch_recording_process_loop,
            (proc,),
            f"shared-recording-watch-{self.room_id}",
        )

        upstream_error = self._ensure_upstream_started()
        if upstream_error:
            self._stop_recording_process()
            return self._recording_start_failed(upstream_error)
        if not self._wait_for_startup_data(recording_path):
            tail = self._stderr_tail(self._recording_stderr_buffer)
            error = "recording ffmpeg startup probe failed"
            if tail:
                error = f"{error} | stderr: {tail}"
            self._stop_recording_process()
            self._stop_upstream_if_idle(reason=error)
            return self._recording_start_failed(error)

        _log.info(
            "shared recording started room=%s upstream_pid=%s recording_pid=%s",
            self.room_id,
            self.process_id,
            self.recording_process_id,
        )
        return SharedIngestStartResult(ok=True)

    def _recording_start_failed(self, error: str) -> SharedIngestStartResult:
        with self._lock:
            self.recording_error = error
            self._last_error = error
            self.recording_active = False
        self._stop_upstream_if_idle(reason=error)
        return SharedIngestStartResult(ok=False, use_legacy_fallback=False, error=error)

    def start_preview(
        self,
        width: int = 0,
        height: int = 0,
        use_nvenc: bool | None = None,
        video_bitrate: str = "",
        crf_value: int = 0,
        fps: int = 0,
        preview_pipe: str = "pipe:1",
    ) -> SharedIngestStartResult:
        if use_nvenc is None:
            from lsc.core.services.mse_streamer import _check_nvenc
            use_nvenc = _check_nvenc()
        options = {
            "width": width,
            "height": height,
            "use_nvenc": use_nvenc,
            "video_bitrate": video_bitrate,
            "crf_value": crf_value,
            "preview_pipe": preview_pipe,
        }
        with self._lock:
            self._preview_options = options
            self._preview_requested = True
            if not self._preview_subscribers:
                return SharedIngestStartResult(ok=True)
            if self._preview_process is not None and self._poll(self._preview_process) is None:
                return SharedIngestStartResult(ok=True)
            self.preview_error = ""
        command = self.build_preview_command(**options)
        try:
            proc = self._launch_process(command)
        except Exception as exc:
            return self._preview_start_failed(f"preview ffmpeg start failed: {exc}")
        return_code = self._poll(proc)
        if return_code is not None:
            self._terminate_process_object(proc)
            return self._preview_start_failed(f"preview ffmpeg exited immediately: code={return_code}")

        with self._preview_condition:
            self._preview_process = proc
            self._last_preview_command = list(command)
            self._preview_parser = Fmp4SegmentParser()
            self.last_init_segment = None
            self._preview_ts_queue.clear()
            self._preview_queued_bytes = 0
            self.is_stopped = False
            self.stop_reason = ""
        self._start_stderr_reader(proc, self._preview_stderr_buffer, "preview")
        self._preview_thread = self._start_thread(
            self._read_preview_stdout_loop,
            (proc,),
            f"shared-preview-output-{self.room_id}",
        )
        self._preview_input_thread = self._start_thread(
            self._write_preview_input_loop,
            (proc,),
            f"shared-preview-input-{self.room_id}",
        )
        self._preview_watch_thread = self._start_thread(
            self._watch_preview_process_loop,
            (proc,),
            f"shared-preview-watch-{self.room_id}",
        )

        upstream_error = self._ensure_upstream_started()
        if upstream_error:
            self._stop_preview_process()
            return self._preview_start_failed(upstream_error)
        return_code = self._poll(proc)
        if return_code is not None:
            error = f"preview ffmpeg exited immediately: code={return_code}"
            self._handle_preview_process_exit(proc, error)
            return SharedIngestStartResult(ok=False, use_legacy_fallback=False, error=error)

        _log.info(
            "shared preview started room=%s upstream_pid=%s preview_pid=%s",
            self.room_id,
            self.process_id,
            self.preview_process_id,
        )
        return SharedIngestStartResult(ok=True)

    def _preview_start_failed(self, error: str) -> SharedIngestStartResult:
        with self._lock:
            self.preview_error = error
            self._last_error = error
        self._stop_upstream_if_idle(reason=error)
        return SharedIngestStartResult(ok=False, use_legacy_fallback=False, error=error)

    def start_preview_only(self, preview_pipe: str = "pipe:1") -> SharedIngestStartResult:
        return self.start_preview(preview_pipe=preview_pipe)

    def _start_preview_only_ffmpeg(self, preview_pipe: str = "pipe:1") -> bool:
        return self.start_preview(preview_pipe=preview_pipe).ok

    def _ensure_upstream_started(self) -> str:
        with self._lock:
            current = self._process
        if current is not None:
            return_code = self._poll(current)
            if return_code is None:
                return ""
            error = f"shared ingest upstream ffmpeg exited: code={return_code}"
            self.handle_upstream_error(error, current)
            return error

        command = self.build_upstream_command()
        try:
            proc = self._launch_process(command)
        except Exception as exc:
            return f"upstream ffmpeg start failed: {exc}"
        return_code = self._poll(proc)
        if return_code is not None:
            self._terminate_process_object(proc)
            return f"upstream ffmpeg exited immediately: code={return_code}"

        with self._lock:
            self._process = proc
            self._last_command = list(command)
            self.upstream_error = ""
            self.is_stopped = False
            self.stop_reason = ""
        self._start_stderr_reader(proc, self._stderr_buffer, "upstream")
        self._upstream_thread = self._start_thread(
            self._read_upstream_stdout_loop,
            (proc,),
            f"shared-upstream-output-{self.room_id}",
        )
        self._upstream_watch_thread = self._start_thread(
            self._watch_upstream_process_loop,
            (proc,),
            f"shared-upstream-watch-{self.room_id}",
        )
        return ""

    def _launch_process(self, command: list[str]):
        from lsc.utils.process_launcher import set_stream_nonblocking
        ffmpeg_path = command[0]
        env, creation_flags, cwd = prepare_launch(ffmpeg_path)
        popen_kwargs: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "env": env,
        }
        if creation_flags:
            popen_kwargs["creationflags"] = creation_flags
        if cwd:
            popen_kwargs["cwd"] = cwd
        proc = subprocess.Popen(command, **popen_kwargs)  # noqa: S603
        set_stream_nonblocking(proc.stdin)
        return proc

    @staticmethod
    def _start_thread(target, args: tuple, name: str) -> threading.Thread:
        thread = threading.Thread(target=target, args=args, name=name, daemon=True)
        thread.start()
        return thread

    def _start_stderr_reader(self, proc, buffer: deque[str], label: str) -> None:
        thread = self._start_thread(
            self._read_stderr_loop,
            (proc, buffer),
            f"shared-{label}-stderr-{self.room_id}",
        )
        with self._lock:
            self._stderr_threads.append(thread)

    def _read_stderr_loop(self, proc, buffer: deque[str] | None = None) -> None:
        stderr = getattr(proc, "stderr", None)
        if stderr is None:
            return
        target = buffer if buffer is not None else self._stderr_buffer
        try:
            while True:
                line = stderr.readline()
                if not line:
                    return
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="replace")
                target.append(line.rstrip("\r\n"))
        except (OSError, ValueError):
            return
        except Exception as exc:
            _log.debug("shared stderr reader error room=%s: %s", self.room_id, exc)

    def _read_upstream_stdout_loop(self, proc) -> None:
        stdout = getattr(proc, "stdout", None)
        if stdout is None:
            self.handle_upstream_error("shared ingest upstream stdout unavailable", proc)
            return
        pending = bytearray()
        try:
            while True:
                chunk = stdout.read(65536)
                if not chunk:
                    return_code = self._poll(proc)
                    if return_code is not None:
                        self.handle_upstream_error(
                            f"shared ingest upstream ffmpeg exited: code={return_code}",
                            proc,
                        )
                    return
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8", errors="ignore")
                pending.extend(chunk)
                complete_size = len(pending) // TS_PACKET_SIZE * TS_PACKET_SIZE
                if complete_size:
                    batch = bytes(pending[:complete_size])
                    del pending[:complete_size]
                    self._dispatch_ts_batch(batch)
        except (OSError, ValueError) as exc:
            with self._lock:
                current = self._process is proc
            if current:
                self.handle_upstream_error(f"shared ingest upstream read failed: {exc}", proc)

    def _dispatch_ts_batch(self, batch: bytes) -> None:
        with self._lock:
            recording = self._recording_process if self.recording_active else None
        if recording is not None:
            try:
                self._write_all(recording, batch)
            except Exception as exc:
                self._handle_recording_process_exit(
                    recording,
                    f"recording ffmpeg input failed: {exc}",
                )
        self._enqueue_preview_ts(batch)

    @staticmethod
    def _write_all(proc, data: bytes) -> None:
        stream = getattr(proc, "stdin", None)
        if stream is None:
            raise OSError("stdin unavailable")
        view = memoryview(data)
        offset = 0
        deadline = time.monotonic() + _WRITE_TIMEOUT_SEC
        while offset < len(view):
            if time.monotonic() > deadline:
                raise TimeoutError(f"stdin write timed out after {_WRITE_TIMEOUT_SEC}s")
            written = stream.write(view[offset:])
            if written is None:
                written = len(view) - offset
            if written <= 0:
                raise OSError("stdin write returned no progress")
            offset += written
        stream.flush()

    def _enqueue_preview_ts(self, batch: bytes) -> None:
        with self._preview_condition:
            if self._preview_process is None or not self._preview_subscribers:
                return
            batch_size = len(batch)
            if batch_size > self.preview_queue_bytes:
                self._record_preview_drop(batch_size)
                return
            if (
                self.preview_drop_policy == "drop_newest"
                and self._preview_queued_bytes + batch_size > self.preview_queue_bytes
            ):
                self._record_preview_drop(batch_size)
                return
            while (
                self._preview_ts_queue
                and self._preview_queued_bytes + batch_size > self.preview_queue_bytes
            ):
                dropped = self._preview_ts_queue.popleft()
                self._preview_queued_bytes -= len(dropped)
                self._record_preview_drop(len(dropped))
            if self._preview_queued_bytes + batch_size > self.preview_queue_bytes:
                self._record_preview_drop(batch_size)
                return
            self._preview_ts_queue.append(batch)
            self._preview_queued_bytes += batch_size
            self._preview_condition.notify()

    def _record_preview_drop(self, size: int) -> None:
        self.preview_dropped_bytes += size
        self.preview_dropped_batches += 1

    def _write_preview_input_loop(self, proc) -> None:
        while True:
            with self._preview_condition:
                while self._preview_process is proc and not self._preview_ts_queue:
                    self._preview_condition.wait()
                if self._preview_process is not proc:
                    return
                batch = self._preview_ts_queue.popleft()
                self._preview_queued_bytes -= len(batch)
            try:
                self._write_all(proc, batch)
            except Exception as exc:
                self._handle_preview_process_exit(proc, f"preview ffmpeg input failed: {exc}")
                return

    def _read_preview_stdout_loop(self, proc) -> None:
        stdout = getattr(proc, "stdout", None)
        if stdout is None:
            self._handle_preview_process_exit(proc, "preview ffmpeg stdout unavailable")
            return
        try:
            while True:
                chunk = stdout.read(65536)
                if not chunk:
                    return_code = self._poll(proc)
                    if return_code is not None:
                        self._handle_preview_process_exit(
                            proc,
                            f"preview ffmpeg exited: code={return_code}",
                        )
                    return
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8", errors="ignore")
                for segment in self._preview_parser.feed(chunk):
                    self.publish_preview_segment(segment.data, kind=segment.kind)
        except (OSError, ValueError) as exc:
            with self._lock:
                current = self._preview_process is proc
            if current:
                self._handle_preview_process_exit(proc, f"preview ffmpeg read failed: {exc}")

    def _watch_upstream_process_loop(self, proc) -> None:
        while True:
            with self._lock:
                if self._process is not proc:
                    return
            return_code = self._poll(proc)
            if return_code is not None:
                self.handle_upstream_error(
                    f"shared ingest upstream ffmpeg exited: code={return_code}",
                    proc,
                )
                return
            time.sleep(0.05)

    def _watch_recording_process_loop(self, proc) -> None:
        while True:
            with self._lock:
                if self._recording_process is not proc:
                    return
            return_code = self._poll(proc)
            if return_code is not None:
                self._handle_recording_process_exit(
                    proc,
                    f"recording ffmpeg exited: code={return_code}",
                )
                return
            time.sleep(0.05)

    def _watch_preview_process_loop(self, proc) -> None:
        while True:
            with self._lock:
                if self._preview_process is not proc:
                    return
            return_code = self._poll(proc)
            if return_code is not None:
                self._handle_preview_process_exit(
                    proc,
                    f"preview ffmpeg exited: code={return_code}",
                )
                return
            time.sleep(0.05)

    def _handle_recording_process_exit(self, proc, error: str) -> None:
        with self._lock:
            if self._recording_process is not proc:
                return
            tail = self._stderr_tail(self._recording_stderr_buffer)
            self.recording_error = f"{error} | stderr: {tail}" if tail else error
            self._recording_process = None
            self.recording_active = False
        self._terminate_process_object(proc)
        self._stop_upstream_if_idle(reason=self.recording_error)

    def _handle_preview_process_exit(self, proc, error: str) -> None:
        with self._preview_condition:
            if self._preview_process is not proc:
                return
            tail = self._stderr_tail(self._preview_stderr_buffer)
            self.preview_error = f"{error} | stderr: {tail}" if tail else error
            self._preview_process = None
            self._preview_ts_queue.clear()
            self._preview_queued_bytes = 0
            self._preview_condition.notify_all()
        self._terminate_process_object(proc)
        self._stop_upstream_if_idle(reason=self.preview_error)

    def _wait_for_startup_data(self, recording_path: str) -> bool:
        deadline = time.monotonic() + STARTUP_PROBE_TIMEOUT_SEC
        while True:
            if self._recording_output_has_started(recording_path):
                if self.recording_media_start_mono <= 0:
                    self.recording_media_start_mono = time.monotonic()
                return True
            with self._lock:
                proc = self._recording_process
                upstream_failed = bool(self.upstream_error)
            if upstream_failed:
                return False
            if proc is not None and self._poll(proc) is not None:
                started = self._recording_output_has_started(recording_path)
                if started and self.recording_media_start_mono <= 0:
                    self.recording_media_start_mono = time.monotonic()
                return started
            if time.monotonic() >= deadline:
                return False
            time.sleep(STARTUP_PROBE_INTERVAL_SEC)

    @staticmethod
    def _recording_output_has_started(recording_path: str) -> bool:
        try:
            return (
                bool(recording_path)
                and os.path.isfile(recording_path)
                and os.path.getsize(recording_path) > 0
            )
        except OSError:
            return False

    def stop_recording_sink(self, reason: str = "recording stopped") -> None:
        with self._lock:
            self.stop_reason = reason
        self._stop_recording_process()
        self._stop_upstream_if_idle(reason=reason)

    def _stop_recording_process(self) -> None:
        with self._lock:
            proc = self._recording_process
            self._recording_process = None
            self.recording_active = False
        if proc is not None:
            self._terminate_process_object(proc, graceful_stdin=True)
        self._join_thread(self._recording_watch_thread)

    def stop_preview_sink(self, reason: str = "preview stopped") -> None:
        with self._lock:
            self.stop_reason = reason
            self._preview_requested = False
        self._stop_preview_process()
        self._stop_upstream_if_idle(reason=reason)

    def _stop_preview_process(self) -> None:
        with self._preview_condition:
            proc = self._preview_process
            self._preview_process = None
            self._preview_ts_queue.clear()
            self._preview_queued_bytes = 0
            self._preview_condition.notify_all()
        if proc is not None:
            self._terminate_process_object(proc)
        self._join_thread(self._preview_input_thread)
        self._join_thread(self._preview_thread)
        self._join_thread(self._preview_watch_thread)

    def _stop_upstream_if_idle(self, reason: str = "") -> None:
        with self._lock:
            has_recording = self._recording_process is not None and self.recording_active
            has_preview = self._preview_process is not None
        if has_recording or has_preview:
            return
        self._stop_upstream_process()
        with self._lock:
            self.is_stopped = True
            if reason:
                self.stop_reason = reason

    def _stop_upstream_process(self) -> None:
        with self._lock:
            proc = self._process
            self._process = None
        if proc is not None:
            self._terminate_process_object(proc)
        self._join_thread(self._upstream_thread)
        self._join_thread(self._upstream_watch_thread)

    def stop(self, reason: str = "") -> None:
        if not self.is_stopped:
            _log.info("shared ingest stopping room=%s reason=%s", self.room_id, reason or "no reason")
        with self._preview_condition:
            upstream = self._process
            recording = self._recording_process
            preview = self._preview_process
            self._process = None
            self._recording_process = None
            self._preview_process = None
            self.recording_active = False
            self.is_stopped = True
            self.stop_reason = reason
            self._preview_requested = False
            self._preview_subscribers.clear()
            self._preview_ts_queue.clear()
            self._preview_queued_bytes = 0
            self._preview_condition.notify_all()
        if recording is not None:
            self._terminate_process_object(recording, graceful_stdin=True)
        if preview is not None:
            self._terminate_process_object(preview)
        if upstream is not None:
            self._terminate_process_object(upstream)
        for thread in (
            self._recording_watch_thread,
            self._preview_input_thread,
            self._preview_thread,
            self._preview_watch_thread,
            self._upstream_thread,
            self._upstream_watch_thread,
        ):
            self._join_thread(thread)

    def _terminate_process(self) -> None:
        self._stop_upstream_process()

    def _terminate_process_object(self, proc, graceful_stdin: bool = False) -> None:
        try:
            if graceful_stdin:
                self._close_pipe(getattr(proc, "stdin", None))
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
            if self._poll(proc) is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
        except Exception as exc:
            _log.warning("shared process cleanup failed room=%s: %s", self.room_id, exc)
        finally:
            for pipe_name in ("stdin", "stdout", "stderr"):
                self._close_pipe(getattr(proc, pipe_name, None))

    @staticmethod
    def _poll(proc) -> int | None:
        try:
            return proc.poll()
        except Exception:
            return None

    @staticmethod
    def _close_pipe(pipe) -> None:
        if pipe is None:
            return
        try:
            pipe.close()
        except Exception as exc:
            _log.debug("关闭管道失败: %s", exc)

    @staticmethod
    def _join_thread(thread: threading.Thread | None) -> None:
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2)


__all__ = ["PreviewSubscriber", "SharedIngestStartResult", "SharedPreviewHandle", "SharedRoomIngest"]
