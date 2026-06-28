#!/usr/bin/env python3
"""Minimal Douyin page parser reused by the platform adapter."""
from __future__ import annotations

import json
import logging
import os
import re

log = logging.getLogger("lsc.douyin")
logging.basicConfig(
    level=os.environ.get("LSC_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# Unified HTTP defaults (mirrors lsc.platforms.base to avoid importing lsc here).
_HTTP_TIMEOUT = 12
_HTTP_RETRIES = 2


def fetch_page(url: str) -> str | None:
    """Fetch the Douyin live page HTML with unified timeout and retry."""
    import time
    from urllib.error import URLError
    from urllib.request import Request, urlopen

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    last_exc: Exception | None = None
    for attempt in range(_HTTP_RETRIES + 1):
        try:
            request = Request(url, headers=headers)
            with urlopen(request, timeout=_HTTP_TIMEOUT) as response:
                return response.read().decode("utf-8", errors="replace")
        except (URLError, Exception) as exc:
            last_exc = exc
            if attempt < _HTTP_RETRIES:
                time.sleep(0.5 * (attempt + 1))
    log.warning("fetch_page failed url=%s err=%s", url, last_exc)
    return None


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
    assert isinstance(quality_urls, dict)
    available_qualities = info["availableQualities"]
    assert isinstance(available_qualities, list)

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
            except Exception:
                pass
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

    return info
