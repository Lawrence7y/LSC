"""Adapter for public Huya live room URLs."""
from __future__ import annotations

import base64
import hashlib
import html
import json
import logging
import random
import re
import threading
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlsplit, urlunsplit

_log = logging.getLogger(__name__)

from .base import (
    DEFAULT_USER_AGENT,
    ERROR_OFFLINE,
    ERROR_PARSE_FAILED,
    ERROR_RESTRICTED,
    BasePlatformAdapter,
    StreamInfo,
    extract_json_after_marker,
    fetch_url,
)
from .redaction import redact_text, redact_url

# CDN FLV rejects requests that carry Origin (HTTP 403, Content-Type still
# video/x-flv). Keep Origin only on the HTML page fetch.
HUYA_HEADERS = {
    "Referer": "https://www.huya.com/",
    "User-Agent": DEFAULT_USER_AGENT,
}
HUYA_PAGE_HEADERS = {
    **HUYA_HEADERS,
    "Origin": "https://www.huya.com",
}


def _get_huya_cookies() -> dict[str, str]:
    """兼容路径：从本地 cookie 文件/环境变量读取虎牙登录态。"""
    try:
        from .cookie_helper import get_huya_cookies

        return get_huya_cookies()
    except Exception as exc:
        _log.debug("获取虎牙cookies失败: %s", redact_text(exc) or type(exc).__name__)
        return {}


def _build_headers_with_cookies() -> dict[str, str]:
    headers = dict(HUYA_PAGE_HEADERS)
    cookies = _get_huya_cookies()
    if cookies:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    return headers


def _cdn_request_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    """Drop Origin and Cookie from stream/CDN request headers.

    Huya's FLV CDN returns 403 when Origin is present, even with a valid
    Referer, User-Agent, and wsSecret. Page fetches may still send Origin
    and optional login Cookie; the signed media URL does not need them.
    """
    out = {
        key: value
        for key, value in dict(headers or HUYA_HEADERS).items()
        if key.lower() not in {"origin", "cookie"}
    }
    return out


_HUYA_SDK_CONSTANTS = {
    "t": 100,
    "ver": 1,
    "sv": 2401090219,
    "codec": 264,
}


def build_huya_live_query(anti_code: str, stream_name: str, *, ratio: int = 0) -> str:
    """Rebuild the FLV query as a mobile/SDK live pull.

    The page ``sFlvAntiCode`` ``wsSecret`` is a web-player token. Opening it
    yields a finite GOP dump (often via ``302_type=extreme_cold_aggr``) and
    then EOF. Streamlink/yt-dlp derive a new secret from ``fm`` + ``wsTime``
    + ``seqid``/``u``; that socket stays open on working CDN lines.
    Incomplete anti-codes fall back to the original query unchanged.
    """
    raw = html.unescape(str(anti_code or "").strip())
    name = str(stream_name or "").strip()
    if not raw or not name:
        return raw
    params = dict(parse_qsl(raw, keep_blank_values=True))
    fm = str(params.get("fm") or "")
    ws_time = str(params.get("wsTime") or "")
    ctype = str(params.get("ctype") or "huya_live")
    fs = str(params.get("fs") or "")
    if not fm or not ws_time:
        return raw
    try:
        prefix = base64.b64decode(unquote(fm).encode()).decode().split("_")[0]
    except Exception:
        return raw
    if not prefix:
        return raw
    uid = random.randint(12340000, 12349999)
    convert_uid = (uid << 8 | uid >> (32 - 8)) & 0xFFFFFFFF
    timestamp = int(time.time() * 1000)
    seqid = uid + timestamp
    t_value = int(_HUYA_SDK_CONSTANTS["t"])
    secret_hash = hashlib.md5(f"{seqid}|{ctype}|{t_value}".encode()).hexdigest()
    ws_secret = hashlib.md5(
        f"{prefix}_{convert_uid}_{name}_{secret_hash}_{ws_time}".encode()
    ).hexdigest()
    query = {
        "wsSecret": ws_secret,
        "wsTime": ws_time,
        "ctype": ctype,
        "fs": fs,
        "seqid": seqid,
        "u": convert_uid,
        "sdk_sid": timestamp,
        "ratio": int(ratio or 0),
        **_HUYA_SDK_CONSTANTS,
    }
    return urlencode(query)


_ROOM_PATH_RE = re.compile(r"^/[^/?#]+/?$")
_PROFILE_ROOM_RE = re.compile(r'"profileRoom"\s*:\s*"?(\d+)"?')
_CANONICAL_ROOM_RE = re.compile(
    r'rel=["\']canonical["\'][^>]+href=["\']https?://(?:www\.)?huya\.com/(\d+)',
    re.I,
)

# Scoped CDN quarantine: (room key, network profile, CDN name) → monotonic
# timestamp.  A 403 in one room/network must never poison another room.
_CDN_BLACKLIST_TTL_SEC = 300.0  # 5 min: CDN blocks are often temporary
_cdn_blacklist: dict[tuple[str, str, str], float] = {}
_cdn_blacklist_lock = threading.RLock()


def _numeric_room_id_from_match_page(html: str) -> str:
    """Read the canonical numeric room from a Huya match/event alias page."""
    text = str(html or "")
    match = _PROFILE_ROOM_RE.search(text)
    if match:
        return match.group(1)
    match = _CANONICAL_ROOM_RE.search(text)
    return match.group(1) if match else ""


def _scope_key(room_key: str = "", network_profile: str = "") -> tuple[str, str]:
    raw_room = str(room_key or "").strip()
    try:
        parsed = urlsplit(raw_room)
        if parsed.scheme and parsed.netloc:
            raw_room = urlunsplit(
                (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", "")
            )
    except Exception:
        pass
    return (
        raw_room.rstrip("/").lower() or "*",
        str(network_profile or "").strip() or "default",
    )


def mark_cdn_bad(
    cdn_name: str,
    *,
    room_key: str = "",
    network_profile: str = "",
) -> None:
    """Mark a CDN line as bad so the adapter skips it on the next parse.

    Called by the orchestrator when a 403 or connection failure is detected
    on a specific CDN line. The blacklist entry expires after
    ``_CDN_BLACKLIST_TTL_SEC`` seconds.
    """
    import time as _time
    if cdn_name:
        room_scope, network_scope = _scope_key(room_key, network_profile)
        with _cdn_blacklist_lock:
            _cdn_blacklist[(room_scope, network_scope, cdn_name)] = _time.monotonic()
        _log.info(
            "Huya CDN line '%s' quarantined scope=%s/%s for %.0fs",
            cdn_name,
            redact_url(room_scope),
            redact_text(network_scope),
            _CDN_BLACKLIST_TTL_SEC,
        )


def clear_cdn_blacklist(
    *,
    room_key: str = "",
    network_profile: str = "",
) -> None:
    """Clear a room/network quarantine, or all entries for compatibility."""
    if not room_key and not network_profile:
        with _cdn_blacklist_lock:
            _cdn_blacklist.clear()
        return
    room_scope, network_scope = _scope_key(room_key, network_profile)
    with _cdn_blacklist_lock:
        for key in list(_cdn_blacklist):
            if key[:2] == (room_scope, network_scope):
                _cdn_blacklist.pop(key, None)


def _is_cdn_blacklisted(
    cdn_name: str,
    *,
    room_key: str = "",
    network_profile: str = "",
) -> bool:
    """Check if a CDN line is currently blacklisted (and prune expired entries)."""
    import time as _time
    room_scope, network_scope = _scope_key(room_key, network_profile)
    key = (room_scope, network_scope, cdn_name)
    with _cdn_blacklist_lock:
        ts = _cdn_blacklist.get(key)
        if ts is None:
            return False
        if _time.monotonic() - ts > _CDN_BLACKLIST_TTL_SEC:
            _cdn_blacklist.pop(key, None)
            return False
        return True


class HuyaAdapter(BasePlatformAdapter):
    platform = "huya"
    display_name = "虎牙"

    def can_handle(self, url: str) -> bool:
        parsed = urlparse((url or "").strip())
        host = parsed.netloc.lower()
        return host in {"www.huya.com", "huya.com", "m.huya.com"} and bool(_ROOM_PATH_RE.fullmatch(parsed.path))

    def parse(self, url: str) -> StreamInfo:
        return self._parse(url)

    def parse_with_context(self, url: str, context: object) -> StreamInfo:
        """Carry the scoped headers, proxy, timeout and network profile."""
        headers = dict(HUYA_PAGE_HEADERS)
        headers.update(dict(getattr(context, "headers", {}) or {}))
        return self._parse(
            url,
            network_profile=str(getattr(context, "network_profile", "") or ""),
            request_headers=headers,
            network_context=getattr(context, "network_context", {}) or {},
        )

    def _parse(
        self,
        url: str,
        *,
        network_profile: str = "",
        request_headers: Mapping[str, str] | None = None,
        network_context: Mapping[str, object] | None = None,
        _follow_depth: int = 0,
    ) -> StreamInfo:
        _log.info("Huya: parsing %s", redact_url(url)[:80])
        clean_url = (url or "").strip()
        headers = dict(request_headers or _build_headers_with_cookies())
        html = ""
        try:
            html = self._fetch_page(
                clean_url,
                headers=headers,
                network_context=network_context,
            )
            data = self._extract_global_init(html)
        except Exception as exc:
            followed = self._follow_match_room(
                clean_url,
                html,
                network_profile=network_profile,
                request_headers=request_headers,
                network_context=network_context,
                follow_depth=_follow_depth,
            )
            if followed is not None:
                return followed
            return self._failed(clean_url, f"虎牙直播间解析失败: {exc}", ERROR_PARSE_FAILED)

        room_info = data.get("roomInfo")
        room_info = room_info if isinstance(room_info, dict) else {}
        profile_info = data.get("profileInfo")
        profile_info = profile_info if isinstance(profile_info, dict) else {}

        # 多级回退：当 roomInfo/profileInfo 字段缺失时从 HTML <title> 和 stream 数据补全
        title = str(room_info.get("sIntroduction") or "")
        streamer = str(profile_info.get("nick") or "")
        category = ""
        # 尝试从 stream 数据的 gameLiveInfo 补全
        stream_data = data.get("stream")
        stream_data = stream_data if isinstance(stream_data, dict) else {}
        for item in stream_data.get("data") or []:
            if not isinstance(item, dict):
                continue
            gli = item.get("gameLiveInfo") or {}
            if not isinstance(gli, dict):
                continue
            if not title:
                title = str(gli.get("roomName") or gli.get("sRoomName") or gli.get("introduction") or "")
            if not streamer:
                streamer = str(gli.get("nick") or gli.get("sNick") or gli.get("ownerNick") or "")
            category = str(gli.get("gameFullName") or gli.get("sGameFullName") or "")
            break
        if not title or not streamer:
            # 最终回退：从 HTML <title> 标签提取（虎牙格式通常为 "主播名 - 房间标题"）
            try:
                title_match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
                if title_match:
                    page_title = title_match.group(1).strip()
                    # 虎牙页面标题格式: "主播名 - 房间标题-虎牙直播" 或 "主播名 房间标题 虎牙直播"
                    if "-虎牙直播" in page_title:
                        page_title = page_title.replace("-虎牙直播", "").strip()
                    if " - " in page_title and not streamer:
                        parts = page_title.rsplit(" - ", 1)
                        if len(parts) == 2:
                            streamer = streamer or parts[0].strip()
                            title = title or parts[1].strip()
                    elif " " in page_title and not streamer:
                        parts = page_title.rsplit(" ", 1)
                        if len(parts) == 2:
                            streamer = streamer or parts[0].strip()
                            title = title or parts[1].strip()
                    else:
                        title = title or page_title
            except Exception as exc:
                _log.debug("操作异常（已忽略）: %s", redact_text(exc))

        # 开播状态检查：优先使用 room_info 中的 tLiveStatus；
        # 若 room_info 缺失该字段（如 hyPlayerConfig 路径推导的 room_info），
        # 则默认视为已开播（因为 stream 数据存在即表明有直播内容）
        live_status = room_info.get("tLiveStatus")
        if live_status is not None and int(live_status) != 1:
            return self._failed(clean_url, "虎牙直播间未开播", ERROR_OFFLINE, raw=data)

        quality_urls = self._extract_stream_urls(
            data,
            room_key=clean_url,
            network_profile=network_profile,
        )
        if not quality_urls:
            followed = self._follow_match_room(
                clean_url,
                html,
                network_profile=network_profile,
                request_headers=request_headers,
                network_context=network_context,
                follow_depth=_follow_depth,
            )
            if followed is not None:
                return followed
            return self._failed(clean_url, "虎牙未找到公开流", ERROR_RESTRICTED, raw=data)

        return self._success(
            clean_url,
            stream_url=quality_urls.get("source", ""),
            title=title or "虎牙直播",
            streamer=streamer or "虎牙主播",
            is_live=True,
            quality_urls=quality_urls,
            selected_quality="source",
            headers=_cdn_request_headers(headers),
            category=category,
            raw={
                "source_kind": "official",
                "confidence": 0.8,
                "state_source": "room_page",
            },
        )

    def _follow_match_room(
        self,
        current_url: str,
        html: str,
        *,
        network_profile: str,
        request_headers: Mapping[str, str] | None,
        network_context: Mapping[str, object] | None,
        follow_depth: int,
    ) -> StreamInfo | None:
        """Event aliases like /lpl often omit hyPlayerConfig; follow profileRoom."""
        if follow_depth >= 1 or not html:
            return None
        room_id = _numeric_room_id_from_match_page(html)
        if not room_id:
            return None
        current = (urlparse(current_url).path or "").strip("/")
        if current == room_id:
            return None
        target = f"https://www.huya.com/{room_id}"
        _log.info("Huya: following match alias to room %s", room_id)
        return self._parse(
            target,
            network_profile=network_profile,
            request_headers=request_headers,
            network_context=network_context,
            _follow_depth=follow_depth + 1,
        )

    def _fetch_page(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        network_context: Mapping[str, object] | None = None,
    ) -> str:
        context = dict(network_context or {})
        proxy_url = str(
            context.get("proxy_url")
            or context.get("http_proxy")
            or context.get("https_proxy")
            or ""
        ).strip()
        try:
            timeout = max(1, min(300, int(float(context.get("timeout_sec", 12)))))
        except (TypeError, ValueError):
            timeout = 12
        return fetch_url(
            url,
            headers=dict(headers or HUYA_PAGE_HEADERS),
            timeout=timeout,
            proxy_url=proxy_url,
        )

    def _extract_global_init(self, html: str) -> dict[str, Any]:
        """Extract the JSON initialization data from Huya page HTML.

        Tries multiple known markers in order, since Huya may change the
        variable name across page revisions. Falls back to a regex-based
        search if the primary markers are missing.
        """
        # hyPlayerConfig is the current (2025-2026) marker. Its outer object
        # is a JavaScript literal (unquoted keys), so we extract the nested
        # "stream" JSON field separately.
        data = self._try_extract_hyplayer_config(html)
        if data is not None:
            return data

        markers = [
            "window.HNF_GLOBAL_INIT",
            "window.__INITIAL_STATE__",
        ]
        for marker in markers:
            data = self._try_extract_after_marker(html, marker)
            if data is not None:
                return data

        # Last-resort: scan for any JSON object containing "roomInfo"
        data = self._regex_scan_for_room_info(html)
        if data is not None:
            return data

        raise ValueError(
            "虎牙页面结构已变更，未能定位初始化数据。"
            "请尝试使用直链地址或等待适配器更新。"
        )

    def _try_extract_hyplayer_config(self, html: str) -> dict[str, Any] | None:
        """Extract stream/room info from the modern ``var hyPlayerConfig`` block.

        The outer object is a JS literal. The ``stream`` key may be quoted
        (``"stream":``) or unquoted (``stream:``) depending on the page
        revision, so we try both forms. We then parse its JSON value and
        return a shape compatible with the rest of the adapter (keys:
        roomInfo, profileInfo, stream).
        """
        marker = "var hyPlayerConfig"
        marker_index = html.find(marker)
        if marker_index < 0:
            return None

        brace_index = html.find("{", marker_index)
        if brace_index < 0:
            return None

        # Locate the "stream" field inside the JS object. The key may be
        # quoted or unquoted, so we try both forms.
        colon_index = -1
        for stream_key in ('"stream":', 'stream:'):
            stream_index = html.find(stream_key, brace_index)
            if stream_index < 0:
                continue
            candidate = stream_index + len(stream_key) - 1
            if html[candidate] == ":":
                colon_index = candidate
                break
        if colon_index < 0:
            return None

        decoder = json.JSONDecoder()
        try:
            stream_data, _ = decoder.raw_decode(html[colon_index + 1:].lstrip())
        except (json.JSONDecodeError, ValueError):
            return None

        # roomInfo / profileInfo are not nested in hyPlayerConfig. We fetch
        # them from the sibling ``window.HNF_GLOBAL_INIT`` if still present,
        # otherwise build minimal compatible dicts from gameLiveInfo.
        base_data = self._try_extract_after_marker(html, "window.HNF_GLOBAL_INIT") or {}
        room_info = base_data.get("roomInfo") if isinstance(base_data, dict) else None
        profile_info = base_data.get("profileInfo") if isinstance(base_data, dict) else None

        if room_info is None or profile_info is None:
            # Derive from gameLiveInfo inside the stream payload.
            game_live_info: dict[str, Any] = {}
            if (
                isinstance(stream_data, dict)
                and isinstance(stream_data.get("data"), list)
                and stream_data["data"]
                and isinstance(stream_data["data"][0], dict)
            ):
                game_live_info = stream_data["data"][0].get("gameLiveInfo") or {}

            if room_info is None:
                # 注意：isSecret 字段缺失时不能判定为未开播。
                # 虎牙 stream 数据存在即表明有直播内容，默认视为已开播。
                room_info = {
                    "tLiveStatus": 1,
                    "sIntroduction": game_live_info.get("roomName", ""),
                }
            if profile_info is None:
                profile_info = {
                    "nick": game_live_info.get("nick", ""),
                }

        return {
            "roomInfo": room_info,
            "profileInfo": profile_info,
            "stream": stream_data,
        }

    def _try_extract_after_marker(self, html: str, marker: str) -> dict[str, Any] | None:
        """Attempt to extract a JSON object following the given marker."""
        return extract_json_after_marker(html, marker)

    def _regex_scan_for_room_info(self, html: str) -> dict[str, Any] | None:
        """Scan for any JSON fragment containing a roomInfo key."""
        for match in re.finditer(r'"roomInfo"\s*:\s*\{', html):
            # Walk backwards to find the enclosing opening brace
            start = match.start()
            depth = 0
            for i in range(start, -1, -1):
                if html[i] == "}":
                    depth += 1
                elif html[i] == "{":
                    if depth == 0:
                        start = i
                        break
                    depth -= 1
            try:
                decoder = json.JSONDecoder()
                data, _ = decoder.raw_decode(html[start:])
                if isinstance(data, dict) and "roomInfo" in data:
                    return data
            except (json.JSONDecodeError, ValueError):
                continue
        return None

    def _extract_stream_urls(
        self,
        data: dict[str, Any],
        *,
        room_key: str = "",
        network_profile: str = "",
    ) -> dict[str, str]:
        """Build a map of candidate stream URLs from all available CDN lines.

        Huya usually exposes multiple CDN lines (e.g. al, tx, hs). Some lines
        may return 403 in the current network environment, so we keep all of
        them and let the probe step pick a reachable one.

        All URLs are upgraded to HTTPS — Huya CDN enforces stricter anti-bot
        rules on plain HTTP, and FFmpeg recording via HTTP is frequently
        rejected with 403 after 10-30 seconds.
        """
        stream = data.get("stream")
        stream = stream if isinstance(stream, dict) else {}
        quality_urls: dict[str, str] = {}
        cdn_entries: list[tuple[str, str]] = []
        for item in stream.get("data") or []:
            if not isinstance(item, dict):
                continue
            for stream_info in item.get("gameStreamInfoList") or []:
                if not isinstance(stream_info, dict):
                    continue
                flv_url = str(stream_info.get("sFlvUrl") or "")
                stream_name = str(stream_info.get("sStreamName") or "")
                suffix = str(stream_info.get("sFlvUrlSuffix") or "flv")
                anti_code = str(stream_info.get("sFlvAntiCode") or "")
                if not flv_url.startswith(("http://", "https://")) or not stream_name:
                    continue
                # Force HTTPS: Huya CDN blocks plain-HTTP FFmpeg connections
                # with 403 after a short period; HTTPS connections are more
                # stable and less likely to trigger anti-bot rules.
                if flv_url.startswith("http://"):
                    flv_url = "https://" + flv_url[len("http://"):]
                stream_url = f"{flv_url.rstrip('/')}/{stream_name}.{suffix}"
                if anti_code:
                    stream_url = f"{stream_url}?{build_huya_live_query(anti_code, stream_name)}"

                # Derive a short CDN name from the host, e.g. "al", "tx".
                host = urlparse(flv_url).netloc.lower()
                cdn = host.split(".")[0] if host else "cdn"
                # Avoid duplicate keys from repeated lines for the same CDN.
                key = cdn
                base_key = key
                idx = 1
                while key in quality_urls:
                    idx += 1
                    key = f"{base_key}_{idx}"
                quality_urls[key] = stream_url
                cdn_entries.append((key, cdn))
        # Expose a generic "source" quality. Prefer hs: tx often 302s to a
        # finite ``extreme_cold_aggr`` FLV even with a valid SDK secret, while
        # hs keeps the live socket. Fall back to other non-al lines, then al.
        # Skip CDN lines that have been blacklisted (403 errors) so the
        # reconnect cycle tries a different CDN instead of re-using the bad one.
        if cdn_entries and "source" not in quality_urls:
            def _usable(cdn_name: str) -> bool:
                return not _is_cdn_blacklisted(
                    cdn_name,
                    room_key=room_key,
                    network_profile=network_profile,
                )

            preferred_entry = next(
                (entry for entry in cdn_entries if entry[1] == "hs" and _usable(entry[1])),
                None,
            )
            if preferred_entry is None:
                preferred_entry = next(
                    (
                        entry
                        for entry in cdn_entries
                        if entry[1] != "al" and _usable(entry[1])
                    ),
                    None,
                )
            preferred = preferred_entry[0] if preferred_entry else None
            if preferred is None:
                # All non-al lines are blacklisted; try al, then any
                preferred_entry = next(
                    (
                        entry
                        for entry in cdn_entries
                        if not _is_cdn_blacklisted(
                            entry[1],
                            room_key=room_key,
                            network_profile=network_profile,
                        )
                    ),
                    cdn_entries[0],  # last resort: re-use first even if quarantined
                )
                preferred = preferred_entry[0]
            return {"source": quality_urls[preferred], **quality_urls}
        return quality_urls

    def _failed(
        self,
        url: str,
        error: str,
        error_code: str = "parse_failed",
        *,
        headers: dict[str, str] | None = None,
        raw: dict[str, Any] | None = None,
    ) -> StreamInfo:
        """Failed result always carries Huya request headers."""
        return super()._failed(
            url,
            error,
            error_code,
            headers=_cdn_request_headers(headers or HUYA_HEADERS),
            raw=raw,
        )
