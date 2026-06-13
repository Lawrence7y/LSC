# Multiplatform Stream Adapters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a first-phase platform adapter layer for Douyin, Bilibili, Huya, and direct m3u8/flv stream URLs while keeping the current single-room preview, recording, playback, and clipping flow working.

**Architecture:** Add `lsc/platforms/` as the only place that knows platform-specific URL detection, page/API parsing, request headers, and quality maps. Keep `RecordingController` as the bridge to the existing GUI by converting `StreamInfo` into the current dict shape first, then pass platform headers into the existing FFmpeg capture path.

**Tech Stack:** Python 3.10+, stdlib `dataclasses`, `urllib.request`, `json`, `re`, PySide6 `QThread`, existing FFmpeg/mpv capture and preview code, pytest.

---

## Reference Inputs

- Spec: `docs/superpowers/specs/2026-06-13-multiplatform-adapters-design.md`
- Current controller: `lsc/gui/pages/recording_controller.py`
- Current GUI connection path: `lsc/gui/pages/record.py`
- Current Douyin parser: `scripts/douyin_record.py`
- Current regression files: `tests/test_comprehensive_platform.py`, `tests/test_comprehensive_recording.py`, `tests/test_gui_page_audit.py`, `tests/test_gui_recording_preview.py`
- Public Bilibili docs checked before planning: `https://open-live.bilibili.com/document/`, `https://live.bilibili.com/p/html/bilibili-live-player/docs/web-player-biz.roomplayer.on.html`
- Huya public site checked before planning: `https://www.huya.com/`

## File Structure

- Create: `lsc/platforms/__init__.py`
  - Re-export the adapter API used by the rest of the app.
- Create: `lsc/platforms/base.py`
  - Owns `StreamInfo`, `PlatformAdapter`, error code constants, legacy dict conversion, and FFmpeg header argument conversion.
- Create: `lsc/platforms/direct.py`
  - Handles public m3u8/flv/http stream URLs that can be played directly by mpv/FFmpeg.
- Create: `lsc/platforms/douyin.py`
  - Wraps the existing `scripts/douyin_record.py` parser and normalizes its result to `StreamInfo`.
- Create: `lsc/platforms/bilibili.py`
  - Resolves public Bilibili live room URLs through public web endpoints and normalizes play URLs to `StreamInfo`.
- Create: `lsc/platforms/huya.py`
  - Parses public Huya live pages and normalizes stream URLs to `StreamInfo`.
- Create: `lsc/platforms/registry.py`
  - Provides adapter registration, URL detection, parsing dispatch, and quality selection.
- Create: `tests/test_platform_adapters.py`
  - Unit tests for `StreamInfo`, direct URLs, registry dispatch, quality selection, and platform adapter normalization.
- Modify: `tests/test_comprehensive_platform.py`
  - Keep existing Douyin coverage and add compatibility checks that `RecordingController.parse_douyin_url()` now delegates through the platform layer.
- Modify: `tests/test_comprehensive_recording.py`
  - Add regression coverage that platform headers are passed to FFmpeg input args.
- Modify: `lsc/gui/pages/recording_controller.py`
  - Replace Douyin-only parsing with registry parsing while retaining a compatibility method named `parse_douyin_url()`.
- Modify: `lsc/gui/pages/record.py`
  - Pass controller-held platform input args into `start_recording_with_crf()`.

---

### Task 1: Platform Base, Direct Adapter, and Registry Skeleton

**Files:**
- Create: `lsc/platforms/__init__.py`
- Create: `lsc/platforms/base.py`
- Create: `lsc/platforms/direct.py`
- Create: `lsc/platforms/registry.py`
- Create: `tests/test_platform_adapters.py`

- [ ] **Step 1: Write failing tests for the platform base and direct adapter**

Add this file:

```python
"""Tests for platform adapter primitives and direct stream URLs."""
from __future__ import annotations

from lsc.platforms.base import (
    ERROR_UNSUPPORTED_URL,
    StreamInfo,
    headers_to_ffmpeg_input_args,
)
from lsc.platforms.registry import detect_platform, parse_stream, select_quality


def test_stream_info_legacy_dict_contains_current_gui_keys():
    info = StreamInfo(
        platform="direct",
        room_url="https://example.com/live.m3u8",
        stream_url="https://cdn.example.com/live.m3u8",
        title="公开直链",
        streamer="直链",
        is_live=True,
        quality_urls={"origin": "https://cdn.example.com/live.m3u8"},
        selected_quality="origin",
        headers={"Referer": "https://example.com/"},
    )

    legacy = info.to_legacy_dict()

    assert legacy["platform"] == "direct"
    assert legacy["isLive"] is True
    assert legacy["streamUrl"] == "https://cdn.example.com/live.m3u8"
    assert legacy["streamerName"] == "直链"
    assert legacy["qualityUrls"] == {"origin": "https://cdn.example.com/live.m3u8"}
    assert legacy["_headers"] == {"Referer": "https://example.com/"}
    assert legacy["_inputArgs"] == [
        "-headers",
        "Referer: https://example.com/\r\n",
    ]


def test_headers_to_ffmpeg_input_args_returns_empty_for_no_headers():
    assert headers_to_ffmpeg_input_args({}) == []


def test_direct_m3u8_url_is_detected_and_parsed():
    url = "https://cdn.example.com/path/live.m3u8?token=abc"

    assert detect_platform(url) == "direct"

    info = parse_stream(url)

    assert info.platform == "direct"
    assert info.is_live is True
    assert info.stream_url == url
    assert info.quality_urls == {"origin": url}
    assert info.selected_quality == "origin"


def test_direct_flv_url_is_detected_and_parsed():
    url = "https://cdn.example.com/live/room.flv"

    info = parse_stream(url)

    assert info.platform == "direct"
    assert info.is_live is True
    assert info.stream_url == url


def test_unknown_url_returns_structured_error():
    info = parse_stream("https://example.com/not-a-live-room")

    assert info.platform == "unknown"
    assert info.is_live is False
    assert info.stream_url == ""
    assert info.error_code == ERROR_UNSUPPORTED_URL
    assert "不支持" in info.error


def test_select_quality_uses_quality_candidates_then_fallback():
    info = StreamInfo(
        platform="direct",
        room_url="https://example.com/live",
        stream_url="https://example.com/origin.m3u8",
        is_live=True,
        quality_urls={
            "origin": "https://example.com/origin.m3u8",
            "hd": "https://example.com/hd.m3u8",
            "sd": "https://example.com/sd.m3u8",
        },
        selected_quality="origin",
    )

    assert select_quality(info, "高清") == ("https://example.com/hd.m3u8", "hd")
    assert select_quality(info, "流畅") == ("https://example.com/sd.m3u8", "sd")
    assert select_quality(info, "原画") == ("https://example.com/origin.m3u8", "origin")
```

- [ ] **Step 2: Run the new test file and verify it fails**

Run:

```powershell
python -m pytest tests/test_platform_adapters.py -q --no-cov
```

Expected: FAIL during import with `ModuleNotFoundError: No module named 'lsc.platforms'`.

- [ ] **Step 3: Implement the platform base module**

Create `lsc/platforms/base.py`:

```python
"""Shared types for platform stream adapters."""
from __future__ import annotations

from collections.abc import Mapping, Protocol
from dataclasses import dataclass, field
from typing import Any

ERROR_UNSUPPORTED_URL = "unsupported_url"
ERROR_OFFLINE = "offline"
ERROR_RESTRICTED = "restricted"
ERROR_PARSE_FAILED = "parse_failed"


def headers_to_ffmpeg_input_args(headers: Mapping[str, str]) -> list[str]:
    """Convert HTTP headers to FFmpeg input args."""
    clean_headers = {
        str(key).strip(): str(value).strip()
        for key, value in headers.items()
        if str(key).strip() and str(value).strip()
    }
    if not clean_headers:
        return []

    header_blob = "".join(f"{key}: {value}\r\n" for key, value in clean_headers.items())
    return ["-headers", header_blob]


@dataclass(slots=True)
class StreamInfo:
    """Normalized live stream metadata returned by every platform adapter."""

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
        """Return the dict shape currently consumed by RecordPage."""
        return {
            "platform": self.platform,
            "roomUrl": self.room_url,
            "isLive": self.is_live,
            "title": self.title,
            "streamerName": self.streamer,
            "streamUrl": self.stream_url,
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
    """Interface implemented by concrete platform adapters."""

    platform: str

    def can_handle(self, url: str) -> bool:
        """Return whether this adapter owns the URL."""

    def parse(self, url: str) -> StreamInfo:
        """Parse a room URL or stream URL into StreamInfo."""
```

- [ ] **Step 4: Implement the direct adapter**

Create `lsc/platforms/direct.py`:

```python
"""Adapter for direct m3u8/flv stream URLs."""
from __future__ import annotations

from urllib.parse import urlparse

from .base import StreamInfo


class DirectAdapter:
    """Accept direct public stream URLs playable by mpv and FFmpeg."""

    platform = "direct"

    def can_handle(self, url: str) -> bool:
        parsed = urlparse((url or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False

        lower_path = parsed.path.lower()
        lower_url = url.lower()
        return (
            lower_path.endswith(".m3u8")
            or lower_path.endswith(".flv")
            or ".m3u8?" in lower_url
            or ".flv?" in lower_url
        )

    def parse(self, url: str) -> StreamInfo:
        clean_url = (url or "").strip()
        return StreamInfo(
            platform=self.platform,
            room_url=clean_url,
            stream_url=clean_url,
            title="公开直播流",
            streamer="直链",
            is_live=True,
            quality_urls={"origin": clean_url},
            selected_quality="origin",
        )
```

- [ ] **Step 5: Implement registry skeleton with direct adapter**

Create `lsc/platforms/registry.py`:

```python
"""Platform adapter registry and quality selection."""
from __future__ import annotations

from collections.abc import Iterable

from .base import ERROR_UNSUPPORTED_URL, PlatformAdapter, StreamInfo
from .direct import DirectAdapter

QUALITY_PRESET_CANDIDATES = {
    "原画": ["origin", "source", "蓝光", "超清", "FULL_HD1", "uhd", "UHD1", "10000", "400", "hd", "HD1", "sd"],
    "高清": ["hd", "HD1", "250", "300", "uhd", "UHD1", "origin", "source", "sd", "SD1"],
    "流畅": ["sd", "SD1", "150", "80", "ld", "origin", "source", "hd"],
}


def _default_adapters() -> list[PlatformAdapter]:
    return [DirectAdapter()]


def get_adapters() -> list[PlatformAdapter]:
    """Return platform adapters in detection order."""
    return _default_adapters()


def detect_platform(url: str, adapters: Iterable[PlatformAdapter] | None = None) -> str:
    """Detect the platform name for a URL."""
    for adapter in adapters or get_adapters():
        if adapter.can_handle(url):
            return adapter.platform
    return "unknown"


def parse_stream(url: str, adapters: Iterable[PlatformAdapter] | None = None) -> StreamInfo:
    """Parse URL using the first matching adapter."""
    clean_url = (url or "").strip()
    for adapter in adapters or get_adapters():
        if adapter.can_handle(clean_url):
            return adapter.parse(clean_url)
    return StreamInfo(
        platform="unknown",
        room_url=clean_url,
        is_live=False,
        error="不支持的直播间链接或直播流地址",
        error_code=ERROR_UNSUPPORTED_URL,
    )


def select_quality(info: StreamInfo | dict, quality_preset: str) -> tuple[str, str]:
    """Pick the best matching stream URL for a UI quality preset."""
    if isinstance(info, StreamInfo):
        quality_urls = info.quality_urls
        stream_url = info.stream_url
        selected_quality = info.selected_quality
    else:
        quality_urls = info.get("qualityUrls") or {}
        stream_url = info.get("streamUrl", "")
        selected_quality = info.get("selectedQuality", "")

    for quality_key in QUALITY_PRESET_CANDIDATES.get(quality_preset, ()):
        url = quality_urls.get(quality_key, "")
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            return url, quality_key
    return stream_url, selected_quality
```

Create `lsc/platforms/__init__.py`:

```python
"""Live platform adapters."""
from .base import StreamInfo
from .registry import detect_platform, parse_stream, select_quality

__all__ = ["StreamInfo", "detect_platform", "parse_stream", "select_quality"]
```

- [ ] **Step 6: Run direct adapter tests and verify they pass**

Run:

```powershell
python -m pytest tests/test_platform_adapters.py -q --no-cov
```

Expected: PASS with all tests in `tests/test_platform_adapters.py` passing.

- [ ] **Step 7: Commit Task 1**

Run:

```powershell
git add lsc/platforms tests/test_platform_adapters.py
git commit -m "feat: add platform adapter base and direct streams"
```

Expected: commit succeeds and includes only the new platform package plus `tests/test_platform_adapters.py`.

---

### Task 2: Douyin Adapter Migration

**Files:**
- Create: `lsc/platforms/douyin.py`
- Modify: `lsc/platforms/registry.py`
- Modify: `tests/test_platform_adapters.py`
- Modify: `tests/test_comprehensive_platform.py`

- [ ] **Step 1: Add failing tests for Douyin adapter normalization and registry detection**

Append to `tests/test_platform_adapters.py`:

```python
def test_douyin_adapter_wraps_existing_parser(monkeypatch):
    from lsc.platforms.douyin import DouyinAdapter

    class FakeDouyinModule:
        @staticmethod
        def fetch_page(url):
            assert url == "https://live.douyin.com/123456"
            return "<html>fake</html>"

        @staticmethod
        def extract_ssr_data(html):
            assert html == "<html>fake</html>"
            return {
                "platform": "douyin",
                "isLive": True,
                "title": "无畏契约直播",
                "streamerName": "主播A",
                "streamUrl": "https://pull.example.com/live.m3u8",
                "selectedQuality": "origin",
                "qualityUrls": {"origin": "https://pull.example.com/live.m3u8"},
            }

    adapter = DouyinAdapter()
    monkeypatch.setattr(adapter, "_load_script_module", lambda: FakeDouyinModule)

    info = adapter.parse("https://live.douyin.com/123456")

    assert info.platform == "douyin"
    assert info.is_live is True
    assert info.title == "无畏契约直播"
    assert info.streamer == "主播A"
    assert info.stream_url == "https://pull.example.com/live.m3u8"
    assert info.headers["Referer"] == "https://live.douyin.com/"


def test_douyin_registry_detection():
    assert detect_platform("https://live.douyin.com/123456") == "douyin"
```

Append to `tests/test_comprehensive_platform.py`:

```python
class TestPlatformAdapterCompatibility:
    def test_parse_douyin_url_uses_platform_layer_legacy_shape(self, monkeypatch):
        from lsc.gui.pages.recording_controller import RecordingController
        from lsc.platforms.base import StreamInfo

        def fake_parse_stream(url):
            assert url == "https://live.douyin.com/123456"
            return StreamInfo(
                platform="douyin",
                room_url=url,
                stream_url="https://example.com/live.m3u8",
                title="直播标题",
                streamer="主播A",
                is_live=True,
                quality_urls={"origin": "https://example.com/live.m3u8"},
                selected_quality="origin",
                headers={"Referer": "https://live.douyin.com/"},
            )

        monkeypatch.setattr("lsc.gui.pages.recording_controller.parse_stream", fake_parse_stream)

        ctrl = RecordingController()
        result = ctrl.parse_douyin_url("https://live.douyin.com/123456")

        assert result["isLive"] is True
        assert result["platform"] == "douyin"
        assert result["streamUrl"] == "https://example.com/live.m3u8"
        assert result["streamerName"] == "主播A"
        assert result["_inputArgs"] == [
            "-headers",
            "Referer: https://live.douyin.com/\r\n",
        ]
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
python -m pytest tests/test_platform_adapters.py tests/test_comprehensive_platform.py::TestPlatformAdapterCompatibility -q --no-cov
```

Expected: FAIL because `lsc.platforms.douyin` does not exist and `recording_controller.parse_stream` is not imported.

- [ ] **Step 3: Implement Douyin adapter**

Create `lsc/platforms/douyin.py`:

```python
"""Adapter for public Douyin live rooms."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from urllib.parse import urlparse

from .base import ERROR_OFFLINE, ERROR_PARSE_FAILED, StreamInfo

DOUYIN_HEADERS = {
    "Referer": "https://live.douyin.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


class DouyinAdapter:
    """Wrap the existing Douyin page parser in the platform adapter interface."""

    platform = "douyin"

    def can_handle(self, url: str) -> bool:
        parsed = urlparse((url or "").strip())
        host = parsed.netloc.lower()
        return host.endswith("live.douyin.com") or host.endswith("douyin.com")

    def parse(self, url: str) -> StreamInfo:
        clean_url = (url or "").strip()
        try:
            module = self._load_script_module()
            html = module.fetch_page(clean_url)
            if not html:
                return StreamInfo(
                    platform=self.platform,
                    room_url=clean_url,
                    is_live=False,
                    headers=dict(DOUYIN_HEADERS),
                    error="无法获取抖音直播间页面",
                    error_code=ERROR_PARSE_FAILED,
                )
            data = module.extract_ssr_data(html)
        except Exception as exc:
            return StreamInfo(
                platform=self.platform,
                room_url=clean_url,
                is_live=False,
                headers=dict(DOUYIN_HEADERS),
                error=f"抖音直播间解析失败: {exc}",
                error_code=ERROR_PARSE_FAILED,
            )

        quality_urls = {
            str(key): value
            for key, value in (data.get("qualityUrls") or {}).items()
            if isinstance(value, str) and value.startswith(("http://", "https://"))
        }
        stream_url = data.get("streamUrl", "")
        is_live = bool(data.get("isLive") and stream_url)
        return StreamInfo(
            platform=self.platform,
            room_url=clean_url,
            stream_url=stream_url if isinstance(stream_url, str) else "",
            title=data.get("title", "") or "",
            streamer=data.get("streamerName", "") or "",
            is_live=is_live,
            quality_urls=quality_urls,
            selected_quality=data.get("selectedQuality", "") or "",
            headers=dict(DOUYIN_HEADERS),
            raw=data,
            error="" if is_live else data.get("error", "抖音直播间未开播或未找到公开直播流"),
            error_code="" if is_live else ERROR_OFFLINE,
        )

    def _load_script_module(self) -> ModuleType:
        script_path = Path(__file__).resolve().parents[2] / "scripts" / "douyin_record.py"
        spec = importlib.util.spec_from_file_location("douyin_record", str(script_path))
        if spec is None or spec.loader is None:
            raise RuntimeError("无法加载抖音解析脚本")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
```

- [ ] **Step 4: Register Douyin adapter**

Modify `lsc/platforms/registry.py`:

```python
from .douyin import DouyinAdapter
```

Replace `_default_adapters()` with:

```python
def _default_adapters() -> list[PlatformAdapter]:
    return [DirectAdapter(), DouyinAdapter()]
```

- [ ] **Step 5: Import platform parser in recording controller and keep compatibility method**

Modify imports in `lsc/gui/pages/recording_controller.py`:

```python
from lsc.platforms.registry import parse_stream, select_quality
```

Replace `parse_douyin_url()` with:

```python
    def parse_douyin_url(self, url: str) -> dict:
        """Compatibility wrapper for old tests and callers."""
        info = parse_stream(url)
        self.last_stream_info = info
        self.input_args = info.to_legacy_dict().get("_inputArgs", [])
        return info.to_legacy_dict()
```

Add these attributes in `RecordingController.__init__()` after `self.page_url`:

```python
        self.last_stream_info = None
        self.input_args: list[str] = []
```

- [ ] **Step 6: Run Douyin migration tests**

Run:

```powershell
python -m pytest tests/test_platform_adapters.py tests/test_comprehensive_platform.py::TestPlatformAdapterCompatibility -q --no-cov
```

Expected: PASS.

- [ ] **Step 7: Run existing platform tests**

Run:

```powershell
python -m pytest tests/test_comprehensive_platform.py -q --no-cov
```

Expected: PASS.

- [ ] **Step 8: Commit Task 2**

Run:

```powershell
git add lsc/platforms lsc/gui/pages/recording_controller.py tests/test_platform_adapters.py tests/test_comprehensive_platform.py
git commit -m "feat: migrate douyin parsing to platform adapters"
```

Expected: commit succeeds with Douyin adapter and compatibility coverage.

---

### Task 3: Bilibili Public Live Adapter

**Files:**
- Create: `lsc/platforms/bilibili.py`
- Modify: `lsc/platforms/registry.py`
- Modify: `tests/test_platform_adapters.py`

- [ ] **Step 1: Add failing Bilibili tests**

Append to `tests/test_platform_adapters.py`:

```python
def test_bilibili_adapter_parses_public_api_payload(monkeypatch):
    from lsc.platforms.bilibili import BilibiliAdapter

    adapter = BilibiliAdapter()

    def fake_fetch_json(url, params):
        if "room_init" in url:
            return {"code": 0, "data": {"room_id": 7654321, "live_status": 1}}
        if "getRoomPlayInfo" in url:
            return {
                "code": 0,
                "data": {
                    "room_info": {"title": "B站直播标题"},
                    "anchor_info": {"base_info": {"uname": "B站主播"}},
                    "playurl_info": {
                        "playurl": {
                            "stream": [
                                {
                                    "format": [
                                        {
                                            "codec": [
                                                {
                                                    "current_qn": 10000,
                                                    "base_url": "/live-bvc/room/index.m3u8",
                                                    "url_info": [
                                                        {
                                                            "host": "https://bili.example.com",
                                                            "extra": "?token=abc",
                                                        }
                                                    ],
                                                }
                                            ]
                                        }
                                    ]
                                }
                            ]
                        }
                    },
                },
            }
        raise AssertionError(url)

    monkeypatch.setattr(adapter, "_fetch_json", fake_fetch_json)

    info = adapter.parse("https://live.bilibili.com/12345")

    assert info.platform == "bilibili"
    assert info.is_live is True
    assert info.title == "B站直播标题"
    assert info.streamer == "B站主播"
    assert info.stream_url == "https://bili.example.com/live-bvc/room/index.m3u8?token=abc"
    assert info.quality_urls["10000"] == "https://bili.example.com/live-bvc/room/index.m3u8?token=abc"
    assert info.headers["Referer"] == "https://live.bilibili.com/"


def test_bilibili_offline_room_returns_offline_error(monkeypatch):
    from lsc.platforms.bilibili import BilibiliAdapter
    from lsc.platforms.base import ERROR_OFFLINE

    adapter = BilibiliAdapter()
    monkeypatch.setattr(
        adapter,
        "_fetch_json",
        lambda url, params: {"code": 0, "data": {"room_id": 7654321, "live_status": 0}},
    )

    info = adapter.parse("https://live.bilibili.com/12345")

    assert info.platform == "bilibili"
    assert info.is_live is False
    assert info.error_code == ERROR_OFFLINE
    assert "未开播" in info.error


def test_bilibili_registry_detection():
    assert detect_platform("https://live.bilibili.com/12345") == "bilibili"
```

- [ ] **Step 2: Run Bilibili tests and verify they fail**

Run:

```powershell
python -m pytest tests/test_platform_adapters.py -q --no-cov
```

Expected: FAIL because `lsc.platforms.bilibili` does not exist.

- [ ] **Step 3: Implement Bilibili adapter**

Create `lsc/platforms/bilibili.py`:

```python
"""Adapter for public Bilibili live rooms."""
from __future__ import annotations

import json
import re
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from .base import ERROR_OFFLINE, ERROR_PARSE_FAILED, ERROR_RESTRICTED, StreamInfo

BILIBILI_HEADERS = {
    "Referer": "https://live.bilibili.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


class BilibiliAdapter:
    """Resolve public Bilibili live rooms to stream URLs."""

    platform = "bilibili"

    def can_handle(self, url: str) -> bool:
        parsed = urlparse((url or "").strip())
        host = parsed.netloc.lower()
        return host.endswith("live.bilibili.com") or host.endswith("bilibili.com") or host.endswith("b23.tv")

    def parse(self, url: str) -> StreamInfo:
        clean_url = (url or "").strip()
        room_id = self._extract_room_id(clean_url)
        if not room_id:
            return StreamInfo(
                platform=self.platform,
                room_url=clean_url,
                is_live=False,
                headers=dict(BILIBILI_HEADERS),
                error="无法识别 B站直播间房间号",
                error_code=ERROR_PARSE_FAILED,
            )

        try:
            init_payload = self._fetch_json(
                "https://api.live.bilibili.com/room/v1/Room/room_init",
                {"id": room_id},
            )
        except Exception as exc:
            return self._failed(clean_url, f"B站房间信息获取失败: {exc}", ERROR_PARSE_FAILED)

        init_data = init_payload.get("data") or {}
        real_room_id = init_data.get("room_id") or room_id
        if init_payload.get("code") != 0:
            return self._failed(clean_url, "B站直播间接口返回失败", ERROR_PARSE_FAILED)
        if int(init_data.get("live_status") or 0) != 1:
            return self._failed(clean_url, "B站直播间未开播", ERROR_OFFLINE)

        try:
            play_payload = self._fetch_json(
                "https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo",
                {
                    "room_id": real_room_id,
                    "protocol": "0,1",
                    "format": "0,1,2",
                    "codec": "0,1",
                    "qn": "10000",
                    "platform": "web",
                    "ptype": "8",
                },
            )
        except Exception as exc:
            return self._failed(clean_url, f"B站播放地址获取失败: {exc}", ERROR_PARSE_FAILED)

        if play_payload.get("code") != 0:
            return self._failed(clean_url, "B站播放地址接口返回失败", ERROR_RESTRICTED)

        data = play_payload.get("data") or {}
        quality_urls = self._extract_quality_urls(data)
        stream_url = next(iter(quality_urls.values()), "")
        room_info = data.get("room_info") or {}
        anchor_info = data.get("anchor_info") or {}
        base_info = anchor_info.get("base_info") or {}

        if not stream_url:
            return self._failed(clean_url, "B站未返回公开播放地址", ERROR_RESTRICTED, raw=play_payload)

        return StreamInfo(
            platform=self.platform,
            room_url=clean_url,
            stream_url=stream_url,
            title=room_info.get("title", "") or "",
            streamer=base_info.get("uname", "") or "",
            is_live=True,
            quality_urls=quality_urls,
            selected_quality=next(iter(quality_urls.keys()), ""),
            headers=dict(BILIBILI_HEADERS),
            raw=play_payload,
        )

    def _extract_room_id(self, url: str) -> str:
        parsed = urlparse(url)
        match = re.search(r"/(\d+)", parsed.path)
        return match.group(1) if match else ""

    def _fetch_json(self, url: str, params: dict[str, object]) -> dict:
        query = urlencode(params)
        req = Request(f"{url}?{query}", headers=BILIBILI_HEADERS)
        with urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))

    def _extract_quality_urls(self, data: dict) -> dict[str, str]:
        quality_urls: dict[str, str] = {}
        playurl = ((data.get("playurl_info") or {}).get("playurl") or {})
        for stream in playurl.get("stream") or []:
            for fmt in stream.get("format") or []:
                for codec in fmt.get("codec") or []:
                    qn = str(codec.get("current_qn") or codec.get("accept_qn") or "source")
                    base_url = codec.get("base_url") or ""
                    for item in codec.get("url_info") or []:
                        host = item.get("host") or ""
                        extra = item.get("extra") or ""
                        full_url = f"{host}{base_url}{extra}"
                        if full_url.startswith(("http://", "https://")):
                            quality_urls.setdefault(qn, full_url)
        return quality_urls

    def _failed(self, url: str, error: str, code: str, raw: dict | None = None) -> StreamInfo:
        return StreamInfo(
            platform=self.platform,
            room_url=url,
            is_live=False,
            headers=dict(BILIBILI_HEADERS),
            raw=raw or {},
            error=error,
            error_code=code,
        )
```

- [ ] **Step 4: Register Bilibili adapter**

Modify `lsc/platforms/registry.py`:

```python
from .bilibili import BilibiliAdapter
```

Replace `_default_adapters()` with:

```python
def _default_adapters() -> list[PlatformAdapter]:
    return [DirectAdapter(), DouyinAdapter(), BilibiliAdapter()]
```

- [ ] **Step 5: Run Bilibili tests**

Run:

```powershell
python -m pytest tests/test_platform_adapters.py -q --no-cov
```

Expected: PASS.

- [ ] **Step 6: Commit Task 3**

Run:

```powershell
git add lsc/platforms tests/test_platform_adapters.py
git commit -m "feat: add bilibili live platform adapter"
```

Expected: commit succeeds with the Bilibili adapter and tests.

---

### Task 4: Huya Public Page Adapter

**Files:**
- Create: `lsc/platforms/huya.py`
- Modify: `lsc/platforms/registry.py`
- Modify: `tests/test_platform_adapters.py`

- [ ] **Step 1: Add failing Huya tests**

Append to `tests/test_platform_adapters.py`:

```python
def test_huya_adapter_parses_public_page_payload(monkeypatch):
    from lsc.platforms.huya import HuyaAdapter

    adapter = HuyaAdapter()
    html = """
    <html>
      <script>
        window.HNF_GLOBAL_INIT = {
          "roomInfo": {"tLiveStatus": 1, "sIntroduction": "虎牙直播标题"},
          "profileInfo": {"nick": "虎牙主播"},
          "stream": {
            "data": [{
              "gameStreamInfoList": [{
                "sFlvUrl": "https://huya.example.com/live",
                "sStreamName": "room-123",
                "sFlvUrlSuffix": "flv",
                "sFlvAntiCode": "fm=abc&txyp=1"
              }]
            }]
          }
        };
      </script>
    </html>
    """
    monkeypatch.setattr(adapter, "_fetch_page", lambda url: html)

    info = adapter.parse("https://www.huya.com/123")

    assert info.platform == "huya"
    assert info.is_live is True
    assert info.title == "虎牙直播标题"
    assert info.streamer == "虎牙主播"
    assert info.stream_url == "https://huya.example.com/live/room-123.flv?fm=abc&txyp=1"
    assert info.quality_urls["source"] == "https://huya.example.com/live/room-123.flv?fm=abc&txyp=1"
    assert info.headers["Referer"] == "https://www.huya.com/"


def test_huya_offline_page_returns_offline_error(monkeypatch):
    from lsc.platforms.base import ERROR_OFFLINE
    from lsc.platforms.huya import HuyaAdapter

    adapter = HuyaAdapter()
    html = 'window.HNF_GLOBAL_INIT = {"roomInfo": {"tLiveStatus": 0}};'
    monkeypatch.setattr(adapter, "_fetch_page", lambda url: html)

    info = adapter.parse("https://www.huya.com/123")

    assert info.platform == "huya"
    assert info.is_live is False
    assert info.error_code == ERROR_OFFLINE
    assert "未开播" in info.error


def test_huya_registry_detection():
    assert detect_platform("https://www.huya.com/123") == "huya"
```

- [ ] **Step 2: Run Huya tests and verify they fail**

Run:

```powershell
python -m pytest tests/test_platform_adapters.py -q --no-cov
```

Expected: FAIL because `lsc.platforms.huya` does not exist.

- [ ] **Step 3: Implement Huya adapter**

Create `lsc/platforms/huya.py`:

```python
"""Adapter for public Huya live rooms."""
from __future__ import annotations

import json
import re
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .base import ERROR_OFFLINE, ERROR_PARSE_FAILED, ERROR_RESTRICTED, StreamInfo

HUYA_HEADERS = {
    "Referer": "https://www.huya.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


class HuyaAdapter:
    """Resolve public Huya live pages to stream URLs."""

    platform = "huya"

    def can_handle(self, url: str) -> bool:
        parsed = urlparse((url or "").strip())
        host = parsed.netloc.lower()
        return host.endswith("huya.com")

    def parse(self, url: str) -> StreamInfo:
        clean_url = (url or "").strip()
        try:
            html = self._fetch_page(clean_url)
            data = self._extract_global_init(html)
        except Exception as exc:
            return self._failed(clean_url, f"虎牙直播间解析失败: {exc}", ERROR_PARSE_FAILED)

        room_info = data.get("roomInfo") or {}
        profile_info = data.get("profileInfo") or {}
        live_status = int(room_info.get("tLiveStatus") or 0)
        if live_status != 1:
            return self._failed(clean_url, "虎牙直播间未开播", ERROR_OFFLINE, raw=data)

        quality_urls = self._extract_stream_urls(data)
        stream_url = next(iter(quality_urls.values()), "")
        if not stream_url:
            return self._failed(clean_url, "虎牙未找到公开直播流", ERROR_RESTRICTED, raw=data)

        return StreamInfo(
            platform=self.platform,
            room_url=clean_url,
            stream_url=stream_url,
            title=room_info.get("sIntroduction", "") or "",
            streamer=profile_info.get("nick", "") or "",
            is_live=True,
            quality_urls=quality_urls,
            selected_quality=next(iter(quality_urls.keys()), ""),
            headers=dict(HUYA_HEADERS),
            raw=data,
        )

    def _fetch_page(self, url: str) -> str:
        req = Request(url, headers=HUYA_HEADERS)
        with urlopen(req, timeout=15) as response:
            return response.read().decode("utf-8", errors="replace")

    def _extract_global_init(self, html: str) -> dict:
        match = re.search(r"window\.HNF_GLOBAL_INIT\s*=\s*(\{.*?\})\s*;", html, re.S)
        if not match:
            raise ValueError("未找到虎牙页面初始化数据")
        return json.loads(match.group(1))

    def _extract_stream_urls(self, data: dict) -> dict[str, str]:
        stream = data.get("stream") or {}
        for item in stream.get("data") or []:
            for stream_info in item.get("gameStreamInfoList") or []:
                flv_url = stream_info.get("sFlvUrl") or ""
                stream_name = stream_info.get("sStreamName") or ""
                suffix = stream_info.get("sFlvUrlSuffix") or "flv"
                anti_code = stream_info.get("sFlvAntiCode") or ""
                if flv_url and stream_name:
                    url = f"{flv_url.rstrip('/')}/{stream_name}.{suffix}"
                    if anti_code:
                        url = f"{url}?{anti_code}"
                    return {"source": url}
        return {}

    def _failed(self, url: str, error: str, code: str, raw: dict | None = None) -> StreamInfo:
        return StreamInfo(
            platform=self.platform,
            room_url=url,
            is_live=False,
            headers=dict(HUYA_HEADERS),
            raw=raw or {},
            error=error,
            error_code=code,
        )
```

- [ ] **Step 4: Register Huya adapter**

Modify `lsc/platforms/registry.py`:

```python
from .huya import HuyaAdapter
```

Replace `_default_adapters()` with:

```python
def _default_adapters() -> list[PlatformAdapter]:
    return [DirectAdapter(), DouyinAdapter(), BilibiliAdapter(), HuyaAdapter()]
```

- [ ] **Step 5: Run platform adapter tests**

Run:

```powershell
python -m pytest tests/test_platform_adapters.py -q --no-cov
```

Expected: PASS.

- [ ] **Step 6: Commit Task 4**

Run:

```powershell
git add lsc/platforms tests/test_platform_adapters.py
git commit -m "feat: add huya live platform adapter"
```

Expected: commit succeeds with the Huya adapter and tests.

---

### Task 5: Recording Controller and GUI Wiring

**Files:**
- Modify: `lsc/gui/pages/recording_controller.py`
- Modify: `lsc/gui/pages/record.py`
- Modify: `tests/test_comprehensive_platform.py`
- Modify: `tests/test_comprehensive_recording.py`

- [ ] **Step 1: Add failing controller and recording-header tests**

Append to `tests/test_comprehensive_platform.py`:

```python
class TestGenericPlatformParsing:
    def test_start_url_parse_uses_generic_parse_stream(self, qapp, monkeypatch):
        from lsc.gui.pages.recording_controller import RecordingController
        from lsc.platforms.base import StreamInfo

        def fake_parse_stream(url):
            return StreamInfo(
                platform="direct",
                room_url=url,
                stream_url=url,
                is_live=True,
                quality_urls={"origin": url},
                selected_quality="origin",
            )

        monkeypatch.setattr("lsc.gui.pages.recording_controller.parse_stream", fake_parse_stream)

        ctrl = RecordingController()
        result = ctrl.parse_stream_url("https://example.com/live.m3u8")

        assert result["platform"] == "direct"
        assert result["isLive"] is True
        assert result["streamUrl"] == "https://example.com/live.m3u8"
```

Append to `tests/test_comprehensive_recording.py`:

```python
class TestPlatformHeaders:
    def test_recording_passes_platform_input_args_to_capture(self, tmp_path):
        from lsc.gui.pages.recording_controller import RecordingController

        class FakeCapture:
            def __init__(self):
                self.calls = []
                self.status = CaptureStatus.RECORDING

            def start(self, url, output_path, *, codec="copy", input_args=None, extra_args=None):
                self.calls.append({
                    "url": url,
                    "output_path": output_path,
                    "codec": codec,
                    "input_args": input_args or [],
                    "extra_args": extra_args or [],
                })
                return True

        ctrl = RecordingController()
        ctrl._capture = FakeCapture()

        ok, path, encoder = ctrl.start_recording_with_crf(
            "https://example.com/live.m3u8",
            str(tmp_path),
            "Copy",
            23,
            input_args=["-headers", "Referer: https://example.com/\r\n"],
        )

        assert ok is True
        assert encoder == "Copy"
        assert ctrl._capture.calls[-1]["input_args"] == [
            "-headers",
            "Referer: https://example.com/\r\n",
        ]
```

- [ ] **Step 2: Run new controller tests and verify they fail**

Run:

```powershell
python -m pytest tests/test_comprehensive_platform.py::TestGenericPlatformParsing tests/test_comprehensive_recording.py::TestPlatformHeaders -q --no-cov
```

Expected: FAIL because `RecordingController.parse_stream_url()` does not exist.

- [ ] **Step 3: Add generic parser method to the controller**

Modify `lsc/gui/pages/recording_controller.py`.

Add import near the existing `lsc` import:

```python
from lsc.platforms.registry import parse_stream, select_quality
```

Add attributes in `RecordingController.__init__()` after `self.page_url`:

```python
        self.last_stream_info = None
        self.input_args: list[str] = []
```

Replace the existing URL parsing methods with:

```python
    def parse_stream_url(self, url: str) -> dict:
        """Parse any supported platform URL into the legacy dict consumed by the UI."""
        info = parse_stream(url)
        legacy = info.to_legacy_dict()
        self.last_stream_info = info
        self.input_args = legacy.get("_inputArgs", [])
        return legacy

    def parse_douyin_url(self, url: str) -> dict:
        """Compatibility wrapper for old tests and callers."""
        return self.parse_stream_url(url)

    def start_url_parse(self, url: str, on_parsed) -> None:
        """Launch async URL parsing. Calls on_parsed(dict) when done."""
        self._url_parser = UrlParserWorker(url, self.parse_stream_url)
        self._url_parser.finished.connect(on_parsed)
        self._url_parser.start()
```

Replace `select_stream_url()` with:

```python
    @staticmethod
    def select_stream_url(info: dict, quality_preset: str) -> tuple[str, str]:
        """Pick the best-matching source URL for the requested quality preset."""
        return select_quality(info, quality_preset)
```

- [ ] **Step 4: Pass platform input args from the record page into recording**

Modify `lsc/gui/pages/record.py` inside `_start_recording()` where `start_recording_with_crf()` is called.

Replace the call with:

```python
        success, output_path, encoder_used = self._ctrl.start_recording_with_crf(
            self._ctrl.stream_url,
            output_dir,
            encoder,
            crf,
            param_mode=param_mode,
            bitrate=bitrate_value,
            bitrate_unit=bitrate_unit,
            input_args=self._ctrl.input_args or None,
            on_status=lambda txt, typ: self.status_changed.emit(txt, typ),
        )
```

- [ ] **Step 5: Remove Douyin-only default headers from generic recording fallback**

Modify `start_recording_with_crf()` in `lsc/gui/pages/recording_controller.py`.

Replace:

```python
        if input_args is None:
            input_args = [
                "-headers",
                "Referer: https://live.douyin.com/\r\nUser-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36\r\n",
            ]
```

with:

```python
        if input_args is None:
            input_args = []
```

- [ ] **Step 6: Run focused controller and recording tests**

Run:

```powershell
python -m pytest tests/test_comprehensive_platform.py::TestGenericPlatformParsing tests/test_comprehensive_platform.py::TestPlatformAdapterCompatibility tests/test_comprehensive_recording.py::TestPlatformHeaders -q --no-cov
```

Expected: PASS.

- [ ] **Step 7: Run existing GUI preview regression tests**

Run:

```powershell
python -m pytest tests/test_gui_page_audit.py tests/test_gui_recording_preview.py -q --no-cov
```

Expected: PASS.

- [ ] **Step 8: Commit Task 5**

Run:

```powershell
git add lsc/gui/pages/recording_controller.py lsc/gui/pages/record.py tests/test_comprehensive_platform.py tests/test_comprehensive_recording.py
git commit -m "feat: wire platform adapters into recording controller"
```

Expected: commit succeeds with controller, GUI, and regression test changes.

---

### Task 6: Full Regression and Manual Platform Smoke Checklist

**Files:**
- Modify: `docs/superpowers/plans/2026-06-13-multiplatform-adapters-implementation.md` only if execution evidence needs to be appended.

- [ ] **Step 1: Run compile check**

Run:

```powershell
python -m compileall -q lsc tests
```

Expected: exits with code 0 and no output.

- [ ] **Step 2: Run focused platform and recording tests**

Run:

```powershell
python -m pytest tests/test_platform_adapters.py tests/test_comprehensive_platform.py tests/test_comprehensive_recording.py -q --no-cov
```

Expected: PASS.

- [ ] **Step 3: Run GUI regression tests**

Run:

```powershell
python -m pytest tests/test_gui_page_audit.py tests/test_gui_recording_preview.py -q --no-cov
```

Expected: PASS.

- [ ] **Step 4: Run full unit test suite without coverage gate**

Run:

```powershell
python -m pytest -q --no-cov
```

Expected: PASS.

- [ ] **Step 5: Run environment check**

Run:

```powershell
python -m lsc check
```

Expected: reports FFmpeg, FFprobe, and PySide6 as available.

- [ ] **Step 6: Manual smoke test for direct URL**

Use a known public test m3u8/flv URL. In the GUI:

```text
1. Start: python -m lsc gui
2. Paste the direct stream URL.
3. Click 连接.
4. Confirm status shows 已连接.
5. Click record.
6. Confirm a file is created in the selected output directory.
7. Stop recording.
8. Confirm playback starts from the saved file.
```

Expected: direct stream connects, previews, records, stops, and plays back.

- [ ] **Step 7: Manual smoke test for public platform URLs**

Use one public live room per platform:

```text
抖音: https://live.douyin.com/<room_id>
B站: https://live.bilibili.com/<room_id>
虎牙: https://www.huya.com/<room_id>
```

Expected for live public rooms: connects, previews, and records.

Expected for offline or restricted rooms: status shows a clear platform-specific message such as `B站直播间未开播`, `虎牙未找到公开直播流`, or `该直播间需要登录或平台限制`.

- [ ] **Step 8: Commit final verification notes if a note was appended**

If execution evidence was appended to this plan, run:

```powershell
git add docs/superpowers/plans/2026-06-13-multiplatform-adapters-implementation.md
git commit -m "docs: record multiplatform adapter verification"
```

Expected: commit succeeds only when the plan file changed during verification.

---

## Self-Review

Spec coverage:

- Douyin, Bilibili, Huya, and direct m3u8/flv support are covered by Tasks 1 through 4.
- Unified `StreamInfo` and legacy dict compatibility are covered by Tasks 1, 2, and 5.
- Existing single-room GUI flow is preserved by Task 5 and verified by Task 6.
- Public-only scope is represented by `restricted` error handling in Bilibili and Huya adapters.
- Multi-room preview, unified timeline, xiaohongshu, douyu, login state, and engine replacement are excluded from this plan.

Placeholder scan:

- The plan contains concrete file paths, test code, implementation code, exact commands, expected failures, expected passes, and commit commands.

Type consistency:

- `StreamInfo.to_legacy_dict()` defines `_inputArgs`; `RecordingController.parse_stream_url()` stores it as `self.input_args`; `RecordPage._start_recording()` passes it to `start_recording_with_crf()`; `StreamCapture.start()` already accepts `input_args`.
