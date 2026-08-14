"""Adapter for Douyu live room URLs."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlsplit, urlunsplit
from urllib.request import Request

_log = logging.getLogger(__name__)

from .base import (
    DEFAULT_USER_AGENT,
    ERROR_OFFLINE,
    ERROR_PARSE_FAILED,
    BasePlatformAdapter,
    StreamInfo,
    _opener_for_proxy,
    _validate_network_url,
    fetch_url,
)
from .redaction import redact_text, redact_url

DOUYU_HEADERS = {
    "Referer": "https://www.douyu.com/",
    "User-Agent": DEFAULT_USER_AGENT,
}
_ROOM_PATH_RE = re.compile(r"^/(\d+)/?$")
_TOPIC_PATH_RE = re.compile(r"^/topic/")
_DOUYU_HOSTS = {"www.douyu.com", "douyu.com", "m.douyu.com"}
_SHOW_STATUS_LIVE_RE = re.compile(
    r'\\?"(?:show_status|showStatus)\\?"\s*:\s*1\b',
    re.I,
)
_PREVIEW_DID = "10000000000000000000000000001501"


class DouyuAdapter(BasePlatformAdapter):
    platform = "douyu"
    display_name = "斗鱼"

    def can_handle(self, url: str) -> bool:
        _log.debug("Douyu: checking %s", redact_url(url)[:60])
        parsed = urlparse((url or "").strip())
        host = parsed.netloc.lower()
        if host not in _DOUYU_HOSTS:
            return False
        if _ROOM_PATH_RE.fullmatch(parsed.path):
            return True
        if _TOPIC_PATH_RE.match(parsed.path):
            rid = (parse_qs(parsed.query).get("rid") or [""])[0]
            return rid.isdigit()
        return False

    def parse(self, url: str) -> StreamInfo:
        return self._parse(url)

    def parse_with_context(self, url: str, context: object) -> StreamInfo:
        """Use the scoped request headers/proxy for page and API resolution."""
        headers = dict(DOUYU_HEADERS)
        headers.update(dict(getattr(context, "headers", {}) or {}))
        return self._parse(
            url,
            request_headers=headers,
            network_context=getattr(context, "network_context", {}) or {},
        )

    def _parse(
        self,
        url: str,
        *,
        request_headers: Mapping[str, str] | None = None,
        network_context: Mapping[str, object] | None = None,
    ) -> StreamInfo:
        _log.info("Douyu: parsing %s", redact_url(url)[:80])
        clean_url = (url or "").strip()
        headers = dict(request_headers or DOUYU_HEADERS)
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
        match = _ROOM_PATH_RE.fullmatch(urlparse(clean_url).path)
        room_id = match.group(1) if match else ""
        if not room_id:
            rid = (parse_qs(urlparse(clean_url).query).get("rid") or [""])[0]
            if rid.isdigit():
                room_id = rid
                clean_url = f"https://www.douyu.com/{room_id}"
        if not room_id:
            return self._failed(clean_url, "无法识别斗鱼房间号", ERROR_PARSE_FAILED)

        room_info = self._fetch_room_info(
            room_id,
            headers=headers,
            timeout=timeout,
            proxy_url=proxy_url,
        )
        html = ""
        title = ""
        streamer = ""
        category = ""
        real_rid = room_id
        live: bool | None = None
        if room_info is not None:
            real_rid = str(room_info.get("rid") or room_id)
            title = str(room_info.get("roomName") or "")
            streamer = str(room_info.get("nickname") or "")
            category = str(room_info.get("cate2Name") or "")
            if "isLive" in room_info:
                try:
                    live = int(room_info.get("isLive") or 0) == 1
                except (TypeError, ValueError):
                    live = False
                if not live:
                    return self._failed(
                        clean_url,
                        "斗鱼直播间未开播",
                        ERROR_OFFLINE,
                        raw={"room_id": real_rid},
                    )

        if live is None:
            try:
                html = fetch_url(
                    clean_url,
                    headers=headers,
                    timeout=timeout,
                    proxy_url=proxy_url,
                )
            except Exception as exc:
                return self._failed(
                    clean_url,
                    f"斗鱼页面加载失败: {redact_text(exc) or type(exc).__name__}",
                    ERROR_PARSE_FAILED,
                )
            if not self._html_is_live(html):
                if "<title>" not in html.lower():
                    return self._failed(clean_url, "斗鱼页面加载异常", ERROR_PARSE_FAILED)
                return self._failed(clean_url, "斗鱼直播间未开播", ERROR_OFFLINE)
            live = True
            title = title or self._extract_field(html, r'"room_name"\s*:\s*"([^"]*)"')
            streamer = streamer or self._extract_field(html, r'"nickname"\s*:\s*"([^"]*)"')
            category = category or self._extract_field(html, r'"game_name"\s*:\s*"([^"]*)"') or \
                self._extract_field(html, r'"cate_name"\s*:\s*"([^"]*)"') or ""

        stream_url = ""
        if html:
            stream_url = self._extract_stream_url(
                html,
                real_rid,
                headers=headers,
                timeout=timeout,
                proxy_url=proxy_url,
            )
        if not stream_url:
            stream_url = self._fetch_preview_stream(
                real_rid,
                headers=headers,
                timeout=timeout,
                proxy_url=proxy_url,
            )
        if not stream_url and not html:
            try:
                html = fetch_url(
                    clean_url,
                    headers=headers,
                    timeout=timeout,
                    proxy_url=proxy_url,
                )
            except Exception as exc:
                _log.debug("Douyu page fallback failed: %s", redact_text(exc) or type(exc).__name__)
                html = ""
            if html:
                stream_url = self._extract_stream_url(
                    html,
                    real_rid,
                    headers=headers,
                    timeout=timeout,
                    proxy_url=proxy_url,
                )
                if not title:
                    title = self._extract_field(html, r'"room_name"\s*:\s*"([^"]*)"')
                if not streamer:
                    streamer = self._extract_field(html, r'"nickname"\s*:\s*"([^"]*)"')

        if not stream_url:
            _log.warning("Douyu: live room %s has no playable preview/hls URL", real_rid)

        return self._success(
            clean_url,
            stream_url=stream_url,
            title=title or f"斗鱼直播间 {room_id}",
            streamer=streamer or "斗鱼主播",
            is_live=True,
            quality_urls={"source": stream_url} if stream_url else {},
            selected_quality="source" if stream_url else "",
            headers=headers,
            category=category,
            raw={
                "source_kind": "official",
                "confidence": 0.8 if stream_url else 0.5,
                "state_source": "room_api" if room_info is not None else "room_page",
                "room_id": real_rid,
            },
        )

    @staticmethod
    def _html_is_live(html: str) -> bool:
        """Current Douyu pages embed show_status inside escaped JSON strings."""
        if _SHOW_STATUS_LIVE_RE.search(html or ""):
            return True
        return '"status":1' in html or '"status": 1' in html

    def _fetch_room_info(
        self,
        room_id: str,
        *,
        headers: Mapping[str, str],
        timeout: int,
        proxy_url: str,
    ) -> dict[str, Any] | None:
        api_url = f"https://m.douyu.com/api/room/info?rid={room_id}"
        try:
            body = fetch_url(
                api_url,
                headers=dict(headers),
                timeout=timeout,
                proxy_url=proxy_url,
            )
            payload = json.loads(body)
        except Exception as exc:
            _log.debug("Douyu room info failed: %s", redact_text(exc) or type(exc).__name__)
            return None
        if not isinstance(payload, dict):
            return None
        try:
            code = int(payload.get("code") or 0)
        except (TypeError, ValueError):
            return None
        if code != 0:
            return None
        data = payload.get("data")
        room = data.get("roomInfo") if isinstance(data, dict) else None
        return room if isinstance(room, dict) else None

    def _fetch_preview_stream(
        self,
        room_id: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: int = 12,
        proxy_url: str = "",
    ) -> str:
        rid = str(room_id or "").strip()
        if not rid.isdigit():
            return ""
        t13 = str(int(time.time() * 1000))
        auth = hashlib.md5(f"{rid}{t13}".encode()).hexdigest()
        url = f"https://playweb.douyucdn.cn/lapi/live/hlsH5Preview/{rid}"
        req_headers = dict(headers or DOUYU_HEADERS)
        req_headers.update({
            "rid": rid,
            "time": t13,
            "auth": auth,
            "Content-Type": "application/x-www-form-urlencoded",
        })
        try:
            payload = self._post_form(
                url,
                {"rid": rid, "did": _PREVIEW_DID},
                headers=req_headers,
                timeout=timeout,
                proxy_url=proxy_url,
            )
        except Exception as exc:
            _log.warning(
                "Douyu preview failed rid=%s: %s",
                rid,
                redact_text(exc) or type(exc).__name__,
            )
            return ""
        if not isinstance(payload, dict):
            return ""
        try:
            error = int(payload.get("error") if payload.get("error") is not None else 0)
        except (TypeError, ValueError):
            error = -1
        if error != 0:
            _log.info("Douyu preview rid=%s error=%s", rid, error)
            return ""
        data = payload.get("data")
        if not isinstance(data, dict):
            return ""
        base = str(data.get("rtmp_url") or "").rstrip("/")
        live_path = str(data.get("rtmp_live") or "").lstrip("/")
        if not base.startswith(("http://", "https://")) or not live_path:
            return ""
        return self._prefer_flv_play_url(f"{base}/{live_path}")

    @staticmethod
    def _prefer_flv_play_url(url: str) -> str:
        """Douyu preview HLS hangs FFmpeg without producing data; same signed FLV starts immediately."""
        parts = urlsplit(url)
        if not parts.path.endswith(".m3u8"):
            return url
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path[:-5] + ".flv", parts.query, "")
        )

    def _post_form(
        self,
        url: str,
        data: Mapping[str, str],
        *,
        headers: Mapping[str, str],
        timeout: int,
        proxy_url: str,
    ) -> dict[str, Any]:
        _validate_network_url(url)
        body = urlencode(dict(data)).encode("utf-8")
        request = Request(url, data=body, headers=dict(headers))
        with _opener_for_proxy(proxy_url).open(request, timeout=timeout) as response:
            final_url = response.geturl() or url
            _validate_network_url(final_url)
            raw = response.read().decode("utf-8", errors="replace")
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else {}

    def _extract_stream_url(
        self,
        html: str,
        room_id: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: int = 12,
        proxy_url: str = "",
    ) -> str:
        _log.debug("Douyu: extracting stream for room %s", room_id)
        """Extract stream URL from Douyu page. Tries multiple methods."""
        # Method 1: Look for hls_url in page data
        hls_match = re.search(r'"hls_url"\s*:\s*"(https?://[^"]*\.m3u8[^"]*)"', html)
        if hls_match:
            url = hls_match.group(1).replace("\\u002F", "/")
            if url.startswith("http"):
                return url

        # Method 2: Look for rtmp/rtmp_url
        rtmp_match = re.search(r'"rtmp_url"\s*:\s*"(rtmp://[^"]*)"', html)
        if rtmp_match:
            return rtmp_match.group(1).replace("\\u002F", "/")

        # Method 3: Look for any .flv URL
        flv_match = re.search(r'(https?://[^"\']*\.flv[^"\'\s]*)', html)
        if flv_match:
            return flv_match.group(1).replace("\\u002F", "/")

        # Method 4: Try the Douyu API endpoint
        try:
            api_url = f"https://m.douyu.com/html5/live?roomId={room_id}"
            api_html = fetch_url(
                api_url,
                headers=dict(headers or DOUYU_HEADERS),
                timeout=timeout,
                proxy_url=proxy_url,
            )
            data = json.loads(api_html)
            hls_raw = data.get("data", {}).get("hls_url", "")
            hls: str = str(hls_raw) if hls_raw else ""
            if hls:
                return hls
        except Exception as exc:
            _log.debug("操作异常（已忽略）: %s", redact_text(exc))

        return ""

    @staticmethod
    def _extract_field(html: str, pattern: str) -> str:
        match = re.search(pattern, html)
        return str(match.group(1)) if match else ""

    def _failed(
        self,
        url: str,
        error: str,
        error_code: str = "parse_failed",
        *,
        headers: dict[str, str] | None = None,
        raw: dict[str, Any] | None = None,
    ) -> StreamInfo:
        return super()._failed(url, error, error_code, headers=dict(DOUYU_HEADERS), raw=raw or {})
