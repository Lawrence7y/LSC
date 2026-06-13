"""Adapter for Bilibili live room URLs."""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from .base import ERROR_OFFLINE, ERROR_PARSE_FAILED, ERROR_RESTRICTED, StreamInfo

ROOM_INIT_URL = "https://api.live.bilibili.com/room/v1/Room/room_init"
PLAY_INFO_URL = "https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo"
BILIBILI_HEADERS = {
    "Referer": "https://live.bilibili.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}
_ROOM_PATH_RE = re.compile(r"^/(?P<room_id>\d+)/?$")


class BilibiliAdapter:
    platform = "bilibili"

    def can_handle(self, url: str) -> bool:
        parsed = urlparse((url or "").strip())
        host = parsed.netloc.lower()
        return host == "live.bilibili.com" and bool(_ROOM_PATH_RE.fullmatch(parsed.path))

    def parse(self, url: str) -> StreamInfo:
        clean_url = (url or "").strip()
        room_id = self._extract_room_id(clean_url)
        if not room_id:
            return self._failed(clean_url, "无法识别 B 站直播间号", ERROR_PARSE_FAILED)

        room_init = self._fetch_json(ROOM_INIT_URL, params={"id": room_id})
        room_data = room_init.get("data")
        if room_init.get("code") != 0 or not isinstance(room_data, dict):
            return self._failed(clean_url, "B 站直播间初始化接口返回异常", ERROR_PARSE_FAILED)

        real_room_id = str(room_data.get("room_id") or room_id)
        title = str(room_data.get("title") or "")
        streamer = str(room_data.get("uname") or "")
        if int(room_data.get("live_status") or 0) != 1:
            return self._failed(
                clean_url,
                "B 站直播间未开播",
                ERROR_OFFLINE,
                raw={"room_init": room_init},
            )

        play_info = self._fetch_json(
            PLAY_INFO_URL,
            params={
                "room_id": real_room_id,
                "protocol": "0,1",
                "format": "0,1,2",
                "codec": "0,1",
                "qn": "10000",
                "platform": "web",
                "dolby": "5",
                "panorama": "1",
            },
        )
        play_data = play_info.get("data")
        if play_info.get("code") != 0 or not isinstance(play_data, dict):
            return self._failed(clean_url, "B 站播放信息接口返回异常", ERROR_PARSE_FAILED)

        stream_url, quality_urls = self._extract_play_urls(play_data)
        if not stream_url:
            return self._failed(
                clean_url,
                "B 站直播间暂无公开播放地址",
                ERROR_RESTRICTED,
                raw={"room_init": room_init, "play_info": play_info},
            )

        return StreamInfo(
            platform=self.platform,
            room_url=clean_url,
            stream_url=stream_url,
            title=title,
            streamer=streamer,
            is_live=True,
            quality_urls=quality_urls,
            selected_quality=next(iter(quality_urls), ""),
            headers=dict(BILIBILI_HEADERS),
            raw={"room_init": room_init, "play_info": play_info},
        )

    def _extract_room_id(self, url: str) -> str:
        match = _ROOM_PATH_RE.fullmatch(urlparse(url).path)
        if match is None:
            return ""
        return match.group("room_id")

    def _fetch_json(self, url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        query = urlencode(params or {})
        request_url = f"{url}?{query}" if query else url
        request = Request(request_url, headers=BILIBILI_HEADERS)
        with urlopen(request, timeout=10) as response:
            payload = response.read().decode("utf-8")
        data = json.loads(payload)
        return data if isinstance(data, dict) else {}

    def _extract_play_urls(self, payload: dict[str, Any]) -> tuple[str, dict[str, str]]:
        playurl_info = payload.get("playurl_info")
        if not isinstance(playurl_info, dict):
            return "", {}
        playurl = playurl_info.get("playurl")
        if not isinstance(playurl, dict):
            return "", {}

        quality_urls: dict[str, str] = {}
        for stream in playurl.get("stream") or []:
            if not isinstance(stream, dict):
                continue
            for fmt in stream.get("format") or []:
                if not isinstance(fmt, dict):
                    continue
                for codec in fmt.get("codec") or []:
                    if not isinstance(codec, dict):
                        continue
                    built_urls = self._build_quality_urls(codec)
                    for quality, quality_url in built_urls.items():
                        quality_urls.setdefault(quality, quality_url)

        stream_url = next(iter(quality_urls.values()), "")
        return stream_url, quality_urls

    def _build_quality_urls(self, codec: dict[str, Any]) -> dict[str, str]:
        base_url = str(codec.get("base_url") or "")
        url_info_list = codec.get("url_info") or []
        if not base_url or not isinstance(url_info_list, list):
            return {}

        accept_qn = codec.get("accept_qn") or []
        qualities = [str(qn) for qn in accept_qn if str(qn)]
        quality_urls: dict[str, str] = {}

        for url_info in url_info_list:
            if not isinstance(url_info, dict):
                continue
            host = str(url_info.get("host") or "")
            extra = str(url_info.get("extra") or "")
            if not host.startswith(("http://", "https://")):
                continue
            stream_url = f"{host}{base_url}{extra}"
            if not qualities:
                quality_urls.setdefault("origin", stream_url)
                continue
            for quality in qualities:
                quality_urls.setdefault(quality, self._replace_qn(stream_url, quality))
            if quality_urls:
                break

        return quality_urls

    def _replace_qn(self, stream_url: str, quality: str) -> str:
        parsed = urlparse(stream_url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["qn"] = quality
        return urlunparse(parsed._replace(query=urlencode(query)))

    def _failed(self, url: str, error: str, code: str, raw: dict[str, Any] | None = None) -> StreamInfo:
        return StreamInfo(
            platform=self.platform,
            room_url=url,
            is_live=False,
            headers=dict(BILIBILI_HEADERS),
            raw=raw or {},
            error=error,
            error_code=code,
        )
