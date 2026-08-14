"""Shared redaction helpers for URLs, headers, commands and diagnostics."""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SENSITIVE_QUERY_KEYS = {
    "token",
    "stream_token",
    "streamtoken",
    "play_token",
    "playtoken",
    "access_token",
    "authtoken",
    "auth_token",
    "authorization",
    "auth",
    "auth_key",
    "signature",
    "sign",
    "sig",
    "hmac",
    "wssecret",
    "ws_time",
    "wstime",
    "hdnea",
    "hdnts",
    "expire",
    "expires",
    "expires_at",
    "timestamp",
    "ts",
    "pxcode",
    "ms_token",
    "mstoken",
    "ttwid",
    "odin_tt",
    "xsec_token",
    "dyshid",
    "session",
    "sessionid",
    "session_id",
    "sid",
    "sid_guard",
    "vtoken",
}
_SENSITIVE_HEADER_KEYS = {
    "authorization",
    "cookie",
    "referer",
    "proxy-authorization",
    "x-auth-token",
    "x-api-key",
}
_SENSITIVE_INLINE = re.compile(
    r"(?i)(authorization|cookie|proxy-authorization|token|signature|sign|wssecret|access_token)"
    r"(\s*[:=]\s*)([^;\s,]+)",
)
_SENSITIVE_HEADER_BLOB = re.compile(
    r"(?is)((?:cookie|authorization|referer|proxy-authorization)\s*[:=]\s*)"
    r"(.*?)(?=(?:\\r\\n|\r\n|\n|$))",
)


def redact_url(value: str | None) -> str:
    """Keep scheme/host/path while replacing known credential query values."""
    raw = str(value or "")
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        if not parts.scheme or not parts.netloc:
            return _SENSITIVE_INLINE.sub(r"\1\2<redacted>", raw)
        query = []
        for key, item in parse_qsl(parts.query, keep_blank_values=True):
            if key.lower() in _SENSITIVE_QUERY_KEYS:
                query.append((key, "<redacted>"))
            else:
                # Direct/Generic wrappers sometimes carry the actual signed
                # media URL in a ``url``/``src`` query value.  Redact that
                # nested URL before re-encoding the outer query as well.
                query.append((key, redact_url(item) if "://" in item else item))
        # Drop URL userinfo as well; signed media endpoints occasionally use
        # ``user:token@host`` instead of query parameters.
        hostname = parts.hostname or ""
        if parts.port:
            hostname = f"{hostname}:{parts.port}"
        return urlunsplit(
            (parts.scheme, hostname, parts.path, urlencode(query), "")
        )
    except Exception:
        return _SENSITIVE_INLINE.sub(r"\1\2<redacted>", raw)


def redact_headers(headers: Mapping[str, object] | None) -> dict[str, str]:
    """Return a safe header copy suitable for logs and events."""
    result: dict[str, str] = {}
    for key, value in (headers or {}).items():
        name = str(key)
        if name.lower() in _SENSITIVE_HEADER_KEYS:
            result[name] = "<redacted>"
        else:
            result[name] = _SENSITIVE_INLINE.sub(
                r"\1\2<redacted>", str(value)
            )
    return result


def redact_text(value: object) -> str:
    """Redact inline credentials and URL-like values in arbitrary text."""
    text = str(value or "")
    # FFmpeg diagnostics commonly contain a repr() of a complete ``-headers``
    # blob.  Redacting only the first ``name=value`` token would leak the
    # remaining Cookie pairs (for example SESSDATA) after a semicolon.  Treat
    # each sensitive header line as opaque, supporting both real and escaped
    # CRLF sequences found in subprocess exception strings.
    text = _SENSITIVE_HEADER_BLOB.sub(r"\1<redacted>", text)
    text = _SENSITIVE_INLINE.sub(r"\1\2<redacted>", text)
    return re.sub(
        r"https?://[^\s'\"<>]+",
        lambda match: redact_url(match.group(0)),
        text,
    )


def redact_command(command: Sequence[object] | None) -> list[str]:
    """Redact command arguments without changing safe argument structure."""
    result: list[str] = []
    redact_next = False
    for item in command or ():
        arg = str(item)
        if redact_next:
            result.append("<redacted>")
            redact_next = False
            continue
        lowered = arg.lower()
        if lowered in {"-headers", "-http_proxy", "-user_agent", "-referer"}:
            result.append(arg)
            # Header blobs, proxy URLs and referers may carry cookies,
            # signed query parameters or room identifiers.  Keep the
            # argument name for diagnostics but never echo its value.
            redact_next = lowered in {
                "-headers",
                "-http_proxy",
                "-referer",
            }
            continue
        result.append(redact_text(arg))
    return result


def redact_mapping(value: Mapping[str, object] | None) -> dict[str, object]:
    """Recursively redact a diagnostic mapping while preserving its shape."""
    result: dict[str, object] = {}
    for key, item in (value or {}).items():
        lowered = str(key).lower()
        if lowered in _SENSITIVE_QUERY_KEYS or lowered in _SENSITIVE_HEADER_KEYS:
            result[str(key)] = "<redacted>"
        elif isinstance(item, Mapping):
            result[str(key)] = redact_mapping(item)
        elif isinstance(item, (list, tuple)):
            result[str(key)] = [
                redact_mapping(entry) if isinstance(entry, Mapping)
                else redact_text(entry) if isinstance(entry, str) else entry
                for entry in item
            ]
        elif item is None or isinstance(item, (bool, int, float)):
            # Preserve structured diagnostic types; only textual values need
            # credential redaction.  This keeps reports machine-consumable
            # (for example, ``passed`` remains a boolean).
            result[str(key)] = item
        else:
            result[str(key)] = redact_text(item)
    return result


__all__ = [
    "redact_command",
    "redact_headers",
    "redact_mapping",
    "redact_text",
    "redact_url",
]
