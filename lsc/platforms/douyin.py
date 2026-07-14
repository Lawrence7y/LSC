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
    DEFAULT_USER_AGENT,
    ERROR_OFFLINE,
    ERROR_PARSE_FAILED,
    ERROR_RESTRICTED,
    BasePlatformAdapter,
    StreamInfo,
    fetch_json,
    fetch_url,
)

_log = logging.getLogger(__name__)

_COOKIE_HELP = (
    "请在浏览器登录抖音后，用 Cookie 插件导出 JSON，"
    "到本应用「设置 → 抖音 Cookie」粘贴保存，"
    "或写入 ~/.lsc/cookies/douyin.json。"
)


def _is_douyin_verify_page(html: str) -> bool:
    """抖音反爬验证页（无登录 Cookie 时常见），不含直播 SSR 数据。"""
    if not html:
        return False
    sample = html[:8000]
    lowered = sample.lower()
    if "验证码中间页" in sample or "验证中间页" in sample:
        return True
    if "sec_sdk_build" in lowered and "captcha" in lowered:
        return True
    title_match = re.search(r"<title[^>]*>([^<]*)</title>", sample, re.IGNORECASE)
    if title_match and "验证" in title_match.group(1):
        return True
    return "captcha-verify" in lowered


def _cookie_required_error(*, reason: str = "") -> str:
    prefix = reason.strip() or "抖音反爬已拦截请求（返回验证页而非直播数据）"
    return f"{prefix}。Chrome 新版 Cookie 无法自动读取，{_COOKIE_HELP}"

DOUYIN_HEADERS = {
    "Referer": "https://live.douyin.com/",
    "User-Agent": DEFAULT_USER_AGENT,
}

# 用户主页URL模式: https://www.douyin.com/user/xxx
_USER_PROFILE_RE = re.compile(r"^/user/(?P<sec_uid>[^/?#]+)/?$")
# 直播间URL模式: https://live.douyin.com/xxx
_LIVE_ROOM_RE = re.compile(r"^/\d+/?$")
# 关注直播URL模式: https://www.douyin.com/follow/live/xxx
_FOLLOW_LIVE_RE = re.compile(r"^/follow/live/(?P<room_id>\d+)/?$")


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

        # 关注直播URL: www.douyin.com/follow/live/xxx
        return host in self._USER_HOSTS and bool(_FOLLOW_LIVE_RE.fullmatch(path))

    def parse(self, url: str) -> StreamInfo:
        clean_url = (url or "").strip()
        _log.info("Douyin: parsing %s", clean_url[:80])
        parsed = urlparse(clean_url)
        host = parsed.netloc.lower()
        path = parsed.path.rstrip("/")

        # 用户主页URL - 需要先获取直播间ID
        if host in ("www.douyin.com", "douyin.com"):
            match = _USER_PROFILE_RE.fullmatch(path)
            if match:
                sec_uid = match.group("sec_uid")
                return self._parse_user_profile(clean_url, sec_uid)

            # 关注直播URL - 提取房间ID
            match = _FOLLOW_LIVE_RE.fullmatch(path)
            if match:
                room_id = match.group("room_id")
                live_url = f"https://live.douyin.com/{room_id}"
                return self._parse_live_room(live_url)

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
        from urllib.error import HTTPError, URLError
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

        except HTTPError as exc:
            _log.warning("抖音API HTTP错误 %d: %s", exc.code, exc)
        except (URLError, Exception) as exc:
            _log.warning("抖音API获取直播间ID失败: %s", exc)

        # 备用方案: 尝试从用户页面HTML中提取
        try:
            return self._extract_room_from_page(sec_uid)
        except Exception as exc:
            _log.warning("抖音页面提取直播间ID失败: %s", exc)

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
            # 获取抖音登录态 Cookie，绕过反爬验证页面（验证中间页/CAPTCHA）
            cookies = self._get_douyin_cookies()
            if not cookies:
                _log.warning("Douyin parse aborted: no usable cookies url=%s", url[:80])
                return self._failed(
                    url,
                    _cookie_required_error(reason="未检测到有效的抖音登录 Cookie"),
                    ERROR_RESTRICTED,
                )
            module = self._load_script_module()
            html, fetch_err = module.fetch_page(url, cookies=cookies)
            if not html:
                if fetch_err:
                    msg = f"无法获取抖音直播间页面：{fetch_err}。请检查网络连接或稍后重试。"
                else:
                    msg = "无法获取抖音直播间页面。请检查网络连接或稍后重试。"
                _log.warning("Douyin fetch failed url=%s err=%s", url[:80], fetch_err)
                return self._failed(url, msg, ERROR_PARSE_FAILED)
            if _is_douyin_verify_page(html):
                _log.warning("Douyin verify/captcha page url=%s cookies=%d", url[:80], len(cookies))
                return self._failed(
                    url,
                    _cookie_required_error(reason="抖音返回了验证中间页，当前 Cookie 无效或已过期"),
                    ERROR_RESTRICTED,
                )
            data = module.extract_ssr_data(html) or {}
        except Exception as exc:
            return self._failed(url, f"抖音直播间解析失败: {exc}", ERROR_PARSE_FAILED)

        stream_url = str(data.get("streamUrl", "") or "")
        is_live_flag = bool(data.get("isLive"))
        if not is_live_flag or not stream_url:
            error_msg = str(data.get("error", "") or "")
            if not error_msg:
                if is_live_flag and not stream_url:
                    error_msg = "抖音直播间流地址已过期，请重新连接获取新地址。"
                else:
                    # 有 Cookie 且非验证页时，才判定为真正未开播
                    error_msg = "抖音直播间未开播。"
            return self._failed(
                url,
                error_msg,
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

        # Fallback: try to extract title / streamer from HTML <title> tag
        _INVALID = {"", "$undefined", "undefined", "null", "None", "false", "广告投放"}
        title = str(data.get("title", "") or "")
        streamer = str(data.get("streamerName", "") or "")
        room_id = str(data.get("roomId", "") or "")
        category = str(data.get("category", "") or "")

        if title in _INVALID:
            title = ""
        if streamer in _INVALID:
            streamer = ""
        if room_id in _INVALID:
            room_id = ""
        if category in _INVALID:
            category = ""

        if not title or not streamer:
            title_match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
            if title_match:
                page_title = title_match.group(1).strip()
                # 过滤无效标题
                if page_title in _INVALID or len(page_title) < 2:
                    page_title = ""
                if page_title:
                    # 移除常见后缀
                    for suffix in ["- 抖音直播", "_抖音直播", " - 抖音", "的抖音直播间", "的直播间"]:
                        if page_title.endswith(suffix):
                            page_title = page_title[: -len(suffix)].strip()
                    # 尝试分割主播名和标题
                    if not title and not streamer:
                        parts = re.split(r"\s*[-_|｜]\s*", page_title, maxsplit=1)
                        if len(parts) >= 2 and parts[0].strip() and parts[1].strip():
                            streamer = parts[0].strip()
                            title = parts[1].strip()
                        elif page_title:
                            title = page_title
                            streamer = "抖音主播"
                    elif not title:
                        title = page_title
                    elif not streamer:
                        parts = re.split(r"\s*[-_|｜]\s*", page_title, maxsplit=1)
                        if parts and parts[0].strip():
                            streamer = parts[0].strip()

        if not title:
            title = f"抖音直播 {room_id}" if room_id else "抖音直播"
        if not streamer:
            streamer = "抖音主播"

        return self._success(
            url,
            stream_url=stream_url,
            title=title,
            streamer=streamer,
            is_live=True,
            quality_urls=quality_urls,
            selected_quality=str(data.get("selectedQuality", "") or next(iter(quality_urls), "")),
            headers=dict(DOUYIN_HEADERS),
            raw={},  # discard extracted SSR payload on success to save memory
            category=category,
        )

    def _get_douyin_cookies(self) -> dict[str, str]:
        """获取抖音登录态 Cookie，用于绕过反爬验证页面。

        抖音会在请求无登录态时返回"验证中间页"（含 CAPTCHA JS），
        导致 SSR 数据解析失败、房间显示为"未开播"。
        """
        try:
            from .cookie_helper import get_douyin_cookies
            return get_douyin_cookies()
        except Exception as exc:
            _log.debug("获取抖音Cookie失败: %s", exc)
            return {}

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
