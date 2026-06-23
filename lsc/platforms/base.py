"""Core platform adapter primitives."""
from __future__ import annotations

import abc
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

ERROR_UNSUPPORTED_URL = "unsupported_url"
ERROR_OFFLINE = "offline"
ERROR_RESTRICTED = "restricted"
ERROR_PARSE_FAILED = "parse_failed"

_log = logging.getLogger(__name__)

# Unified HTTP defaults for all platform adapters.
DEFAULT_HTTP_TIMEOUT = 12  # seconds per attempt
DEFAULT_HTTP_RETRIES = 2   # extra attempts after the first
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def fetch_url(url: str, *, headers: dict[str, str] | None = None,
              timeout: int = DEFAULT_HTTP_TIMEOUT,
              retries: int = DEFAULT_HTTP_RETRIES) -> str:
    """Fetch a URL with unified timeout and retry policy.

    Returns the response body as text. Raises on final failure.
    """
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            request = Request(url, headers=headers or {})
            with urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                wait = 0.5 * (attempt + 1)
                _log.debug("fetch_url retry %d/%d for %s: %s", attempt + 1, retries, url, exc)
                time.sleep(wait)
    raise last_exc  # type: ignore[misc]


def fetch_json(url: str, *, headers: dict[str, str] | None = None,
               params: dict[str, str] | None = None,
               timeout: int = DEFAULT_HTTP_TIMEOUT,
               retries: int = DEFAULT_HTTP_RETRIES) -> dict[str, Any]:
    """Fetch a URL and parse JSON response with unified timeout/retry."""
    import json
    query = urlencode(params or {})
    request_url = f"{url}?{query}" if query else url
    body = fetch_url(request_url, headers=headers, timeout=timeout, retries=retries)
    data = json.loads(body)
    return data if isinstance(data, dict) else {}


def fetch_head(url: str, *, headers: dict[str, str] | None = None,
               timeout: int = DEFAULT_HTTP_TIMEOUT,
               retries: int = DEFAULT_HTTP_RETRIES) -> str:
    """Issue a HEAD request and return the final URL after redirects.

    Useful for expanding short links without downloading the response body.
    Returns the original URL if no redirect occurred or on final failure.
    """
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            request = Request(url, headers=headers or {}, method="HEAD")
            with urlopen(request, timeout=timeout) as response:
                return response.geturl() or url
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                wait = 0.5 * (attempt + 1)
                _log.debug("fetch_head retry %d/%d for %s: %s", attempt + 1, retries, url, exc)
                time.sleep(wait)
    _log.debug("fetch_head failed for %s: %s", url, last_exc)
    return url


def extract_json_after_marker(
    html: str,
    marker: str,
    *,
    sanitize: Callable[[str], str] | None = None,
) -> dict[str, Any] | None:
    """Extract a JSON object immediately following ``marker`` in ``html``.

    The function searches for the first ``{`` after the marker and parses
    the matching JSON object. If ``sanitize`` is provided, it is applied to
    the raw text before JSON parsing (useful for replacing ``undefined``
    literals with ``null``).

    Returns ``None`` if the marker or a valid JSON object is not found.
    """
    import json

    marker_index = html.find(marker)
    if marker_index < 0:
        return None

    brace_index = html.find("{", marker_index)
    if brace_index < 0:
        return None

    payload = html[brace_index:]
    if sanitize is not None:
        payload = sanitize(payload)

    try:
        decoder = json.JSONDecoder()
        data, _ = decoder.raw_decode(payload)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def extract_braced_block(html: str, start_index: int) -> str:
    """Return the brace-balanced block starting at ``start_index``.

    Respects double-quoted strings and backslash escapes. Returns an empty
    string if no balanced block can be found.
    """
    if start_index >= len(html) or html[start_index] != "{":
        return ""

    depth = 0
    in_str = False
    esc = False
    for i in range(start_index, len(html)):
        ch = html[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return html[start_index : i + 1]
    return ""


def sanitize_undefined_to_null(text: str) -> str:
    """Replace JavaScript ``undefined`` literals with JSON ``null``."""
    return re.sub(r"\bundefined\b", "null", text)


def headers_to_ffmpeg_input_args(headers: dict[str, str] | None) -> list[str]:
    """Convert request headers to FFmpeg input arguments."""
    def _sanitize_header_part(value: object) -> str:
        return str(value).replace("\r", "").replace("\n", "").strip()

    clean_headers = {
        _sanitize_header_part(key): _sanitize_header_part(value)
        for key, value in (headers or {}).items()
        if _sanitize_header_part(key) and _sanitize_header_part(value)
    }
    if not clean_headers:
        return []

    header_blob = "".join(f"{key}: {value}\r\n" for key, value in clean_headers.items())
    return ["-headers", header_blob]


@dataclass(slots=True)
class StreamInfo:
    platform: str
    room_url: str
    stream_url: str = ""
    title: str = ""
    streamer: str = ""
    is_live: bool = False
    quality_urls: dict[str, str] = field(default_factory=dict)
    selected_quality: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    error_code: str = ""

    def to_legacy_dict(self) -> dict[str, Any]:
        """Return the dictionary shape consumed by the current GUI code."""
        return {
            "platform": self.platform,
            "roomUrl": self.room_url,
            "streamUrl": self.stream_url,
            "title": self.title,
            "streamerName": self.streamer,
            "isLive": self.is_live,
            "selectedQuality": self.selected_quality,
            "availableQualities": list(self.quality_urls.keys()),
            "qualityUrls": dict(self.quality_urls),
            "error": self.error,
            "errorCode": self.error_code,
            "_headers": dict(self.headers),
            "_inputArgs": headers_to_ffmpeg_input_args(self.headers),
            "_raw": dict(self.raw),
        }


class PlatformAdapter(Protocol):
    """Interface implemented by concrete platform adapters.

    Statelessness contract
    ----------------------
    Adapters MUST be stateless: the ``parse`` method must not mutate any
    instance attributes and must not rely on mutable instance state shared
    between calls. This allows adapters to be safely registered as module-level
    singletons (see ``lsc.platforms.registry._DEFAULT_ADAPTERS``) and reused
    concurrently across multiple rooms.

    All per-request state (headers, parsed JSON, temporary URLs) must live in
    local variables inside ``parse`` and be returned via :class:`StreamInfo`.
    """

    platform: str
    display_name: str

    def can_handle(self, url: str) -> bool:
        """Return whether this adapter owns the URL."""

    def parse(self, url: str) -> StreamInfo:
        """Parse a room URL or stream URL into StreamInfo."""


class BasePlatformAdapter(abc.ABC):
    """Optional base class for stateless platform adapters.

    Provides common helpers for URL matching, failure result construction,
    and HTML/JSON extraction. Concrete adapters can inherit from this class
    and still satisfy the :class:`PlatformAdapter` protocol.
    """

    @property
    @abc.abstractmethod
    def platform(self) -> str:
        """Platform identifier (e.g. 'bilibili')."""

    @property
    @abc.abstractmethod
    def display_name(self) -> str:
        """Human-readable platform name (e.g. '哔哩哔哩')."""

    def _failed(
        self,
        url: str,
        error: str,
        error_code: str = ERROR_PARSE_FAILED,
        *,
        headers: dict[str, str] | None = None,
        raw: dict[str, Any] | None = None,
    ) -> StreamInfo:
        """Build a failed StreamInfo for this platform."""
        return StreamInfo(
            platform=self.platform,
            room_url=url,
            error=error,
            error_code=error_code,
            headers=headers or {},
            raw=raw or {},
        )

    def _success(
        self,
        url: str,
        *,
        stream_url: str = "",
        title: str = "",
        streamer: str = "",
        is_live: bool = False,
        quality_urls: dict[str, str] | None = None,
        selected_quality: str = "",
        headers: dict[str, str] | None = None,
        raw: dict[str, Any] | None = None,
    ) -> StreamInfo:
        """Build a successful StreamInfo for this platform."""
        return StreamInfo(
            platform=self.platform,
            room_url=url,
            stream_url=stream_url,
            title=title,
            streamer=streamer,
            is_live=is_live,
            quality_urls=quality_urls or {},
            selected_quality=selected_quality,
            headers=headers or {},
            raw=raw or {},
        )

    def _can_handle_by_hosts(
        self,
        url: str,
        hosts: set[str],
        *,
        path_regex: str | None = None,
    ) -> bool:
        """Return True when the URL matches one of the given hosts.

        If ``path_regex`` is provided, the URL path must also match it.
        """
        try:
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"}:
                return False
            if parsed.netloc.lower() not in hosts:
                return False
            if path_regex is not None and not re.search(path_regex, parsed.path):
                return False
            return True
        except Exception:
            return False

    @abc.abstractmethod
    def parse(self, url: str) -> StreamInfo:
        """Parse a room URL or stream URL into StreamInfo."""

    def can_handle(self, url: str) -> bool:
        """Default implementation: subclasses should override this.

        DirectAdapter overrides with direct stream URL detection; most
        platform adapters should use :meth:`_can_handle_by_hosts`.
        """
        return False
