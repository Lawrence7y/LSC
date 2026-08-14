"""Structured failure classification.

Everything recoverable flows through :class:`FailureKind` before producing a
user-facing string. This replaces ad-hoc ``"403" in error`` string checks in
the media path (plan §6.5 / §9).
"""
from __future__ import annotations

import enum
import re
import time
from email.utils import parsedate_to_datetime


class FailureKind(str, enum.Enum):
    """Typed failure categories shared across resolver/probe/sinks."""

    OFFLINE = "OFFLINE"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_EXPIRED = "AUTH_EXPIRED"
    REGION_RESTRICTED = "REGION_RESTRICTED"
    SIGNATURE_EXPIRED = "SIGNATURE_EXPIRED"
    CDN_FORBIDDEN = "CDN_FORBIDDEN"
    RATE_LIMITED = "RATE_LIMITED"
    DNS_FAILURE = "DNS_FAILURE"
    CONNECT_TIMEOUT = "CONNECT_TIMEOUT"
    CONNECTION_RESET = "CONNECTION_RESET"
    NO_MEDIA = "NO_MEDIA"
    UNSUPPORTED_PROTOCOL = "UNSUPPORTED_PROTOCOL"
    UNSUPPORTED_CODEC = "UNSUPPORTED_CODEC"
    TIMESTAMP_DISCONTINUITY = "TIMESTAMP_DISCONTINUITY"
    PREVIEW_ENCODER_FAILURE = "PREVIEW_ENCODER_FAILURE"
    RECORDING_SINK_FAILURE = "RECORDING_SINK_FAILURE"
    DISK_FULL = "DISK_FULL"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    PROCESS_CRASH = "PROCESS_CRASH"
    PLATFORM_SCHEMA_CHANGED = "PLATFORM_SCHEMA_CHANGED"
    UNKNOWN = "UNKNOWN"


# Ordered rules: first match wins. Keep CDN_FORBIDDEN before generic 403 net
# patterns and RATE_LIMITED before generic 5xx.
_RULES: list[tuple[FailureKind, re.Pattern]] = [
    # Platform auth / schema
    (FailureKind.PLATFORM_SCHEMA_CHANGED, re.compile(r"解析.*失败|parse.*fail|extract.*fail|schema", re.I)),
    (FailureKind.AUTH_EXPIRED, re.compile(r"登录.*过期|登录态.*失效|cookie.*expired|过期.*cookie|鉴权失败|sign.?out", re.I)),
    (FailureKind.AUTH_REQUIRED, re.compile(r"Cookie|登录|login required|需要登录|需要.*凭证", re.I)),
    (FailureKind.REGION_RESTRICTED, re.compile(r"region|geo.?block|location restricted|地区|地域", re.I)),
    # Signature / CDN / rate limits. HTTP 403 must win over wrapper text such
    # as "流地址可能已过期", otherwise a CDN reject is recovered as a refresh
    # of the same signed URL.
    (FailureKind.RATE_LIMITED, re.compile(
        r"429|412|Too Many Requests|Precondition Failed|limit.*exceed|频率.*限制|限流|请求被拦截",
        re.I,
    )),
    (FailureKind.CDN_FORBIDDEN, re.compile(r"403|Forbidden|拒绝访问|风控", re.I)),
    (FailureKind.SIGNATURE_EXPIRED, re.compile(r"签名.*过期|链接已过期|token.*expir|wsSecret.*无效|鉴权.*失效", re.I)),
    # DNS / connect. Windows FFmpeg maps ETIMEDOUT to error -138.
    (FailureKind.DNS_FAILURE, re.compile(r"getaddrinfo|Name or service not known|不知道这样的主机|主机名无法解析|DNS", re.I)),
    (FailureKind.CONNECT_TIMEOUT, re.compile(r"timed out|超时|timeout|Error number -138\b", re.I)),
    (FailureKind.CONNECTION_RESET, re.compile(r"can't start new thread|Expecting value", re.I)),
    (FailureKind.CONNECTION_RESET, re.compile(r"Connection reset|reset by peer|ECONNRESET|提前.*退出|code=0", re.I)),
    (FailureKind.CONNECTION_RESET, re.compile(r"Connection refused|ECONNREFUSED|拒绝连接|连接被拒绝", re.I)),
    (FailureKind.CONNECTION_RESET, re.compile(r"\bcode\s*[=:]\s*0\b", re.I)),
    (FailureKind.CONNECTION_RESET, re.compile(r"End of file|Error during demuxing|demuxing: I/O error", re.I)),
    # Media / codec
    (FailureKind.UNSUPPORTED_CODEC, re.compile(r"Unsupported codec|unsupported format|cannot find decoder|cannot find encoder|Decoder.*not found|Encoder.*not found", re.I)),
    (FailureKind.TIMESTAMP_DISCONTINUITY, re.compile(r"timestamp.*(discontinu|reset|jump)|时间戳|DTS|PTS.*(discontinu|error)", re.I)),
    (FailureKind.NO_MEDIA, re.compile(r"Stream not found|No stream|no video|无音轨|没有.*流|Input/output error.*stream|stream ends prematurely|EOF|eof", re.I)),
    (FailureKind.NO_MEDIA, re.compile(r"404|Not Found", re.I)),
    (FailureKind.DISK_FULL, re.compile(r"No space left|ENOSPC|disk full|磁盘空间不足|磁盘已满", re.I)),
    (FailureKind.PERMISSION_DENIED, re.compile(r"Permission denied|EACCES|WinError 5|拒绝访问|无法写入|权限不足", re.I)),
    (FailureKind.PREVIEW_ENCODER_FAILURE, re.compile(r"preview.*(encoder|nvenc|failed)|预览.*(编码|转码).*(失败|不可用)", re.I)),
    (FailureKind.RECORDING_SINK_FAILURE, re.compile(r"recording.*(sink|failed|write)|录制.*(写入|失败)", re.I)),
    (FailureKind.OFFLINE, re.compile(r"未开播|房间未开播|直播间未开播|not live|offline|主播.*下播", re.I)),
    # Generic server error -> treat as retryable transit failure
    (FailureKind.CONNECTION_RESET, re.compile(r"Server returned 5\d\d|Internal Server Error", re.I)),
]

_RETRY_AFTER_RE = re.compile(
    r"retry[- ]after\s*[:=]\s*([^\r\n;]+)",
    re.IGNORECASE,
)


def extract_retry_after(raw: str | None) -> float | None:
    """Extract a bounded Retry-After delay from upstream diagnostics."""
    if not raw:
        return None
    match = _RETRY_AFTER_RE.search(str(raw))
    if not match:
        return None
    value = match.group(1).strip()
    try:
        # Avoid allowing a malformed server response to freeze recovery for
        # an unbounded period.  The supervisor may still apply its own cap.
        return max(0.0, min(300.0, float(value)))
    except (TypeError, ValueError):
        # HTTP also permits an absolute date.  Parse it when a caller passes a
        # raw header rather than an FFmpeg-normalized numeric diagnostic.
        try:
            target = parsedate_to_datetime(value).timestamp()
            return max(0.0, min(300.0, target - time.time()))
        except (TypeError, ValueError, OverflowError, IndexError):
            return None


def classify_failure(raw: str | None, http_status: int | None = None) -> FailureKind:
    """Classify a raw error string / HTTP status into a ``FailureKind``.

    ``http_status`` takes precedence for well-known codes, then the raw string
    is matched against ordered regex rules. Falls back to ``UNKNOWN``.
    """
    if http_status is not None:
        if http_status == 404:
            return FailureKind.OFFLINE
        if http_status == 401:
            return FailureKind.AUTH_REQUIRED
        if http_status == 403:
            return FailureKind.CDN_FORBIDDEN
        if http_status == 410:
            return FailureKind.SIGNATURE_EXPIRED
        if http_status in {412, 429}:
            return FailureKind.RATE_LIMITED
        if 500 <= http_status < 600:
            return FailureKind.CONNECTION_RESET

    if not raw or not isinstance(raw, str) or not raw.strip():
        return FailureKind.UNKNOWN
    text = raw.strip()
    for kind, pattern in _RULES:
        if pattern.search(text):
            return kind
    return FailureKind.UNKNOWN


def normalize_failure_kind(value: object) -> FailureKind:
    """Normalize enum instances and wire strings to one failure kind.

    Python's ``str(FailureKind.AUTH_REQUIRED)`` is
    ``"FailureKind.AUTH_REQUIRED"`` on supported runtimes, while the
    WebSocket/JSON contract carries the plain ``"AUTH_REQUIRED"`` value.
    Accept both forms at every boundary so legacy callers cannot silently
    bypass auth, recovery, or user-action handling.
    """
    if isinstance(value, FailureKind):
        return value
    raw = str(value or "").strip().upper().replace("-", "_")
    if raw.startswith("FAILUREKIND."):
        raw = raw.split(".", 1)[1]
    try:
        return FailureKind(raw)
    except ValueError:
        return FailureKind.UNKNOWN


def is_recoverable_failure(kind: object) -> bool:
    """Whether a typed failure warrants automatic recovery.

    Mirrors the legacy regex recoverability semantics so a typed path can be
    adopted without changing existing auto-reconnect behavior.
    """
    kind = normalize_failure_kind(kind)
    return kind in {
        FailureKind.SIGNATURE_EXPIRED,
        FailureKind.CDN_FORBIDDEN,
        FailureKind.RATE_LIMITED,
        FailureKind.DNS_FAILURE,
        FailureKind.CONNECT_TIMEOUT,
        FailureKind.CONNECTION_RESET,
        FailureKind.NO_MEDIA,
        FailureKind.PREVIEW_ENCODER_FAILURE,
        FailureKind.RECORDING_SINK_FAILURE,
        FailureKind.PROCESS_CRASH,
    }


_USER_MESSAGES: dict[FailureKind, str] = {
    FailureKind.OFFLINE: "主播已下播",
    FailureKind.AUTH_REQUIRED: "需要登录凭证，请配置该平台登录凭据",
    FailureKind.AUTH_EXPIRED: "登录态已过期，请更新凭据",
    FailureKind.REGION_RESTRICTED: "该直播内容受地区限制，无法连接",
    FailureKind.SIGNATURE_EXPIRED: "直播流签名已过期，正在刷新",
    FailureKind.CDN_FORBIDDEN: "平台拒绝了连接（403），正在切换线路",
    FailureKind.RATE_LIMITED: "平台限流，稍后自动恢复",
    FailureKind.DNS_FAILURE: "域名解析失败，请检查网络连接",
    FailureKind.CONNECT_TIMEOUT: "连接直播服务器超时",
    FailureKind.CONNECTION_RESET: "网络波动，正在恢复连接",
    FailureKind.NO_MEDIA: "未找到可播放的直播流",
    FailureKind.UNSUPPORTED_PROTOCOL: "不支持的直播协议",
    FailureKind.UNSUPPORTED_CODEC: "视频编码不兼容，已尝试切换",
    FailureKind.TIMESTAMP_DISCONTINUITY: "直播流时间戳异常，正在重建",
    FailureKind.PREVIEW_ENCODER_FAILURE: "预览已降级，录制未受影响",
    FailureKind.RECORDING_SINK_FAILURE: "录制正在恢复",
    FailureKind.DISK_FULL: "磁盘空间不足，录制已停止",
    FailureKind.PERMISSION_DENIED: "输出目录不可写",
    FailureKind.PROCESS_CRASH: "录制进程异常退出，正在重启",
    FailureKind.PLATFORM_SCHEMA_CHANGED: "平台页面结构可能已变化，适配器需更新",
    FailureKind.UNKNOWN: "发生未知错误",
}


def failure_kind_to_message(kind: FailureKind) -> str:
    """User-facing Chinese message for a typed failure."""
    return _USER_MESSAGES.get(
        normalize_failure_kind(kind),
        _USER_MESSAGES[FailureKind.UNKNOWN],
    )


__all__ = [
    "FailureKind",
    "classify_failure",
    "extract_retry_after",
    "failure_kind_to_message",
    "is_recoverable_failure",
    "normalize_failure_kind",
]
