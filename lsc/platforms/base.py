"""Core platform adapter primitives."""
from __future__ import annotations

import abc
import logging
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .redaction import redact_text, redact_url

ERROR_UNSUPPORTED_URL = "unsupported_url"
ERROR_OFFLINE = "offline"
ERROR_RESTRICTED = "restricted"
ERROR_PARSE_FAILED = "parse_failed"

_log = logging.getLogger(__name__)

# Unified HTTP defaults for all platform adapters.
DEFAULT_HTTP_TIMEOUT = 12  # seconds per attempt
DEFAULT_HTTP_RETRIES = 2   # extra attempts after the first
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def _is_private_ip(hostname: str) -> bool:
    """检查主机名是否解析为私有/内网 IP（SSRF 防护）。

    仅阻止真正的私有/内网地址：
    - RFC 1918 私有地址（10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16）
    - loopback（127.0.0.0/8）
    - link-local（169.254.0.0/16）

    注意：不阻止 198.18.0.0/15 等保留地址，因为部分网络环境（企业代理/VPN）
    会使用这类地址作为合法网关，且实际 HTTP 请求可以正常到达。
    """
    import ipaddress
    import socket

    # 明确定义的私有网络范围（仅 RFC 1918 + loopback + link-local）
    _PRIVATE_NETWORKS = [
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("192.168.0.0/16"),
        ipaddress.ip_network("127.0.0.0/8"),
        ipaddress.ip_network("169.254.0.0/16"),
    ]
    try:
        addr_info = socket.getaddrinfo(hostname, None)
        for _family, _, _, _, sockaddr in addr_info:
            ip = ipaddress.ip_address(sockaddr[0])
            for network in _PRIVATE_NETWORKS:
                if ip in network:
                    return True
    except Exception:
        # 解析失败时保守处理，拒绝访问
        return True
    return False


def _validate_network_url(url: str) -> None:
    """Validate every URL before it is requested, including redirects."""
    from .url_policy import validate_public_url

    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"Only public HTTP/HTTPS URLs are supported, got: {url}")
    safe, reason = validate_public_url(url)
    if not safe:
        raise ValueError(reason)
    if _is_private_ip(parsed.hostname):
        raise ValueError(f"Access to private/internal IP is forbidden: {parsed.hostname}")


class _SafeRedirectHandler(HTTPRedirectHandler):
    """Reject unsafe redirect targets before urllib opens the next hop."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_network_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_SAFE_OPENER = build_opener(_SafeRedirectHandler())


def _opener_for_proxy(proxy_url: str = ""):
    """Build a safe redirect-checking opener with an optional scoped proxy."""
    proxy = str(proxy_url or "").strip()
    if not proxy:
        return _SAFE_OPENER
    parsed = urlparse(proxy)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("network proxy must be an explicit proxy URL")
    return build_opener(
        _SafeRedirectHandler(),
        ProxyHandler({"http": proxy, "https": proxy}),
    )


def fetch_url(url: str, *, headers: dict[str, str] | None = None,
              timeout: int = DEFAULT_HTTP_TIMEOUT,
              retries: int = DEFAULT_HTTP_RETRIES,
              proxy_url: str = "") -> str:
    """Fetch a URL with unified timeout and retry policy.

    Returns the response body as text. Raises on final failure.
    """
    # 安全检查：只允许 HTTP/HTTPS 协议
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"Only HTTP/HTTPS URLs are supported, got: {url}")

    # SSRF 防护：拒绝访问私有/内网 IP
    from urllib.parse import urlparse
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    if _is_private_ip(hostname):
        raise ValueError(f"Access to private/internal IP is forbidden: {hostname}")

    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            request = Request(url, headers=headers or {})
            with _opener_for_proxy(proxy_url).open(request, timeout=timeout) as response:
                final_url = response.geturl() or url
                _validate_network_url(final_url)
                raw_bytes: bytes = response.read()
                return raw_bytes.decode("utf-8", errors="replace")
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                wait = 0.5 * (attempt + 1)
                _log.debug("fetch_url retry %d/%d for %s: %s", attempt + 1, retries, redact_url(url), redact_text(exc))
                time.sleep(wait)
    raise last_exc  # type: ignore[misc]


def open_http_stream(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
    proxy_url: str = "",
):
    """Open a public HTTP(S) URL for binary streaming. Caller must close."""
    _validate_network_url(url)
    request = Request(url, headers=headers or {})
    response = _opener_for_proxy(proxy_url).open(request, timeout=timeout)
    try:
        _validate_network_url(response.geturl() or url)
    except Exception:
        response.close()
        raise
    return response



def fetch_json(url: str, *, headers: dict[str, str] | None = None,
               params: dict[str, str] | None = None,
               timeout: int = DEFAULT_HTTP_TIMEOUT,
               retries: int = DEFAULT_HTTP_RETRIES,
               proxy_url: str = "") -> dict[str, Any]:
    """Fetch a URL and parse JSON response with unified timeout/retry."""
    import json
    query = urlencode(params or {})
    request_url = f"{url}?{query}" if query else url
    body = fetch_url(
        request_url,
        headers=headers,
        timeout=timeout,
        retries=retries,
        proxy_url=proxy_url,
    )
    data = json.loads(body)
    return data if isinstance(data, dict) else {}


def fetch_head(url: str, *, headers: dict[str, str] | None = None,
               timeout: int = DEFAULT_HTTP_TIMEOUT,
               retries: int = DEFAULT_HTTP_RETRIES,
               proxy_url: str = "") -> str:
    """Issue a HEAD request and return the final URL after redirects.

    Useful for expanding short links without downloading the response body.
    Returns the original URL if no redirect occurred or on final failure.
    """
    # 安全检查：只允许 HTTP/HTTPS 协议
    if not url.startswith(("http://", "https://")):
        _log.warning("fetch_head called with non-HTTP URL: %s", redact_url(url))
        return url
    try:
        _validate_network_url(url)
    except ValueError:
        return url

    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            request = Request(url, headers=headers or {}, method="HEAD")
            with _opener_for_proxy(proxy_url).open(request, timeout=timeout) as response:
                final_url = response.geturl() or url
                try:
                    _validate_network_url(final_url)
                except ValueError:
                    return url
                return final_url
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                wait = 0.5 * (attempt + 1)
                _log.debug("fetch_head retry %d/%d for %s: %s", attempt + 1, retries, redact_url(url), redact_text(exc))
                time.sleep(wait)
    _log.debug("fetch_head failed for %s: %s", redact_url(url), redact_text(last_exc))
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

    user_agent = ""
    for key, value in clean_headers.items():
        if key.lower() == "user-agent" and not user_agent:
            user_agent = value

    args: list[str] = []
    if user_agent:
        # FFmpeg HTTP ignores User-Agent inside -headers and sends Lavf/* unless
        # this dedicated option is set.
        args.extend(["-user_agent", user_agent])
    header_blob = "".join(f"{key}: {value}\r\n" for key, value in clean_headers.items())
    args.extend(["-headers", header_blob])
    return args


def network_timeout_args(
    network_context: Mapping[str, object] | None = None,
    *,
    default_connect_sec: float = 10.0,
    default_read_sec: float = 15.0,
) -> list[str]:
    """Build bounded FFmpeg network timeout arguments from one request context.

    ``connect_timeout_sec`` and ``read_timeout_sec`` are deliberately kept in
    the non-secret network context so ffprobe and the real ingest process can
    consume the same timeout policy.  A single ``timeout_sec`` is accepted as
    a convenient override for both phases.  Values are clamped to prevent a
    malformed platform response from disabling the deadline entirely.
    """
    context = dict(network_context or {})

    def _value(key: str, fallback: float) -> float:
        raw = context.get(key, fallback)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = fallback
        return max(0.1, min(300.0, value))

    shared = context.get("timeout_sec")
    try:
        shared_value = float(shared) if shared not in (None, "") else None
    except (TypeError, ValueError):
        shared_value = None
    connect_default = shared_value if shared_value is not None else default_connect_sec
    read_default = shared_value if shared_value is not None else default_read_sec
    connect_sec = _value("connect_timeout_sec", connect_default)
    read_sec = _value("read_timeout_sec", read_default)
    return [
        "-timeout",
        str(max(100_000, int(connect_sec * 1_000_000))),
        "-rw_timeout",
        str(max(100_000, int(read_sec * 1_000_000))),
    ]


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
    category: str = ""
    # Optional V2-only alternatives.  Legacy consumers continue to see the
    # stable ``quality_urls`` mapping; Resolver consumes this richer list so
    # multiple CDN hosts for the same quality are not collapsed prematurely.
    candidate_urls: tuple[dict[str, Any], ...] = field(default_factory=tuple)

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
            "category": self.category,
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
    capabilities: Any

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

    @property
    def capabilities(self) -> Any:
        """Return declarative capabilities without creating adapter state."""
        from .capabilities import get_platform_capabilities

        return get_platform_capabilities(self.platform)

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
            error=redact_text(error),
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
        category: str = "",
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
            category=category,
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
            return path_regex is None or re.search(path_regex, parsed.path) is not None
        except Exception:
            return False

    @abc.abstractmethod
    def parse(self, url: str) -> StreamInfo:
        """Parse a room URL or stream URL into StreamInfo."""

    def parse_with_context(self, url: str, context: object) -> StreamInfo:
        """Parse without the registry cache under a scoped request context.

        Platform adapters that need credentials can override this method and
        consume ``context.headers`` directly.  The default deliberately calls
        ``parse`` on the adapter instance instead of ``registry.parse_stream``:
        V2 requests must not reuse a URL-only legacy cache entry across
        accounts, proxies or credential generations.  The resolver merges the
        scoped headers into the normalized candidates for adapters that do not
        need custom parsing.
        """
        del context
        return self.parse(url)

    def can_handle(self, url: str) -> bool:
        """Default implementation: subclasses should override this.

        DirectAdapter overrides with direct stream URL detection; most
        platform adapters should use :meth:`_can_handle_by_hosts`.
        """
        return False
