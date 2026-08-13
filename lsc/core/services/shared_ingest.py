from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

from lsc import get_logger
from lsc.config import ExportProfile, load_config, preferred_hw_video_codec
from lsc.core.services.fmp4_segments import Fmp4SegmentParser
from lsc.platforms.base import headers_to_ffmpeg_input_args, network_timeout_args
from lsc.platforms.failure import FailureKind, classify_failure
from lsc.platforms.redaction import redact_text
from lsc.recorder.manifest import ManifestStore, RecordingManifest
from lsc.utils.process_launcher import prepare_launch

_log = get_logger(__name__)
STARTUP_PROBE_TIMEOUT_SEC = 15.0
STARTUP_PROBE_INTERVAL_SEC = 0.2
# MP4 会在启动时先写入很小的文件头；仅凭文件非空会把随即退出的 FFmpeg
# 误认为录制成功。要求录制进程至少稳定存活一小段时间再确认启动成功。
_RECORDING_START_STABLE_SEC = 1.0
# 上游连接超时后仍无数据则快速失败（上游 -timeout 为 10s，加 3s 缓冲）
_UPSTREAM_NO_DATA_FAST_FAIL_SEC = 13.0
TS_PACKET_SIZE = 188
_WRITE_TIMEOUT_SEC = 10.0
_RECORDING_OVERFLOW_SEC = 5.0
# 预览 stdout 超过该秒数无数据视为挂死，触发 on_error / 进程恢复
_PREVIEW_STDOUT_STALL_SEC = 15.0

_FfmpegProcess = subprocess.Popen[Any]


def _is_network_url(url: str) -> bool:
    return url.startswith(("http://", "https://"))


_H264_STARTUP_NOISE_RE = re.compile(
    r"non-existing PPS|no frame!",
    re.IGNORECASE,
)


def _is_h264_startup_noise(stderr: str) -> bool:
    """True when recording stderr is only live-H264 mid-GOP decoder noise."""
    text = str(stderr or "").strip()
    if not text:
        return False
    parts = [part.strip() for part in text.split("|") if part.strip()]
    if not parts:
        return False
    return all(_H264_STARTUP_NOISE_RE.search(part) for part in parts)


def _is_signed_network_url(url: str, network_context: Mapping[str, object] | None = None) -> bool:
    context = dict(network_context or {})
    if bool(context.get("signed_url") or context.get("disable_http_reconnect")):
        return True
    lowered = str(url or "").lower()
    return any(
        marker in lowered
        for marker in ("wssecret=", "wstime=", "ctype=huya_live")
    )


def _network_input_args(
    url: str,
    network_context: Mapping[str, object] | None = None,
) -> list[str]:
    if not _is_network_url(url):
        return []
    context = dict(network_context or {})
    args = [
        *network_timeout_args(
            context,
            default_connect_sec=10.0,
            default_read_sec=15.0,
        ),
    ]
    # Signed Huya/CDN leases die after EOF; FFmpeg HTTP reconnect retries the
    # same expired URL for several seconds and then takes recording with it.
    if not _is_signed_network_url(url, context):
        args.extend([
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
        ])
    proxy = str(
        context.get("proxy_url")
        or context.get("http_proxy")
        or context.get("https_proxy")
        or ""
    ).strip()
    if proxy:
        args.extend(["-http_proxy", proxy])
    return args


def _scaled_kbitrate(value: str, numerator: int, denominator: int = 1) -> str:
    return f"{int(value.replace('k', '')) * numerator // denominator}k"


@dataclass(frozen=True)
class SharedIngestStartResult:
    ok: bool
    use_legacy_fallback: bool = False
    error: str = ""
    accepted: bool = False
    media_ready: bool = False


def ingest_start_result(
    *,
    accepted: bool = False,
    media_ready: bool = False,
    error: str = "",
    use_legacy_fallback: bool = False,
) -> SharedIngestStartResult:
    return SharedIngestStartResult(
        ok=media_ready,
        accepted=accepted or media_ready,
        media_ready=media_ready,
        error=error,
        use_legacy_fallback=use_legacy_fallback,
    )


def preview_start_accepted(result: SharedIngestStartResult | None) -> bool:
    if result is None:
        return False
    return bool(getattr(result, "accepted", False) or getattr(result, "ok", False))


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
        network_context: Mapping[str, object] | None = None,
        preview_queue_bytes: int = 2 * 1024 * 1024,
        preview_drop_policy: str = "drop_oldest",
        recording_queue_bytes: int = 2 * 1024 * 1024,
    ):
        self.room_id = room_id
        self.url = url
        self.headers = dict(headers or {})
        self.network_context = dict(network_context or {})
        self.preview_queue_bytes = max(1, int(preview_queue_bytes))
        self.preview_drop_policy = preview_drop_policy
        self.recording_queue_bytes = max(1, int(recording_queue_bytes))
        self.is_stopped = False
        self.stop_reason = ""
        self.preview_error = ""
        self.recording_error = ""
        self.upstream_error = ""
        self.recording_active = False
        self.recording_manifest_path = ""
        self.recording_media_start_mono = 0.0
        self.last_init_segment: bytes | None = None
        self.preview_dropped_bytes = 0
        self.preview_dropped_batches = 0
        self.recording_dropped_bytes = 0
        self.recording_dropped_batches = 0
        # Monotonic media-progress counters used by runtime health and the
        # real-platform acceptance runner.  They are deliberately cumulative
        # for the room session so a sink restart cannot hide a stalled source.
        self.upstream_bytes = 0
        self.preview_segment_count = 0
        self.preview_media_bytes = 0

        self._lock = threading.RLock()
        self._preview_condition = threading.Condition(self._lock)
        self._recording_condition = threading.Condition(self._lock)
        self._preview_subscribers: list[PreviewSubscriber] = []
        self._error_callbacks: list[Callable[[str, str], None]] = []
        self._preview_ts_queue: deque[bytes] = deque()
        self._preview_queued_bytes = 0
        self._recording_ts_queue: deque[bytes] = deque()
        self._recording_queued_bytes = 0
        self._recording_generation = 0
        self._recording_overflow_since = 0.0

        self._process: _FfmpegProcess | None = None
        # Every upstream process owns a generation.  Reader/watchdog threads
        # must verify it before dispatching bytes, otherwise a late chunk from
        # an expired lease can be written after a refreshed upstream starts.
        self._upstream_generation = 0
        self._recording_process: _FfmpegProcess | None = None
        self._preview_process: _FfmpegProcess | None = None
        self._recording_path = ""
        self._recording_segmented = False
        self._recording_segments_dir = ""
        self._recording_manifest_store: ManifestStore | None = None
        self._recording_manifest: RecordingManifest | None = None
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
        self._preview_has_init = False
        self._preview_has_media_segment = False
        self._upstream_thread: threading.Thread | None = None
        self._upstream_watch_thread: threading.Thread | None = None
        self._recording_watch_thread: threading.Thread | None = None
        self._recording_input_thread: threading.Thread | None = None
        self._preview_input_thread: threading.Thread | None = None
        self._preview_thread: threading.Thread | None = None
        self._preview_watch_thread: threading.Thread | None = None
        # _stderr_threads: clean up dead threads on stop() to prevent memory leak
        self._stderr_threads: list[threading.Thread] = []
        self._stderr_buffer: deque[str] = deque(maxlen=100)
        self._recording_stderr_buffer: deque[str] = deque(maxlen=100)
        self._preview_stderr_buffer: deque[str] = deque(maxlen=100)
        self._upstream_has_produced_data = False
        # Bytes read while preflighting a replacement upstream are handed to
        # the normal generation-checked reader instead of being discarded.
        self._upstream_prefetch: dict[int, bytes] = {}

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
    def upstream_generation(self) -> int:
        with self._lock:
            return self._upstream_generation

    @property
    def recording_size_bytes(self) -> int:
        """Return the current recording output size without trusting process state."""
        with self._lock:
            path = self._recording_path
            segments_dir = self._recording_segments_dir
            segmented = self._recording_segmented
        if not path and not segments_dir:
            return 0
        try:
            targets: list[Path] = []
            if path:
                targets.append(Path(path))
            # Segmented recording writes partial/final files into this
            # directory while the public target remains a compatibility path.
            if segmented and segments_dir:
                targets.append(Path(segments_dir))
            total = 0
            seen: set[str] = set()
            for target in targets:
                identity = str(target.resolve())
                if identity in seen:
                    continue
                seen.add(identity)
                if target.is_dir():
                    total += sum(
                        item.stat().st_size
                        for item in target.rglob("*")
                        if item.is_file()
                    )
                elif target.is_file():
                    total += target.stat().st_size
            return total
        except OSError:
            return 0

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

    def add_error_callback(self, callback: Callable[[str, str], None]) -> None:
        """Register a non-blocking sink/upstream error observer."""
        with self._lock:
            if callback not in self._error_callbacks:
                self._error_callbacks.append(callback)

    def _notify_error(self, sink: str, error: str) -> None:
        with self._lock:
            callbacks = list(self._error_callbacks)
        for callback in callbacks:
            try:
                callback(sink, error)
            except Exception as exc:
                _log.debug(
                    "shared ingest error callback failed room=%s sink=%s: %s",
                    self.room_id,
                    sink,
                    exc,
                )

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
            if not preview_start_accepted(result):
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
                self._preview_has_init = True
            elif kind == "media":
                self._preview_has_media_segment = True
            self.preview_segment_count += 1
            self.preview_media_bytes += len(data)
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
        self._notify_error("preview", error)
        self._stop_upstream_if_idle(reason=error)

    def handle_upstream_error(self, error: str, proc: Any | None = None) -> None:
        with self._lock:
            if proc is not None and self._process is not proc:
                return
            tail = self._stderr_tail(self._stderr_buffer)
            self.upstream_error = f"{error} | stderr: {tail}" if tail else error
            safe_error = self.upstream_error
        self._notify_error("upstream", safe_error)
        # An upstream failure is recoverable and must not tear down healthy
        # recording/preview sinks.  Invalidate only the failed upstream;
        # IngestSupervisor is the sole owner of the subsequent replacement.
        self._stop_upstream_process()
        self._stop_upstream_if_idle(reason=safe_error)

    def _get_stderr_tail(self, lines: int = 20) -> str:
        return self._stderr_tail(self._stderr_buffer, lines)

    @staticmethod
    def _stderr_tail(buffer: deque[str], lines: int = 20) -> str:
        return redact_text(" | ".join(list(buffer)[-lines:])) if buffer else ""

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
        command += _network_input_args(self.url, self.network_context)
        if self.headers:
            command += headers_to_ffmpeg_input_args(self.headers)
        command += [
            "-protocol_whitelist",
            "file,http,https,tcp,tls,crypto",
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

    @staticmethod
    def _mpegts_pipe_input_args() -> list[str]:
        # Live pipe muxers must start reading immediately. Default probesize
        # (5MB) plus +faststart will buffer the entire recording in memory
        # and never create an output file during the 15s startup probe.
        return [
            "-fflags", "+genpts+discardcorrupt",
            "-probesize", "32768",
            "-analyzeduration", "500000",
            "-thread_queue_size", "1024",
            "-f", "mpegts",
            "-i", "pipe:0",
        ]

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
            *self._mpegts_pipe_input_args(),
            "-map", "0:v",
            "-map", "0:a?",
            *effective_profile.ffmpeg_video_args(),
            *effective_profile.ffmpeg_audio_args(),
            *filter_args,
            "-f", "mp4",
            "-movflags", "frag_keyframe+empty_moov+default_base_moof",
            recording_path,
        ]

    def build_segmented_recording_command(
        self,
        segments_dir: str,
        profile: ExportProfile | None = None,
        *,
        segment_seconds: int = 60,
    ) -> list[str]:
        """Build a segment-muxer command consuming the shared TS pipe."""
        profile = profile or ExportProfile(codec="copy")
        filter_args = profile.ffmpeg_filter_args()
        if profile.is_copy and filter_args:
            effective_profile = replace(profile, codec=preferred_hw_video_codec())
        else:
            effective_profile = profile
        Path(segments_dir).mkdir(parents=True, exist_ok=True)
        pattern = str(Path(segments_dir) / "%06d.partial.mkv")
        return [
            self._ffmpeg_path(),
            "-y",
            "-loglevel", "warning",
            *self._mpegts_pipe_input_args(),
            "-map", "0:v",
            "-map", "0:a?",
            *effective_profile.ffmpeg_video_args(),
            *effective_profile.ffmpeg_audio_args(),
            *filter_args,
            "-f", "segment",
            "-segment_time", str(max(5, int(segment_seconds))),
            "-reset_timestamps", "1",
            "-segment_format", "matroska",
            pattern,
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

    def build_preview_only_command(self, preview_pipe: str = "pipe:1", **kwargs: Any) -> list[str]:
        return self.build_preview_command(preview_pipe=preview_pipe, **kwargs)

    def start_recording_and_preview(
        self,
        recording_path: str,
        preview_pipe: str = "pipe:1",
        profile: ExportProfile | None = None,
        segmented: bool = False,
        segment_seconds: int = 60,
        platform_id: str = "",
        canonical_room_id: str = "",
        manifest_path: str = "",
    ) -> SharedIngestStartResult:
        del preview_pipe
        if (
            not any((segmented, manifest_path, platform_id, canonical_room_id))
            and segment_seconds == 60
        ):
            return self.start_recording(recording_path, profile=profile)
        return self.start_recording(
            recording_path,
            profile=profile,
            segmented=segmented,
            segment_seconds=segment_seconds,
            platform_id=platform_id,
            canonical_room_id=canonical_room_id,
            manifest_path=manifest_path,
        )

    def start_recording(
        self,
        recording_path: str,
        profile: ExportProfile | None = None,
        *,
        segmented: bool = False,
        segment_seconds: int = 60,
        platform_id: str = "",
        canonical_room_id: str = "",
        manifest_path: str = "",
    ) -> SharedIngestStartResult:
        with self._lock:
            if self._recording_process is not None and self.recording_active:
                return ingest_start_result(accepted=True, media_ready=True)
            self.recording_error = ""
            self.recording_media_start_mono = 0.0
        startup_probe_path = recording_path
        manifest_store: ManifestStore | None = None
        manifest: RecordingManifest | None = None
        segments_dir = ""
        if segmented:
            target = Path(recording_path).resolve()
            session_dir = target.parent / (
                f"{target.stem}_session_{uuid.uuid4().hex[:10]}"
            )
            segments_path = session_dir / "segments"
            segments_path.mkdir(parents=True, exist_ok=False)
            manifest_store = ManifestStore(
                manifest_path or str(session_dir / "manifest.json")
            )
            manifest = RecordingManifest.create(
                room_session_id=self.room_id,
                platform_id=platform_id or "unknown",
                canonical_room_id=canonical_room_id,
            )
            manifest.add_segment("segments/000001.partial.mkv")
            manifest_store.save(manifest)
            segments_dir = str(segments_path)
            startup_probe_path = segments_dir
            command = self.build_segmented_recording_command(
                segments_dir,
                profile,
                segment_seconds=segment_seconds,
            )
        else:
            command = self.build_recording_command(recording_path, profile)
        try:
            proc = self._launch_process(command)
        except Exception as exc:
            if manifest is not None and manifest_store is not None:
                manifest.close(state="FAILED", unclean=True)
                manifest_store.save(manifest)
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
            self._recording_segmented = bool(segmented)
            self._recording_segments_dir = segments_dir
            self._recording_manifest_store = manifest_store
            self._recording_manifest = manifest
            self.recording_manifest_path = (
                str(manifest_store.path) if manifest_store is not None else ""
            )
            self._last_recording_command = list(command)
            self.recording_active = True
            self.is_stopped = False
            self.stop_reason = ""
            self._recording_generation += 1
            recording_generation = self._recording_generation
            self._recording_ts_queue.clear()
            self._recording_queued_bytes = 0
            self._recording_overflow_since = 0.0
        self._start_stderr_reader(proc, self._recording_stderr_buffer, "recording")
        self._recording_input_thread = self._start_thread(
            self._write_recording_input_loop,
            (proc, recording_generation),
            f"shared-recording-input-{self.room_id}",
        )
        self._recording_watch_thread = self._start_thread(
            self._watch_recording_process_loop,
            (proc,),
            f"shared-recording-watch-{self.room_id}",
        )

        upstream_error = self._ensure_upstream_started()
        if upstream_error:
            self._stop_recording_process()
            return self._recording_start_failed(upstream_error)
        if not self._wait_for_startup_data(startup_probe_path):
            tail = self._stderr_tail(self._recording_stderr_buffer)
            upstream_tail = self._stderr_tail(self._stderr_buffer)
            existing = (self.recording_error or "").strip()
            # Prefer the upstream diagnostic. Live H264 often logs PPS/no-frame
            # while waiting for the next IDR; that is not the failure cause.
            upstream_kind = classify_failure(
                self.upstream_error or upstream_tail
            )
            if existing and "startup probe failed" not in existing.lower():
                error = existing
            elif (
                (self.upstream_error or upstream_tail)
                and upstream_kind is not FailureKind.UNKNOWN
            ):
                error = (
                    "录制启动失败：直播流连接异常 | upstream: "
                    f"{upstream_tail or self.upstream_error}"
                )
            elif not self._upstream_has_produced_data:
                error = "录制启动失败：直播流无数据返回"
                if upstream_tail:
                    error = f"{error} | upstream: {upstream_tail}"
            elif _is_h264_startup_noise(tail):
                error = "录制启动失败：直播流尚未出现可解码关键帧"
            else:
                error = "录制启动失败：录制文件未开始写入"
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
        return ingest_start_result(accepted=True, media_ready=True)

    def _recording_start_failed(self, error: str) -> SharedIngestStartResult:
        with self._lock:
            self.recording_error = error
            self._last_error = error
            self.recording_active = False
        self._notify_error("recording", error)
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
                return ingest_start_result(accepted=True)
            if self._preview_process is not None and self._poll(self._preview_process) is None:
                return ingest_start_result(
                    accepted=True,
                    media_ready=bool(self._preview_has_init and self._preview_has_media_segment),
                )
            self.preview_error = ""
        command = self.build_preview_command(**cast(Any, options))
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
            self._preview_has_init = False
            self._preview_has_media_segment = False
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
        return ingest_start_result(accepted=True)

    def _preview_start_failed(self, error: str) -> SharedIngestStartResult:
        with self._lock:
            self.preview_error = error
            self._last_error = error
        self._notify_error("preview", error)
        self._stop_upstream_if_idle(reason=error)
        return SharedIngestStartResult(ok=False, use_legacy_fallback=False, error=error)

    def start_preview_only(self, preview_pipe: str = "pipe:1") -> SharedIngestStartResult:
        return self.start_preview(preview_pipe=preview_pipe)

    def _start_preview_only_ffmpeg(self, preview_pipe: str = "pipe:1") -> bool:
        return self.start_preview(preview_pipe=preview_pipe).accepted

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
            self._upstream_generation += 1
            generation = self._upstream_generation
            self._last_command = list(command)
            self.upstream_error = ""
            self.is_stopped = False
            self.stop_reason = ""
        self._upstream_has_produced_data = False
        self._start_stderr_reader(proc, self._stderr_buffer, "upstream")
        self._upstream_thread = self._start_thread(
            self._read_upstream_stdout_loop,
            (proc, generation),
            f"shared-upstream-output-{self.room_id}",
        )
        self._upstream_watch_thread = self._start_thread(
            self._watch_upstream_process_loop,
            (proc, generation),
            f"shared-upstream-watch-{self.room_id}",
        )
        return ""

    def replace_upstream(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        network_context: Mapping[str, object] | None = None,
        preflight: bool = False,
    ) -> str:
        """Switch the remote source while retaining both downstream sinks.

        The old reader is invalidated before its process is terminated.  A
        new upstream is started only when at least one sink is still active;
        existing recording/preview FFmpeg processes continue consuming the
        new TS stream through their existing pipes.
        """
        with self._lock:
            old_process = self._process
            has_sink = bool(
                self.recording_active
                or self._preview_process is not None
                or self._preview_subscribers
            )
        if not has_sink:
            with self._lock:
                self.url = str(url or "")
                self.headers = dict(headers or {})
                self.network_context = dict(network_context or {})
            self._stop_upstream_process()
            return ""

        if preflight and old_process is not None:
            candidate, prefetched, error = self._preflight_upstream(
                url,
                headers=headers,
                network_context=network_context,
            )
            if error:
                return error
            assert candidate is not None
            with self._lock:
                self.url = str(url or "")
                self.headers = dict(headers or {})
                self.network_context = dict(network_context or {})
                self._process = candidate
                self._upstream_generation += 1
                generation = self._upstream_generation
                self._last_command = self.build_upstream_command()
                self.upstream_error = ""
                self.is_stopped = False
                self.stop_reason = ""
                self._upstream_has_produced_data = False
                if prefetched:
                    self._upstream_prefetch[id(candidate)] = prefetched
            self._start_stderr_reader(candidate, self._stderr_buffer, "upstream")
            self._upstream_thread = self._start_thread(
                self._read_upstream_stdout_loop,
                (candidate, generation),
                f"shared-upstream-output-{self.room_id}",
            )
            self._upstream_watch_thread = self._start_thread(
                self._watch_upstream_process_loop,
                (candidate, generation),
                f"shared-upstream-watch-{self.room_id}",
            )
            self._terminate_process_object(old_process)
            return ""

        with self._lock:
            self.url = str(url or "")
            self.headers = dict(headers or {})
            self.network_context = dict(network_context or {})
        self._stop_upstream_process()
        return self._ensure_upstream_started()

    def _preflight_upstream(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        network_context: Mapping[str, object] | None = None,
    ) -> tuple[_FfmpegProcess | None, bytes, str]:
        """Start a candidate upstream and require a valid MPEG-TS packet."""
        with self._lock:
            old_url, old_headers, old_context = self.url, self.headers, self.network_context
            self.url = str(url or "")
            self.headers = dict(headers or {})
            self.network_context = dict(network_context or {})
            try:
                command = self.build_upstream_command()
            finally:
                self.url, self.headers, self.network_context = old_url, old_headers, old_context
        try:
            candidate = self._launch_process(command)
        except Exception as exc:
            return None, b"", f"upstream preflight start failed: {redact_text(exc)}"
        return_code = self._poll(candidate)
        if return_code is not None:
            self._terminate_process_object(candidate)
            return None, b"", f"upstream preflight exited immediately: code={return_code}"

        self._start_stderr_reader(candidate, self._stderr_buffer, "preflight")
        stdout = getattr(candidate, "stdout", None)
        if stdout is None:
            self._terminate_process_object(candidate)
            return None, b"", "upstream preflight stdout unavailable"

        result: dict[str, bytes] = {}
        done = threading.Event()

        def _read_first_chunk() -> None:
            buffered = bytearray()
            deadline = time.monotonic() + 8.0
            try:
                while time.monotonic() < deadline:
                    chunk = stdout.read(65536)
                    if chunk:
                        if isinstance(chunk, str):
                            chunk = chunk.encode("utf-8", errors="ignore")
                        buffered.extend(chunk)
                        if self._contains_ts_packet(buffered):
                            result["data"] = bytes(buffered)
                            return
                    elif self._poll(candidate) is not None:
                        return
                    time.sleep(0.02)
            except (OSError, ValueError):
                return
            finally:
                done.set()

        reader = threading.Thread(
            target=_read_first_chunk,
            name=f"shared-upstream-preflight-{self.room_id}",
            daemon=True,
        )
        reader.start()
        done.wait(timeout=8.5)
        if "data" not in result:
            self._terminate_process_object(candidate)
            reader.join(timeout=1)
            return None, b"", "upstream preflight did not produce a valid media packet"
        reader.join(timeout=1)
        return candidate, result["data"], ""

    @staticmethod
    def _contains_ts_packet(data: bytes | bytearray) -> bool:
        raw = bytes(data)
        return any(raw[offset] == 0x47 for offset in range(max(0, len(raw) - TS_PACKET_SIZE + 1)))

    def _launch_process(self, command: list[str]) -> _FfmpegProcess:
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
    def _start_thread(
        target: Callable[..., Any],
        args: tuple[Any, ...],
        name: str,
    ) -> threading.Thread:
        thread = threading.Thread(target=target, args=args, name=name, daemon=True)
        thread.start()
        return thread

    def _start_stderr_reader(self, proc: _FfmpegProcess, buffer: deque[str], label: str) -> None:
        thread = self._start_thread(
            self._read_stderr_loop,
            (proc, buffer),
            f"shared-{label}-stderr-{self.room_id}",
        )
        with self._lock:
            # 先清理已结束的 stderr 线程，防止长时间运行时列表无限增长
            self._stderr_threads = [t for t in self._stderr_threads if t.is_alive()]
            self._stderr_threads.append(thread)

    def _read_stderr_loop(self, proc: _FfmpegProcess, buffer: deque[str] | None = None) -> None:
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
            # 非预期异常升级为 warning：stderr 读取线程退出后管道可能写满阻塞 FFmpeg
            _log.warning("shared stderr reader error room=%s: %s", self.room_id, redact_text(exc))

    def _read_upstream_stdout_loop(
        self,
        proc: _FfmpegProcess,
        generation: int | None = None,
    ) -> None:
        stdout = getattr(proc, "stdout", None)
        if stdout is None:
            self.handle_upstream_error("shared ingest upstream stdout unavailable", proc)
            return
        with self._lock:
            prefetched = self._upstream_prefetch.pop(id(proc), b"")
        pending = bytearray(prefetched)
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
                with self._lock:
                    if self._process is not proc or (
                        generation is not None and self._upstream_generation != generation
                    ):
                        return
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8", errors="ignore")
                with self._lock:
                    if self._process is not proc or (
                        generation is not None and self._upstream_generation != generation
                    ):
                        return
                    self.upstream_bytes += len(chunk)
                pending.extend(chunk)
                complete_size = len(pending) // TS_PACKET_SIZE * TS_PACKET_SIZE
                if complete_size:
                    batch = bytes(pending[:complete_size])
                    del pending[:complete_size]
                    self._dispatch_ts_batch(batch, proc=proc, generation=generation)
        except (OSError, ValueError) as exc:
            with self._lock:
                current = self._process is proc
            if current:
                self.handle_upstream_error(f"shared ingest upstream read failed: {exc}", proc)

    def _dispatch_ts_batch(
        self,
        batch: bytes,
        *,
        proc: _FfmpegProcess | None = None,
        generation: int | None = None,
    ) -> None:
        with self._lock:
            if proc is not None and self._process is not proc:
                return
            if generation is not None and self._upstream_generation != generation:
                return
            if not self._upstream_has_produced_data:
                self._upstream_has_produced_data = True
        with self._lock:
            recording = self._recording_process if self.recording_active else None
        if recording is not None:
            self._enqueue_recording_ts(batch)
        self._enqueue_preview_ts(batch)

    @staticmethod
    def _write_all(proc: _FfmpegProcess, data: bytes) -> None:
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

    def _enqueue_recording_ts(self, batch: bytes) -> None:
        overflow_proc = None
        with self._recording_condition:
            if self._recording_process is None or not self.recording_active:
                return
            batch_size = len(batch)
            dropped = False
            if batch_size > self.recording_queue_bytes:
                self._record_recording_drop(batch_size)
                dropped = True
            else:
                while (
                    self._recording_ts_queue
                    and self._recording_queued_bytes + batch_size > self.recording_queue_bytes
                ):
                    old = self._recording_ts_queue.popleft()
                    self._recording_queued_bytes -= len(old)
                    self._record_recording_drop(len(old))
                    dropped = True
                if self._recording_queued_bytes + batch_size > self.recording_queue_bytes:
                    self._record_recording_drop(batch_size)
                    dropped = True
                else:
                    self._recording_ts_queue.append(batch)
                    self._recording_queued_bytes += batch_size
                    self._recording_overflow_since = 0.0
                    self._recording_condition.notify()
                    return
            now = time.monotonic()
            if self._recording_overflow_since <= 0:
                self._recording_overflow_since = now
            elif now - self._recording_overflow_since >= _RECORDING_OVERFLOW_SEC:
                overflow_proc = self._recording_process
        if overflow_proc is not None:
            self._handle_recording_process_exit(
                overflow_proc,
                "RECORDING_SINK_FAILURE: recording stdin queue overflow",
            )

    def _record_recording_drop(self, size: int) -> None:
        self.recording_dropped_bytes += size
        self.recording_dropped_batches += 1

    def _write_recording_input_loop(self, proc: _FfmpegProcess, generation: int) -> None:
        while True:
            with self._recording_condition:
                while (
                    self._recording_process is proc
                    and self._recording_generation == generation
                    and not self._recording_ts_queue
                ):
                    self._recording_condition.wait()
                if (
                    self._recording_process is not proc
                    or self._recording_generation != generation
                ):
                    return
                batch = self._recording_ts_queue.popleft()
                self._recording_queued_bytes -= len(batch)
            try:
                self._write_all(proc, batch)
            except Exception as extra:
                self._handle_recording_process_exit(
                    proc,
                    f"recording ffmpeg input failed: {extra}",
                )
                return

    def _write_preview_input_loop(self, proc: _FfmpegProcess) -> None:
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

    def _read_preview_stdout_loop(self, proc: _FfmpegProcess) -> None:
        stdout = getattr(proc, "stdout", None)
        if stdout is None:
            self._handle_preview_process_exit(proc, "preview ffmpeg stdout unavailable")
            return
        last_data_time = [time.monotonic()]
        stop_watchdog = threading.Event()

        def _stdout_stall_watchdog() -> None:
            while not stop_watchdog.wait(1.0):
                with self._lock:
                    if self._preview_process is not proc:
                        return
                if self._poll(proc) is not None:
                    return
                if time.monotonic() - last_data_time[0] > _PREVIEW_STDOUT_STALL_SEC:
                    _log.error(
                        "shared preview stdout stalled (%ds) room=%s",
                        _PREVIEW_STDOUT_STALL_SEC,
                        self.room_id,
                    )
                    # kill 失败时尝试 terminate + 关闭 stdin 作为备用方案
                    try:
                        proc.kill()
                    except Exception as exc:
                        _log.warning(
                            "shared preview stall kill failed room=%s: %s, trying terminate",
                            self.room_id, exc,
                        )
                        try:
                            proc.terminate()
                        except Exception:
                            pass
                    try:
                        if proc.stdin:
                            proc.stdin.close()
                    except Exception:
                        pass
                    try:
                        if proc.stdout:
                            proc.stdout.close()
                    except Exception as exc:
                        _log.debug(
                            "shared preview stall stdout close failed: %s", exc,
                        )
                    return

        watchdog = threading.Thread(target=_stdout_stall_watchdog, daemon=True)
        watchdog.start()
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
                last_data_time[0] = time.monotonic()
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8", errors="ignore")
                for segment in self._preview_parser.feed(chunk):
                    self.publish_preview_segment(segment.data, kind=segment.kind)
        except (OSError, ValueError) as exc:
            with self._lock:
                current = self._preview_process is proc
            if current:
                self._handle_preview_process_exit(proc, f"preview ffmpeg read failed: {exc}")
        finally:
            stop_watchdog.set()
            if watchdog.is_alive() and watchdog is not threading.current_thread():
                watchdog.join(timeout=2)

    def _watch_upstream_process_loop(
        self,
        proc: _FfmpegProcess,
        generation: int | None = None,
    ) -> None:
        while True:
            with self._lock:
                if self._process is not proc or (
                    generation is not None and self._upstream_generation != generation
                ):
                    return
            return_code = self._poll(proc)
            if return_code is not None:
                self.handle_upstream_error(
                    f"shared ingest upstream ffmpeg exited: code={return_code}",
                    proc,
                )
                return
            time.sleep(0.25)

    def _watch_recording_process_loop(self, proc: _FfmpegProcess) -> None:
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
            time.sleep(0.25)

    def _watch_preview_process_loop(self, proc: _FfmpegProcess) -> None:
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
            time.sleep(0.25)

    def _handle_recording_process_exit(self, proc: _FfmpegProcess, error: str) -> None:
        with self._recording_condition:
            if self._recording_process is not proc:
                return
            tail = self._stderr_tail(self._recording_stderr_buffer)
            self.recording_error = f"{error} | stderr: {tail}" if tail else error
            self._recording_process = None
            self.recording_active = False
            self._recording_generation += 1
            self._recording_ts_queue.clear()
            self._recording_queued_bytes = 0
            self._recording_condition.notify_all()
            segmented = self._recording_segmented
            manifest_store = self._recording_manifest_store
            segments_dir = self._recording_segments_dir
        self._terminate_process_object(proc)
        self._notify_error("recording", self.recording_error)
        if segmented and manifest_store is not None:
            self._finalize_segmented_manifest(
                manifest_store,
                segments_dir,
                unclean=True,
            )
        self._stop_upstream_if_idle(reason=self.recording_error)

    def _handle_preview_process_exit(self, proc: _FfmpegProcess, error: str) -> None:
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
        self._notify_error("preview", self.preview_error)
        self._stop_upstream_if_idle(reason=self.preview_error)

    def _wait_for_startup_data(self, recording_path: str) -> bool:
        deadline = time.monotonic() + STARTUP_PROBE_TIMEOUT_SEC
        fast_fail_at = time.monotonic() + _UPSTREAM_NO_DATA_FAST_FAIL_SEC
        output_seen_at: float | None = None
        while True:
            with self._lock:
                proc = self._recording_process
                upstream = self._process
            # Watcher 线程已清空进程引用，说明录制端已经退出；即便 MP4 文件头
            # 已落盘也不能视作有效录制，交由调用方回退到常规录制器。
            if proc is None or self._poll(proc) is not None:
                return False
            if self._recording_output_has_started(recording_path):
                now = time.monotonic()
                if output_seen_at is None:
                    output_seen_at = now
                elif now - output_seen_at >= _RECORDING_START_STABLE_SEC:
                    if self.recording_media_start_mono <= 0:
                        self.recording_media_start_mono = now
                    return True
            # Upstream EOF is recoverable while the recording sink is still
            # alive: keep the pipe open and replace the remote source so the
            # next IDR can land.  Aborting here surfaces benign H264 PPS
            # warnings as a recording start failure.
            if upstream is None:
                self._ensure_upstream_started()
            # 快速失败：上游超时仍无数据，检查上游 stderr 诊断原因
            if not self._upstream_has_produced_data and time.monotonic() >= fast_fail_at:
                upstream_tail = self._stderr_tail(self._stderr_buffer)
                _log.warning(
                    "shared ingest upstream no data after %.0fs room=%s upstream_stderr: %s",
                    _UPSTREAM_NO_DATA_FAST_FAIL_SEC,
                    self.room_id,
                    upstream_tail or "(empty)",
                )
                return False
            if time.monotonic() >= deadline:
                return False
            time.sleep(STARTUP_PROBE_INTERVAL_SEC)

    @staticmethod
    def _recording_output_has_started(recording_path: str) -> bool:
        try:
            if os.path.isdir(recording_path):
                return any(
                    path.is_file() and path.stat().st_size > 0
                    for path in Path(recording_path).glob("*.mkv")
                )
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
        with self._recording_condition:
            proc = self._recording_process
            self._recording_process = None
            self.recording_active = False
            self._recording_generation += 1
            self._recording_ts_queue.clear()
            self._recording_queued_bytes = 0
            self._recording_condition.notify_all()
            segmented = self._recording_segmented
            manifest_store = self._recording_manifest_store
            segments_dir = self._recording_segments_dir
        if proc is not None:
            return_code = self._poll(proc)
            self._terminate_process_object(proc, graceful_stdin=True)
            if segmented and manifest_store is not None:
                self._finalize_segmented_manifest(
                    manifest_store,
                    segments_dir,
                    unclean=return_code not in (None, 0),
                )
        self._join_thread(self._recording_input_thread)
        self._join_thread(self._recording_watch_thread)

    @staticmethod
    def _finalize_segmented_manifest(
        store: ManifestStore,
        segments_dir: str,
        *,
        unclean: bool,
    ) -> None:
        """Recover/close the manifest without deleting partial evidence."""
        del segments_dir
        try:
            manifest = store.recover()
            if not unclean:
                for entry in manifest.segments:
                    if entry.state == "RECOVERED":
                        entry.state = "COMPLETE"
            manifest.close(
                state="PARTIAL" if unclean else "COMPLETE",
                unclean=unclean,
            )
            store.save(manifest)
        except (OSError, ValueError, TypeError) as exc:
            _log.warning("segmented manifest finalize failed path=%s: %s", store.path, exc)

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
            self._preview_has_init = False
            self._preview_has_media_segment = False
            self.last_init_segment = None
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
            self._upstream_generation += 1
            if proc is not None:
                self._upstream_prefetch.pop(id(proc), None)
        if proc is not None:
            self._terminate_process_object(proc)
        self._join_thread(self._upstream_thread)
        self._join_thread(self._upstream_watch_thread)

    def stop(
        self,
        reason: str = "",
        *,
        deadline_monotonic: float | None = None,
    ) -> None:
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
            if upstream is not None:
                self._upstream_prefetch.pop(id(upstream), None)
        if recording is not None:
            self._terminate_process_object(
                recording,
                graceful_stdin=True,
                deadline_monotonic=deadline_monotonic,
            )
        if preview is not None:
            self._terminate_process_object(
                preview,
                deadline_monotonic=deadline_monotonic,
            )
        if upstream is not None:
            self._terminate_process_object(
                upstream,
                deadline_monotonic=deadline_monotonic,
            )
        for thread in (
            self._recording_watch_thread,
            self._preview_input_thread,
            self._preview_thread,
            self._preview_watch_thread,
            self._upstream_thread,
            self._upstream_watch_thread,
        ):
            self._join_thread(thread, deadline_monotonic=deadline_monotonic)
        # 回收 stderr 读取线程：进程已终止、管道已关闭，readline 会返回 EOF
        with self._lock:
            stderr_threads = list(self._stderr_threads)
            self._stderr_threads.clear()
        for thread in stderr_threads:
            self._join_thread(thread, deadline_monotonic=deadline_monotonic)

    def _terminate_process(self) -> None:
        self._stop_upstream_process()

    def _terminate_process_object(
        self,
        proc: _FfmpegProcess,
        graceful_stdin: bool = False,
        *,
        deadline_monotonic: float | None = None,
    ) -> None:
        def remaining(fallback: float) -> float:
            if deadline_monotonic is None:
                return fallback
            return max(0.0, min(fallback, deadline_monotonic - time.monotonic()))

        try:
            if graceful_stdin:
                self._close_pipe(getattr(proc, "stdin", None))
                try:
                    proc.wait(timeout=remaining(3.0))
                except subprocess.TimeoutExpired:
                    pass
            if self._poll(proc) is None:
                proc.terminate()
                try:
                    proc.wait(timeout=remaining(3.0))
                except subprocess.TimeoutExpired:
                    proc.kill()
                    # Once the hard deadline is reached, keep a small fixed
                    # reap window so the killed child is not left as a zombie.
                    try:
                        proc.wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        pass
        except Exception as exc:
            _log.warning("shared process cleanup failed room=%s: %s", self.room_id, exc)
        finally:
            for pipe_name in ("stdin", "stdout", "stderr"):
                self._close_pipe(getattr(proc, pipe_name, None))

    @staticmethod
    def _poll(proc: _FfmpegProcess) -> int | None:
        try:
            return proc.poll()
        except Exception:
            return None

    @staticmethod
    def _close_pipe(pipe: Any) -> None:
        if pipe is None:
            return
        try:
            pipe.close()
        except Exception as exc:
            _log.debug("关闭管道失败: %s", exc)

    @staticmethod
    def _join_thread(
        thread: threading.Thread | None,
        *,
        deadline_monotonic: float | None = None,
    ) -> None:
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            timeout = 2.0
            if deadline_monotonic is not None:
                timeout = max(0.0, min(timeout, deadline_monotonic - time.monotonic()))
            thread.join(timeout=timeout)


__all__ = [
    "PreviewSubscriber",
    "SharedIngestStartResult",
    "SharedPreviewHandle",
    "SharedRoomIngest",
    "ingest_start_result",
    "preview_start_accepted",
]
