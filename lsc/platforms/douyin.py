"""Adapter for Douyin live room URLs and user profile URLs."""
from __future__ import annotations

import importlib.util
import logging
import re
from pathlib import Path
from types import ModuleType
from typing import ClassVar
from urllib.parse import urlparse

from .base import (
    ERROR_OFFLINE,
    ERROR_PARSE_FAILED,
    BasePlatformAdapter,
    DEFAULT_USER_AGENT,
    StreamInfo,
    fetch_json,
    fetch_url,
)

_log = logging.getLogger(__name__)

DOUYIN_HEADERS = {
    "Referer": "https://live.douyin.com/",
    "User-Agent": DEFAULT_USER_AGENT,
}

# 用户主页URL模式: https://www.douyin.com/user/xxx
_USER_PROFILE_RE = re.compile(r"^/user/(?P<sec_uid>[^/?#]+)/?$")
# 直播间URL模式: https://live.douyin.com/xxx
_LIVE_ROOM_RE = re.compile(r"^/\d+/?$")


class DouyinAdapter(BasePlatformAdapter):
    platform = "douyin"
    display_name = "抖音"
    _cached_module: ClassVar[ModuleType | None] = None

    _LIVE_HOSTS = {"live.douyin.com"}
    _USER_HOSTS = {"www.douyin.com", "douyin.com"}

    def can_handle(self, url: str) -> bool:
        parsed = urlparse((url or "").strip())
        host = parsed.netloc.lower()
        path = parsed.path.rstrip("/")

        # 直播间URL: live.douyin.com/123456
        if host in self._LIVE_HOSTS and bool(_LIVE_ROOM_RE.fullmatch(path)):
            return True

        # 用户主页URL: www.douyin.com/user/xxx
        if host in self._USER_HOSTS and bool(_USER_PROFILE_RE.fullmatch(path)):
            return True

        return False

    def parse(self, url: str) -> StreamInfo:
        clean_url = (url or "").strip()
        parsed = urlparse(clean_url)
        host = parsed.netloc.lower()
        path = parsed.path.rstrip("/")
        
        # 用户主页URL - 需要先获取直播间ID
        if host in ("www.douyin.com", "douyin.com"):
            match = _USER_PROFILE_RE.fullmatch(path)
            if match:
                sec_uid = match.group("sec_uid")
                return self._parse_user_profile(clean_url, sec_uid)
        
        # 直播间URL - 直接解析
        return self._parse_live_room(clean_url)

    def _parse_user_profile(self, url: str, sec_uid: str) -> StreamInfo:
        """从用户主页获取直播信息。"""
        try:
            # 尝试通过API获取用户直播状态
            live_room_id = self._get_user_live_room(sec_uid)
            if not live_room_id:
                return self._failed(
                    url,
                    "该用户未在直播或无法获取直播间信息。",
                    ERROR_OFFLINE,
                )
            
            # 使用获取到的直播间ID构造直播URL并解析
            live_url = f"https://live.douyin.com/{live_room_id}"
            return self._parse_live_room(live_url)
            
        except Exception as exc:
            return self._failed(url, f"获取用户直播信息失败: {exc}", ERROR_PARSE_FAILED)

    def _get_user_live_room(self, sec_uid: str) -> str:
        """通过用户sec_uid获取直播间ID。"""
        try:
            # 使用抖音API获取用户信息
            api_url = (
                "https://live.douyin.com/webcast/room/web/enter/"
                f"?aid=6383&app_name=douyin_web&live_id=1&device_platform=web"
                f"&language=zh-CN&browser_language=zh-CN&browser_platform=Win32"
                f"&browser_name=Chrome&browser_version=120.0.0.0&sec_uid={sec_uid}"
            )

            data = fetch_json(api_url, headers=DOUYIN_HEADERS)

            # 从返回数据中提取直播间ID
            room_id = data.get("data", {}).get("room_id", "")
            if room_id:
                return str(room_id)

        except Exception as exc:
            _log.debug("抖音API获取直播间ID失败: %s", exc)
        
        # 备用方案: 尝试从用户页面HTML中提取
        try:
            return self._extract_room_from_page(sec_uid)
        except Exception as exc:
            _log.debug("抖音页面提取直播间ID失败: %s", exc)
        
        return ""

    def _extract_room_from_page(self, sec_uid: str) -> str:
        """从用户页面HTML中提取直播间ID。"""
        url = f"https://www.douyin.com/user/{sec_uid}"
        try:
            html = fetch_url(url, headers=DOUYIN_HEADERS)
            
            # 尝试从HTML中提取直播间ID
            # 常见模式: "room_id":"123456" 或 roomId:123456
            patterns = [
                r'"room_id"\s*:\s*"?(\d+)"?',
                r'"roomId"\s*:\s*"?(\d+)"?',
                r'room_id=(\d+)',
            ]
            
            for pattern in patterns:
                match = re.search(pattern, html)
                if match:
                    return match.group(1)
                    
        except Exception as exc:
            _log.debug("抖音页面提取房间号失败: %s", exc)
        
        return ""

    def _parse_live_room(self, url: str) -> StreamInfo:
        """解析直播间URL。"""
        try:
            module = self._load_script_module()
            html = module.fetch_page(url)
            if not html:
                return self._failed(url, "无法获取抖音直播间页面。", ERROR_PARSE_FAILED)
            data = module.extract_ssr_data(html) or {}
        except Exception as exc:
            return self._failed(url, f"抖音直播间解析失败: {exc}", ERROR_PARSE_FAILED)

        stream_url = str(data.get("streamUrl", "") or "")
        is_live = bool(data.get("isLive")) and bool(stream_url)
        if not is_live:
            return self._failed(
                url,
                str(data.get("error", "") or "抖音直播间未开播。"),
                ERROR_OFFLINE,
                raw=data if isinstance(data, dict) else {},
            )

        raw_quality_urls = data.get("qualityUrls") or {}
        quality_urls: dict[str, str] = {}
        if isinstance(raw_quality_urls, dict):
            quality_urls = {
                str(key): str(value)
                for key, value in raw_quality_urls.items()
                if isinstance(value, str) and value.startswith(("http://", "https://"))
            }
        if stream_url and not quality_urls:
            quality_urls = {"origin": stream_url}

        return self._success(
            url,
            stream_url=stream_url,
            title=str(data.get("title", "") or ""),
            streamer=str(data.get("streamerName", "") or ""),
            is_live=True,
            quality_urls=quality_urls,
            selected_quality=str(data.get("selectedQuality", "") or next(iter(quality_urls), "")),
            headers=dict(DOUYIN_HEADERS),
            raw={},  # discard extracted SSR payload on success to save memory
        )

    def _failed(self, url: str, error: str, code: str, raw: dict | None = None) -> StreamInfo:
        """Failed result always carries Douyin request headers."""
        return super()._failed(url, error, code, headers=dict(DOUYIN_HEADERS), raw=raw)

    def _load_script_module(self) -> ModuleType:
        if self._cached_module is not None:
            return self._cached_module

        script_path = Path(__file__).resolve().parents[2] / "scripts" / "douyin_record.py"
        if not script_path.exists():
            raise FileNotFoundError(f"missing script: {script_path}")

        spec = importlib.util.spec_from_file_location("douyin_record", str(script_path))
        if spec is None or spec.loader is None:
            raise ImportError(f"unable to load spec for {script_path}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self._cached_module = module
        return module
