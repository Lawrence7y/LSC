"""Real media probing and candidate scoring."""
from __future__ import annotations

import concurrent.futures
import json
import logging
import re
import subprocess
import time
from collections.abc import Iterable, Mapping
from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import urlparse

from .base import headers_to_ffmpeg_input_args, network_timeout_args
from .failure import classify_failure
from .models import (
    PlatformCapabilities,
    ProbeRequest,
    ProbeResult,
    StreamCandidate,
)
from .redaction import redact_text
from .url_policy import validate_redirect_chain

_log = logging.getLogger(__name__)
_HTTP_STATUS_RE = re.compile(
    r"(?:Server returned|HTTP error|HTTP/\d(?:\.\d)?)\s+([4-5]\d\d)",
    re.IGNORECASE,
)
_RETRY_AFTER_RE = re.compile(
    r"retry[- ]after\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)",
    re.IGNORECASE,
)


class _ProbeCancelled(Exception):
    def __init__(self, *, stdout: str = "", stderr: str = "") -> None:
        super().__init__("probe cancelled")
        self.stdout = stdout
        self.stderr = stderr


class ProbeService:
    """Probe candidates using the same headers and URL as the media process."""

    def __init__(self, ffprobe_path: str = "ffprobe") -> None:
        self.ffprobe_path = ffprobe_path or "ffprobe"

    @staticmethod
    def build_command(request: ProbeRequest) -> list[str]:
        try:
            timeout_default = float(request.timeout_sec)
        except (TypeError, ValueError):
            timeout_default = 8.0
        command = [
            request.ffprobe_path or "ffprobe",
            "-v",
            "warning",
            *network_timeout_args(
                request.network_context,
                default_connect_sec=timeout_default,
                default_read_sec=timeout_default,
            ),
            "-show_streams",
            "-show_format",
            # Read a bounded packet window as well as metadata.  Live MPEG-TS
            # often has no container duration, so packet timestamp movement is
            # the only reliable proof that media is advancing.
            "-show_packets",
            "-read_intervals",
            "%+1",
            # Keep ffprobe inside the media transport set.  HLS playlists
            # legitimately recurse through file/http(s)/TCP/TLS/crypto;
            # arbitrary protocols (including concat/subprocess) are not
            # accepted from a platform candidate.
            "-protocol_whitelist",
            "file,http,https,tcp,tls,crypto",
            "-of",
            "json",
        ]
        command.extend(headers_to_ffmpeg_input_args(
            dict(request.candidate.request_headers)
        ))
        context = dict(request.network_context or {})
        proxy = str(
            context.get("proxy_url")
            or context.get("http_proxy")
            or context.get("https_proxy")
            or ""
        ).strip()
        if proxy:
            command.extend(["-http_proxy", proxy])
        command.extend(["-i", request.candidate.url])
        return command

    def probe(self, request: ProbeRequest) -> ProbeResult:
        started = time.monotonic()
        candidate = request.candidate
        cancellation = request.cancellation
        cancelled = (
            self._cancelled(cancellation) if cancellation is not None else False
        )
        if cancelled:
            return ProbeResult(
                candidate_id=candidate.candidate_id,
                failure_kind="CANCELLED",
                failure_detail="probe cancelled",
            )
        if request.ffprobe_path == "ffprobe" and self.ffprobe_path != "ffprobe":
            request = replace(request, ffprobe_path=self.ffprobe_path)
        if (
            request.deadline_monotonic is not None
            and started >= request.deadline_monotonic
        ):
            return ProbeResult(
                candidate_id=candidate.candidate_id,
                failure_kind="CONNECT_TIMEOUT",
                failure_detail="probe deadline exceeded",
            )
        timeout = max(0.1, float(request.timeout_sec))
        if request.deadline_monotonic is not None:
            remaining = request.deadline_monotonic - started
            if remaining <= 0:
                return ProbeResult(
                    candidate_id=candidate.candidate_id,
                    failure_kind="CONNECT_TIMEOUT",
                    failure_detail="probe deadline exceeded",
                )
            timeout = min(
                timeout,
                max(0.01, remaining),
                )
        if bool(candidate.raw_metadata.get("validate_redirects")):
            context = dict(request.network_context or {})
            proxy = str(
                context.get("proxy_url")
                or context.get("http_proxy")
                or context.get("https_proxy")
                or ""
            ).strip()
            redirect_safe, redirect_reason = validate_redirect_chain(
                candidate.url,
                headers=dict(candidate.request_headers),
                timeout_sec=timeout,
                proxy_url=proxy,
            )
            if not redirect_safe:
                return ProbeResult(
                    candidate_id=candidate.candidate_id,
                    failure_kind="RESTRICTED",
                    failure_detail=redact_text(redirect_reason),
                )
        command = self.build_command(request)
        try:
            if cancellation is None:
                # Keep the simple path for callers that do not need cooperative
                # cancellation (and for compatibility with existing adapters).
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
            else:
                completed = self._run_cancellable(
                    command,
                    timeout=timeout,
                    cancellation=cancellation,
                )
        except subprocess.TimeoutExpired as exc:
            return ProbeResult(
                candidate_id=candidate.candidate_id,
                failure_kind="CONNECT_TIMEOUT",
                failure_detail=redact_text(exc),
            )
        except _ProbeCancelled:
            return ProbeResult(
                candidate_id=candidate.candidate_id,
                failure_kind="CANCELLED",
                failure_detail="probe cancelled",
            )
        except FileNotFoundError as exc:
            return ProbeResult(
                candidate_id=candidate.candidate_id,
                failure_kind="UNKNOWN",
                failure_detail=redact_text(exc),
            )
        except OSError as exc:
            return ProbeResult(
                candidate_id=candidate.candidate_id,
                failure_kind=classify_failure(str(exc)).value,
                failure_detail=redact_text(exc),
            )

        elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
        cancelled = (
            self._cancelled(cancellation) if cancellation is not None else False
        )
        if cancelled:
            return ProbeResult(
                candidate_id=candidate.candidate_id,
                first_packet_ms=elapsed_ms,
                failure_kind="CANCELLED",
                failure_detail="probe cancelled",
            )
        stderr = str(completed.stderr or "")
        status_match = _HTTP_STATUS_RE.search(stderr)
        http_status = int(status_match.group(1)) if status_match else None
        retry_match = _RETRY_AFTER_RE.search(stderr)
        retry_after = float(retry_match.group(1)) if retry_match else None
        try:
            payload = json.loads(completed.stdout or "{}")
        except (TypeError, ValueError):
            payload = {}
        streams = payload.get("streams", []) if isinstance(payload, dict) else []
        if not isinstance(streams, list):
            streams = []
        fmt = payload.get("format", {}) if isinstance(payload, dict) else {}
        if not isinstance(fmt, dict):
            fmt = {}
        packets = payload.get("packets", []) if isinstance(payload, dict) else []
        if not isinstance(packets, list):
            packets = []
        video = next(
            (item for item in streams if isinstance(item, dict)
             and item.get("codec_type") == "video"),
            {},
        )
        audio = next(
            (item for item in streams if isinstance(item, dict)
             and item.get("codec_type") == "audio"),
            {},
        )
        try:
            duration = float(fmt.get("duration"))
        except (TypeError, ValueError):
            duration = 0.0
        try:
            stream_duration = float(video.get("duration"))
        except (TypeError, ValueError):
            stream_duration = 0.0
        try:
            bitrate = int(float(fmt.get("bit_rate")))
        except (TypeError, ValueError):
            bitrate = -1
        has_video = bool(video)
        has_audio = bool(audio)
        packet_timestamps: list[float] = []
        packet_bytes = 0
        for packet in packets:
            if not isinstance(packet, dict):
                continue
            try:
                packet_bytes += max(0, int(packet.get("size", 0) or 0))
            except (TypeError, ValueError):
                pass
            for key in ("pts_time", "dts_time"):
                try:
                    value = float(packet.get(key))
                except (TypeError, ValueError):
                    continue
                if value == value and value not in packet_timestamps:
                    packet_timestamps.append(value)
        packet_span = (
            max(packet_timestamps) - min(packet_timestamps)
            if len(packet_timestamps) >= 2
            else 0.0
        )
        # ``time_base`` only describes a clock, it does not prove that media
        # timestamps are advancing.  Require an observed positive duration or
        # a bounded packet timestamp span; this rejects static HTTP/200
        # responses and metadata-only probes, including live TS without a
        # container duration.
        timestamp_ok = bool(duration > 0 or stream_duration > 0 or packet_span > 0.0)
        container = str(fmt.get("format_name") or "").split(",")[0]
        video_codec = str(video.get("codec_name") or "")
        packet_seen = bool(packets)
        failure_kind = ""
        if completed.returncode != 0:
            failure_kind = classify_failure(stderr, http_status).value
        elif not has_video:
            failure_kind = "NO_MEDIA"
        elif not packet_seen:
            # A stream metadata response alone is not proof that the remote
            # endpoint is producing media.  The command requests a bounded
            # packet window, so require at least one packet before selecting
            # this candidate for a real connection.
            failure_kind = "NO_MEDIA"
        elif not container:
            failure_kind = "UNSUPPORTED_PROTOCOL"
        elif not video_codec:
            failure_kind = "UNSUPPORTED_CODEC"
        elif not timestamp_ok:
            failure_kind = "TIMESTAMP_DISCONTINUITY"
        return ProbeResult(
            candidate_id=candidate.candidate_id,
            reachable=completed.returncode == 0 and bool(streams),
            first_packet_ms=elapsed_ms if packet_seen else -1,
            http_status=http_status,
            protocol=candidate.protocol or container,
            container=container,
            video_codec=video_codec,
            audio_codec=str(audio.get("codec_name") or ""),
            has_video=has_video,
            has_audio=has_audio,
            timestamp_ok=timestamp_ok,
            failure_kind=failure_kind,
            failure_detail=redact_text(stderr),
            duration_ms=max(0, int(max(duration, stream_duration, packet_span) * 1000)),
            bitrate=bitrate,
            retry_after_seconds=retry_after,
            probe_duration_ms=elapsed_ms,
            # Prefer the media packet sizes reported by ffprobe.  Falling
            # back to JSON length would count diagnostics rather than bytes
            # actually consumed from the remote stream.
            read_bytes=(
                packet_bytes
                if packet_bytes > 0
                else len((completed.stdout or "").encode("utf-8", "replace"))
            ),
            server_id=str(urlparse(candidate.url).hostname or ""),
            cdn_id=candidate.cdn_id,
        )

    @staticmethod
    def _cancelled(cancellation: object) -> bool:
        try:
            value = getattr(cancellation, "is_set", None)
            return bool(value() if callable(value) else value)
        except Exception:
            # A broken cancellation token must fail closed so that a probe
            # cannot keep an orphaned ffprobe process alive indefinitely.
            return True

    def _run_cancellable(
        self,
        command: list[str],
        *,
        timeout: float,
        cancellation: object,
    ) -> SimpleNamespace:
        """Run ffprobe while allowing a token to terminate the child process."""
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + timeout
        try:
            while True:
                if self._cancelled(cancellation):
                    self._terminate_process(process)
                    stdout, stderr = process.communicate()
                    raise _ProbeCancelled(stdout=stdout, stderr=stderr)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._terminate_process(process)
                    stdout, stderr = process.communicate()
                    raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr)
                try:
                    stdout, stderr = process.communicate(
                        timeout=min(0.2, remaining),
                    )
                    return SimpleNamespace(
                        returncode=process.returncode,
                        stdout=stdout,
                        stderr=stderr,
                    )
                except subprocess.TimeoutExpired:
                    continue
        finally:
            if process.poll() is None:
                self._terminate_process(process)

    @staticmethod
    def _terminate_process(process: subprocess.Popen) -> None:
        try:
            process.terminate()
            process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
                process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                pass

    def probe_candidates(
        self,
        candidates: Iterable[StreamCandidate],
        *,
        timeout_sec: float = 8.0,
        max_concurrency: int = 3,
        request_id: str = "",
        deadline_monotonic: float | None = None,
        cancellation: object | None = None,
        network_context: Mapping[str, object] | None = None,
    ) -> dict[str, ProbeResult]:
        items = list(candidates)
        if not items:
            return {}
        workers = max(1, min(int(max_concurrency), len(items)))
        requests = [
            ProbeRequest(
                candidate=item,
                timeout_sec=timeout_sec,
                ffprobe_path=self.ffprobe_path,
                request_id=request_id,
                deadline_monotonic=deadline_monotonic,
                network_context=dict(network_context or {}),
                cancellation=cancellation,
            )
            for item in items
        ]
        if workers == 1:
            # Signed multi-CDN platforms (Huya) must not fan out probes: the
            # first reachable line wins, remaining signatures stay unused.
            results: dict[str, ProbeResult] = {}
            for request in requests:
                result = self.probe(request)
                results[request.candidate.candidate_id] = result
                if result.ok:
                    break
            return results
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="lsc-probe",
        ) as executor:
            futures = [executor.submit(self.probe, item) for item in requests]
            return {
                item.candidate_id: future.result()
                for item, future in zip(items, futures, strict=True)
            }


def score_candidate(
    candidate: StreamCandidate,
    result: ProbeResult,
    *,
    requested_quality: str = "",
    capabilities: PlatformCapabilities | None = None,
    history_score: float = 0.0,
) -> float:
    if not result.ok:
        return float("-inf")
    score = 1_000.0 + history_score
    if requested_quality and candidate.quality_id == requested_quality:
        score += 250.0
    if candidate.quality_id:
        score += max(0, 100 - candidate.priority)
    # Preserve resolver-side quality rank/confidence and scoped CDN health in
    # the final choice.  These values are hints only; a candidate still needs
    # a successful real-media probe before it can be selected.
    try:
        quality_rank = float(candidate.quality_rank or 0)
    except (TypeError, ValueError):
        quality_rank = 0.0
    try:
        confidence = float(candidate.confidence or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    score += max(0.0, min(100.0, quality_rank)) * 0.5
    score += max(0.0, min(1.0, confidence)) * 10.0
    try:
        cdn_health = float(candidate.raw_metadata.get("cdn_health_score", 0.0) or 0.0)
    except (TypeError, ValueError):
        cdn_health = 0.0
    score += max(-100.0, min(100.0, cdn_health))
    if result.has_audio:
        score += 40.0
    if result.first_packet_ms >= 0:
        score += max(0.0, 100.0 - result.first_packet_ms / 100.0)
    if capabilities is not None:
        try:
            score += max(
                0,
                len(capabilities.preferred_protocols)
                - capabilities.preferred_protocols.index(result.protocol),
            ) * 15.0
        except ValueError:
            pass
    return score


def select_best_candidate(
    candidates: Iterable[StreamCandidate],
    results: Mapping[str, ProbeResult],
    *,
    requested_quality: str = "",
    capabilities: PlatformCapabilities | None = None,
) -> StreamCandidate | None:
    best: tuple[float, StreamCandidate] | None = None
    for candidate in candidates:
        result = results.get(candidate.candidate_id)
        if result is None:
            continue
        # A failed probe must never become the fallback merely because it is
        # the first (or only) candidate.  ``score_candidate`` uses -inf for
        # diagnostics, but selection must treat that as non-selectable.
        if not result.ok:
            continue
        raw_history = candidate.raw_metadata.get("history_score", 0.0)
        try:
            history_score = float(raw_history or 0.0)
        except (TypeError, ValueError):
            history_score = 0.0
        score = score_candidate(
            candidate,
            result,
            requested_quality=requested_quality,
            capabilities=capabilities,
            history_score=history_score,
        )
        if best is None or score > best[0]:
            best = (score, replace(
                candidate,
                protocol=result.protocol or candidate.protocol,
                container=result.container or candidate.container,
                video_codec=result.video_codec or candidate.video_codec,
                audio_codec=result.audio_codec or candidate.audio_codec,
            ))
    return best[1] if best is not None else None


__all__ = [
    "ProbeService",
    "score_candidate",
    "select_best_candidate",
    "summarize_probe_failures",
]


_PROBE_FAILURE_LABELS = {
    "CDN_FORBIDDEN": "线路被拒绝",
    "CONNECT_TIMEOUT": "连接超时",
    "SIGNATURE_EXPIRED": "签名过期",
    "RATE_LIMITED": "请求过于频繁",
    "AUTH_REQUIRED": "需要登录",
    "OFFLINE": "未开播",
    "NO_MEDIA": "没有可用媒体",
    "TIMESTAMP_DISCONTINUITY": "时间戳异常",
}


def summarize_probe_failures(results: Mapping[str, ProbeResult]) -> str:
    """Build a user-facing reason when no probed candidate is selectable."""
    kinds: list[str] = []
    for result in results.values():
        kind = str(getattr(result, "failure_kind", "") or "")
        if kind and kind not in {"CANCELLED"} and kind not in kinds:
            kinds.append(kind)
    if not kinds:
        return "候选直播流未通过真实媒体探测"
    pretty = "、".join(_PROBE_FAILURE_LABELS.get(kind, kind) for kind in kinds[:3])
    return f"候选直播流未通过真实媒体探测（{pretty}）"
