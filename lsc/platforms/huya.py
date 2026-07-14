"""Adapter for public Huya live room URLs."""
from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

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

HUYA_HEADERS = {
    "Referer": "https://www.huya.com/",
    "User-Agent": DEFAULT_USER_AGENT,
}
_ROOM_PATH_RE = re.compile(r"^/[^/?#]+/?$")


class HuyaAdapter(BasePlatformAdapter):
    platform = "huya"
    display_name = "虎牙"

    def can_handle(self, url: str) -> bool:
        parsed = urlparse((url or "").strip())
        host = parsed.netloc.lower()
        return host in {"www.huya.com", "huya.com"} and bool(_ROOM_PATH_RE.fullmatch(parsed.path))

    def parse(self, url: str) -> StreamInfo:
        _log.info("Huya: parsing %s", url[:80])
        clean_url = (url or "").strip()
        try:
            html = self._fetch_page(clean_url)
            data = self._extract_global_init(html)
        except Exception as exc:
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
                _log.debug("操作异常（已忽略）: %s", exc)
        # 覆盖回 room_info/profile_info 以便后续使用
        if title:
            room_info["sIntroduction"] = title
        if streamer:
            profile_info["nick"] = streamer

        if int(room_info.get("tLiveStatus") or 0) != 1:
            return self._failed(clean_url, "虎牙直播间未开播", ERROR_OFFLINE, raw=data)

        quality_urls = self._extract_stream_urls(data)
        if not quality_urls:
            return self._failed(clean_url, "虎牙未找到公开流", ERROR_RESTRICTED, raw=data)

        return self._success(
            clean_url,
            stream_url=quality_urls.get("source", ""),
            title=str(room_info.get("sIntroduction") or ""),
            streamer=str(profile_info.get("nick") or ""),
            is_live=True,
            quality_urls=quality_urls,
            selected_quality="source",
            headers=dict(HUYA_HEADERS),
            category=category,
            raw={},  # discard large HTML/JSON payload on success to save memory
        )

    def _fetch_page(self, url: str) -> str:
        return fetch_url(url, headers=HUYA_HEADERS)

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

        The outer object is a JS literal, so we locate the ``stream:`` field
        and parse its JSON value. We then return a shape compatible with the
        rest of the adapter (keys: roomInfo, profileInfo, stream).
        """
        marker = "var hyPlayerConfig"
        marker_index = html.find(marker)
        if marker_index < 0:
            return None

        brace_index = html.find("{", marker_index)
        if brace_index < 0:
            return None

        # Locate the "stream:" field inside the JS object. The key is not
        # quoted in the JS literal, so we search for "stream:" directly and
        # make sure it sits inside the config block (before the next top-level
        # key would be enough for a quick check).
        stream_key = "stream:"
        stream_index = html.find(stream_key, brace_index)
        if stream_index < 0:
            return None

        colon_index = stream_index + len(stream_key) - 1
        if html[colon_index] != ":":
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
                room_info = {
                    "tLiveStatus": 1 if game_live_info.get("isSecret") == 0 else 0,
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

    def _extract_stream_urls(self, data: dict[str, Any]) -> dict[str, str]:
        """Build a map of candidate stream URLs from all available CDN lines.

        Huya usually exposes multiple CDN lines (e.g. al, tx, hs). Some lines
        may return 403 in the current network environment, so we keep all of
        them and let the probe step pick a reachable one.
        """
        stream = data.get("stream")
        stream = stream if isinstance(stream, dict) else {}
        quality_urls: dict[str, str] = {}
        cdn_names: list[str] = []
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
                stream_url = f"{flv_url.rstrip('/')}/{stream_name}.{suffix}"
                if anti_code:
                    stream_url = f"{stream_url}?{anti_code}"

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
                cdn_names.append(key)
        # Expose a generic "source" quality. Prefer a non-"al" CDN when
        # multiple lines are available, since the al line frequently rejects
        # anonymous/ffmpeg requests in certain networks. If only one line is
        # present we use it as source regardless of its name.
        if cdn_names and "source" not in quality_urls:
            preferred = next(
                (name for name in cdn_names if name != "al"),
                cdn_names[0],
            )
            quality_urls = {"source": quality_urls[preferred], **quality_urls}
        return quality_urls

    def _failed(self, url: str, error: str, code: str, raw: dict[str, Any] | None = None) -> StreamInfo:
        """Failed result always carries Huya request headers."""
        return super()._failed(url, error, code, headers=dict(HUYA_HEADERS), raw=raw)
