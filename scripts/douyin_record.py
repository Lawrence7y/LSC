#!/usr/bin/env python3
"""Minimal Douyin page parser reused by the platform adapter."""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener, getproxies

try:
    from lsc.platforms.redaction import redact_text, redact_url
except ModuleNotFoundError:  # direct ``python scripts/douyin_record.py`` usage
    _repo_root = str(Path(__file__).resolve().parents[1])
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)
    from lsc.platforms.redaction import redact_text, redact_url

log = logging.getLogger("lsc.douyin")
logging.basicConfig(
    level=os.environ.get("LSC_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# Unified HTTP defaults (mirrors lsc.platforms.base to avoid importing lsc here).
_HTTP_TIMEOUT = 20
_HTTP_RETRIES = 3


def _is_private_ip(hostname: str) -> bool:
    """Reject private/internal/reserved IP targets before network access.

    198.18.0.0/15 intentionally excluded: Clash/FlClash TUN mode Fake IP range.
    """
    networks = [
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("192.168.0.0/16"),
        ipaddress.ip_network("127.0.0.0/8"),
        ipaddress.ip_network("169.254.0.0/16"),
        ipaddress.ip_network("100.64.0.0/10"),
        # 198.18.0.0/15 NOT blocked: Clash/FlClash TUN Fake IP, not real internal network
        ipaddress.ip_network("0.0.0.0/8"),
        ipaddress.ip_network("240.0.0.0/4"),
        ipaddress.ip_network("::1/128"),
        ipaddress.ip_network("fc00::/7"),
        ipaddress.ip_network("fe80::/10"),
        ipaddress.ip_network("::ffff:0:0/96"),
    ]
    try:
        for _family, _, _, _, sockaddr in socket.getaddrinfo(hostname, None):
            ip = ipaddress.ip_address(sockaddr[0])
            if any(ip in network for network in networks):
                return True
    except Exception:
        return True
    return False


class _SSRFRedirectHandler(HTTPRedirectHandler):
    """Revalidate every redirect target before following it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is None:
            return None
        target = urlparse(new_req.full_url)
        hostname = (target.hostname or "").lower()
        if not hostname or target.scheme not in ("http", "https") or _is_private_ip(hostname):
            return None
        return new_req


_SSRF_SAFE_OPENER = build_opener(
    _SSRFRedirectHandler(),
    ProxyHandler(getproxies()),  # explicit system proxy (env vars / Windows registry)
)


def urlopen(request, *, timeout: float):
    """Compatibility seam for tests/callers while retaining the safe opener."""
    return _SSRF_SAFE_OPENER.open(request, timeout=timeout)


def fetch_page(
    url: str,
    cookies: dict[str, str] | None = None,
    *,
    proxy_url: str = "",
    timeout_sec: float | None = None,
) -> tuple[str | None, str | None]:
    """Fetch the Douyin live page HTML with unified timeout and retry.

    Returns
    -------
    (html, None) on success; (None, reason) on failure.
    """
    import time
    from urllib.error import HTTPError, URLError

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None, "仅支持 http/https 链接"
    if _is_private_ip(parsed.hostname):
        return None, "不允许访问内网/保留地址"

    request_timeout = _HTTP_TIMEOUT
    if timeout_sec is not None:
        try:
            request_timeout = max(1.0, min(300.0, float(timeout_sec)))
        except (TypeError, ValueError):
            request_timeout = _HTTP_TIMEOUT

    scoped_proxy = str(proxy_url or "").strip()
    opener = _SSRF_SAFE_OPENER
    if scoped_proxy:
        proxy = urlparse(scoped_proxy)
        if proxy.scheme not in ("http", "https") or not proxy.hostname:
            return None, "仅支持 http/https 代理"
        # Keep redirects SSRF-safe while scoping this request to the resolved
        # platform proxy. Do not log or expose proxy credentials.
        opener = build_opener(
            _SSRFRedirectHandler(),
            ProxyHandler({"http": scoped_proxy, "https": scoped_proxy}),
        )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://live.douyin.com/",
    }
    if cookies:
        # Cookie 头必须 latin-1 可编码；过滤解密失败产生的 � 等脏值
        safe_pairs = []
        for k, v in cookies.items():
            if not isinstance(k, str) or not isinstance(v, str):
                continue
            if "�" in k or "�" in v:
                continue
            try:
                k.encode("latin-1")
                v.encode("latin-1")
            except UnicodeEncodeError:
                continue
            safe_pairs.append(f"{k}={v}")
        if safe_pairs:
            headers["Cookie"] = "; ".join(safe_pairs)

    last_exc: Exception | None = None
    reason: str | None = None
    for attempt in range(_HTTP_RETRIES + 1):
        try:
            request = Request(url, headers=headers)
            if scoped_proxy:
                response_context = opener.open(request, timeout=request_timeout)
            else:
                # Preserve the legacy urlopen seam for existing callers/tests.
                response_context = urlopen(request, timeout=request_timeout)
            with response_context as response:
                return response.read().decode("utf-8", errors="replace"), None
        except HTTPError as exc:
            last_exc = exc
            reason = f"HTTP {exc.code}"
            if attempt < _HTTP_RETRIES:
                time.sleep(1.0 * (attempt + 1))
        except URLError as exc:
            last_exc = exc
            reason = f"网络错误: {exc.reason}"
            if attempt < _HTTP_RETRIES:
                time.sleep(1.0 * (attempt + 1))
        except Exception as exc:
            last_exc = exc
            reason = f"连接异常: {exc}"
            if attempt < _HTTP_RETRIES:
                time.sleep(1.0 * (attempt + 1))
    log.warning("fetch_page failed url=%s err=%s", redact_url(url), redact_text(last_exc))
    return None, reason


def extract_ssr_data(html: str) -> dict[str, object]:
    """Extract live stream info from Douyin SSR payloads embedded in HTML."""
    prefix = 'self.__pace_f.push([1,"'
    title_fields = [
        "title",
        "room.title",
        "seo_title",
        "room_name",
        "room.roomName",
        "room.name",
        "liveRoom.name",
        "liveRoom.title",
        "data.title",
        "data.room.title",
    ]
    streamer_fields = [
        "owner.nickname",
        "anchor.nickname",
        "nickname",
        "owner.display_id",
        "owner.name",
        "anchor.name",
        "streamer.name",
        "user.nickname",
        "user.name",
        "data.owner.nickname",
        "data.anchor.nickname",
    ]
    room_id_fields = ["room_id", "roomId", "room.id", "web_rid", "id_str"]
    quality_keys = ["origin", "uhd", "hd", "sd", "ld", "ao"]

    info: dict[str, object] = {
        "platform": "douyin",
        "isLive": False,
        "title": "",
        "streamerName": "",
        "roomId": "",
        "streamUrl": "",
        "backupStreamUrl": "",
        "selectedQuality": "",
        "availableQualities": [],
        "qualityUrls": {},
        "category": "",
    }

    def pick_first(obj: dict[str, object], fields: list[str]) -> str:
        for field in fields:
            current: object = obj
            valid = True
            for part in field.split("."):
                if not isinstance(current, dict):
                    valid = False
                    break
                current = current.get(part)
            if valid and isinstance(current, str) and current.strip():
                return current.strip()
        return ""

    def find_value_by_path(obj: object, path: str) -> str:
        parts = path.split(".")
        _INVALID_VALUES = {"", "$undefined", "undefined", "null", "None", "0", "false", "False"}

        def search(current: object, part_idx: int) -> str:
            if part_idx == len(parts):
                if isinstance(current, (str, int, float)):
                    val = str(current).strip()
                    if val and val not in _INVALID_VALUES:
                        return val
                return ""

            part = parts[part_idx]

            if isinstance(current, dict):
                if part in current:
                    val = search(current[part], part_idx + 1)
                    if val:
                        return val
                for v in current.values():
                    val = search(v, part_idx)
                    if val:
                        return val
            elif isinstance(current, list):
                for item in current:
                    val = search(item, part_idx)
                    if val:
                        return val
            return ""

        return search(obj, 0)

    def is_valid_url(value: object) -> bool:
        return isinstance(value, str) and value.startswith(("http://", "https://"))

    quality_urls = info["qualityUrls"]
    if not isinstance(quality_urls, dict):
        raise ValueError(f"qualityUrls 类型错误: 期望 dict, 实际 {type(quality_urls).__name__}")
    available_qualities = info["availableQualities"]
    if not isinstance(available_qualities, list):
        raise ValueError(f"availableQualities 类型错误: 期望 list, 实际 {type(available_qualities).__name__}")

    # 1. Concatenate all pace_f string payloads
    search_pos = 0
    chunks = []
    while search_pos < len(html):
        start_idx = html.find(prefix, search_pos)
        if start_idx < 0:
            break
        start_idx += len(prefix)
        end_idx = html.find('"])', start_idx)
        if end_idx < 0:
            end_idx = html.find('"])</script>', start_idx)
        if end_idx < 0:
            search_pos = start_idx
            continue

        raw_str = html[start_idx:end_idx]
        try:
            decoded = json.loads('"' + raw_str + '"')
            chunks.append(decoded)
        except Exception:
            s = raw_str.replace('\\"', '"').replace('\\/', '/').replace('\\\\', '\\')
            chunks.append(s)

        search_pos = end_idx + 3

    full_payload = "".join(chunks)

    # 2. Sequentially parse JSON objects from full_payload using raw_decode
    decoder = json.JSONDecoder()
    pos = 0
    doc = None  # 初始化为 None，防止所有 JSON 解析失败时后续访问 doc 触发 UnboundLocalError
    chunk_pattern = re.compile(r'([a-zA-Z0-9_$]+):(?:([HLIMSJTH])([a-f0-9]+)?,)?')

    while pos < len(full_payload):
        match = chunk_pattern.search(full_payload, pos)
        if not match:
            break

        header_end = match.end()
        start_pos = header_end
        if start_pos < len(full_payload) and full_payload[start_pos] == ',':
            start_pos += 1

        if start_pos < len(full_payload) and full_payload[start_pos] in ('{', '['):
            try:
                doc, end_idx = decoder.raw_decode(full_payload, start_pos)

                # Extract meta info using recursive path finder
                if not info["title"]:
                    title_paths = [
                        "roomStore.roomInfo.room.title",
                        "room.title",
                        "liveRoom.title",
                        "liveRoom.name",
                    ]
                    for p in title_paths:
                        val = find_value_by_path(doc, p)
                        if val and not val.startswith("$"):
                            info["title"] = val
                            break

                if not info["streamerName"]:
                    streamer_paths = [
                        "roomStore.roomInfo.room.owner.nickname",
                        "roomStore.roomInfo.anchor.nickname",
                        "owner.nickname",
                        "anchor.nickname",
                        "user.nickname",
                    ]
                    for p in streamer_paths:
                        val = find_value_by_path(doc, p)
                        if val and not val.startswith("$"):
                            info["streamerName"] = val
                            break

                if not info["roomId"]:
                    room_id_paths = [
                        "roomStore.roomInfo.room.id_str",
                        "room.id_str",
                        "roomId",
                        "room_id",
                    ]
                    for p in room_id_paths:
                        val = find_value_by_path(doc, p)
                        if val and not val.startswith("$"):
                            info["roomId"] = val
                            break

                root = doc if isinstance(doc, dict) else {}
                data = root.get("data", {})
                if not isinstance(data, dict):
                    data = {}

                if not info["title"]:
                    val = pick_first(data, title_fields) or pick_first(root, title_fields)
                    if val and not val.startswith("$"):
                        info["title"] = val
                if not info["streamerName"]:
                    val = pick_first(data, streamer_fields) or pick_first(root, streamer_fields)
                    if val and not val.startswith("$"):
                        info["streamerName"] = val
                if not info["roomId"]:
                    val = pick_first(data, room_id_fields) or pick_first(root, room_id_fields)
                    if val and not val.startswith("$"):
                        info["roomId"] = val

                for quality in quality_keys:
                    if quality in available_qualities:
                        continue
                    main = ((data.get(quality) or {}).get("main") or {}) if isinstance(data.get(quality), dict) else {}
                    flv_url = str(main.get("flv") or "").replace("\\u0026", "&")
                    hls_url = str(main.get("hls") or "").replace("\\u0026", "&")
                    preferred_url = flv_url if is_valid_url(flv_url) else hls_url
                    if not is_valid_url(preferred_url):
                        continue

                    available_qualities.append(quality)
                    quality_urls[quality] = preferred_url
                    if not info["streamUrl"]:
                        info["streamUrl"] = preferred_url
                        info["backupStreamUrl"] = hls_url if is_valid_url(hls_url) else preferred_url
                        info["selectedQuality"] = quality
                        info["isLive"] = True

                camera_list = data.get("cameraInfoList", [])
                if isinstance(camera_list, list):
                    for camera in camera_list:
                        if not isinstance(camera, dict):
                            continue
                        h264 = camera.get("h264Stream", {})
                        if not isinstance(h264, dict):
                            continue

                        hls_pull = str(h264.get("hls_pull_url") or "").replace("\\u0026", "&")
                        if not info["streamUrl"] and is_valid_url(hls_pull):
                            info["streamUrl"] = hls_pull
                            info["backupStreamUrl"] = hls_pull
                            info["selectedQuality"] = "h264_hls"
                            info["isLive"] = True
                            quality_urls.setdefault("h264_hls", hls_pull)
                            if "h264_hls" not in available_qualities:
                                available_qualities.append("h264_hls")

                        hls_map = h264.get("hls_pull_url_map", {})
                        if not isinstance(hls_map, dict):
                            continue
                        for quality in ["FULL_HD1", "UHD1", "HD1", "SD1", "SD2"]:
                            if quality in available_qualities:
                                continue
                            quality_url = str(hls_map.get(quality) or "").replace("\\u0026", "&")
                            if not is_valid_url(quality_url):
                                continue
                            available_qualities.append(quality)
                            quality_urls[quality] = quality_url
                            if not info["streamUrl"]:
                                info["streamUrl"] = quality_url
                                info["selectedQuality"] = quality
                                info["isLive"] = True
                            if not info["backupStreamUrl"]:
                                info["backupStreamUrl"] = quality_url

                pos = end_idx
                continue
            except Exception as exc:
                log.debug("douyin JSON chunk parse failed at pos %d: %s", start_pos, exc)
        pos = start_pos

    if not info["streamUrl"]:
        match = re.search(r'hls_pull_url[^"]*?(https?://pull-hls[^"]+\.m3u8\?expire=\d+\\u0026[^"]+)', html)
        if match:
            stream_url = match.group(1).replace("\\u0026", "&")
            if is_valid_url(stream_url):
                info["streamUrl"] = stream_url
                info["backupStreamUrl"] = stream_url
                info["selectedQuality"] = "regex_hls"
                info["isLive"] = True
                quality_urls.setdefault("regex_hls", stream_url)
                if "regex_hls" not in available_qualities:
                    available_qualities.append("regex_hls")

    if not info.get("category"):
        category_paths = [
            "roomStore.roomInfo.room.category",
            "roomStore.roomInfo.category",
            "liveRoom.category",
            "anchor.category",
            "data.category",
            "room.category",
        ]
        for p in category_paths:
            val = find_value_by_path(doc if isinstance(doc, dict) else {}, p)
            if val and not val.startswith("$"):
                info["category"] = val
                break

    return info
