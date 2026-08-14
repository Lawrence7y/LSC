"""Adapter for direct stream URLs."""
from __future__ import annotations

import logging

_log = logging.getLogger(__name__)

from urllib.parse import ParseResult, parse_qsl, urlparse

from .base import BasePlatformAdapter, StreamInfo
from .redaction import redact_url
from .url_policy import validate_public_url

_DIRECT_SUFFIXES = (".m3u8", ".flv")
_DIRECT_QUERY_FORMAT_KEYS = {"type", "format", "container", "ext"}
_DIRECT_QUERY_URL_KEYS = {"url", "play_url", "stream", "stream_url", "src", "target"}
_DIRECT_HINT_TOKENS = (".m3u8", ".flv", "m3u8", "flv")


class DirectAdapter(BasePlatformAdapter):
    platform = "direct"
    display_name = "直链"

    def can_handle(self, url: str) -> bool:
        _log.debug("Direct: checking %s", redact_url(url)[:60])
        clean_url = (url or "").strip()
        parsed = urlparse(clean_url)
        safe, _reason = validate_public_url(clean_url)
        if not safe or not parsed.netloc:
            return False
        if parsed.path.lower().endswith(_DIRECT_SUFFIXES):
            return True
        return self._has_direct_query_hint(parsed)

    def parse(self, url: str) -> StreamInfo:
        _log.info("Direct: parsing %s", redact_url(url)[:80])
        clean_url = (url or "").strip()
        safe, reason = validate_public_url(clean_url)
        if not safe:
            return self._failed(clean_url, reason, "restricted")
        return self._success(
            clean_url,
            stream_url=clean_url,
            title="公开直播流",
            streamer="直链",
            is_live=True,
            quality_urls={"origin": clean_url},
            selected_quality="origin",
            raw={
                "source_kind": "official",
                "confidence": 0.95,
                "state_source": "direct_url",
                "validate_redirects": True,
            },
        )

    def _has_direct_query_hint(self, parsed: ParseResult) -> bool:
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            key_lower = key.lower()
            value_lower = value.lower()
            if key_lower in _DIRECT_QUERY_FORMAT_KEYS and value_lower in {"m3u8", "flv"}:
                return True
            if key_lower in _DIRECT_QUERY_URL_KEYS and any(token in value_lower for token in _DIRECT_HINT_TOKENS):
                return True
        return False
