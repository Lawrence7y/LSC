"""Adapter for Douyin live room URLs."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import re
from types import ModuleType
from urllib.parse import urlparse

from .base import ERROR_OFFLINE, ERROR_PARSE_FAILED, StreamInfo

DOUYIN_HEADERS = {
    "Referer": "https://live.douyin.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


class DouyinAdapter:
    platform = "douyin"

    def can_handle(self, url: str) -> bool:
        parsed = urlparse((url or "").strip())
        host = parsed.netloc.lower()
        path = parsed.path.rstrip("/")
        return host == "live.douyin.com" and bool(re.fullmatch(r"/\d+", path))

    def parse(self, url: str) -> StreamInfo:
        clean_url = (url or "").strip()
        try:
            module = self._load_script_module()
            html = module.fetch_page(clean_url)
            if not html:
                return self._failed(clean_url, "无法获取抖音直播间页面", ERROR_PARSE_FAILED)
            data = module.extract_ssr_data(html) or {}
        except Exception as exc:
            return self._failed(clean_url, f"抖音直播间解析失败: {exc}", ERROR_PARSE_FAILED)

        stream_url = str(data.get("streamUrl", "") or "")
        is_live = bool(data.get("isLive")) and bool(stream_url)
        if not is_live:
            return self._failed(
                clean_url,
                str(data.get("error", "") or "抖音直播间未开播"),
                ERROR_OFFLINE,
                raw=data if isinstance(data, dict) else {},
            )

        raw_quality_urls = data.get("qualityUrls") or {}
        quality_urls = {}
        if isinstance(raw_quality_urls, dict):
            quality_urls = {
                str(key): str(value)
                for key, value in raw_quality_urls.items()
                if isinstance(value, str) and value.startswith(("http://", "https://"))
            }
        if stream_url and not quality_urls:
            quality_urls = {"origin": stream_url}

        return StreamInfo(
            platform=self.platform,
            room_url=clean_url,
            stream_url=stream_url,
            title=str(data.get("title", "") or ""),
            streamer=str(data.get("streamerName", "") or ""),
            is_live=True,
            quality_urls=quality_urls,
            selected_quality=str(data.get("selectedQuality", "") or next(iter(quality_urls), "")),
            headers=dict(DOUYIN_HEADERS),
            raw=data if isinstance(data, dict) else {},
        )

    def _load_script_module(self) -> ModuleType:
        script_path = Path(__file__).resolve().parents[2] / "scripts" / "douyin_record.py"
        if not script_path.exists():
            raise FileNotFoundError(f"missing script: {script_path}")

        spec = importlib.util.spec_from_file_location("douyin_record", str(script_path))
        if spec is None or spec.loader is None:
            raise ImportError(f"unable to load spec for {script_path}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _failed(self, url: str, error: str, code: str, raw: dict | None = None) -> StreamInfo:
        return StreamInfo(
            platform=self.platform,
            room_url=url,
            is_live=False,
            headers=dict(DOUYIN_HEADERS),
            raw=raw or {},
            error=error,
            error_code=code,
        )
