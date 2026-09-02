"""录制与切片的磁盘目录约定。

单房间：``{output_dir}/{主播名}/``，多次开录复用，不因目录已存在而加 ``_1``。
对齐成功后：``{output_dir}/{主播A}+{主播B}/{主播名}/``，切片立即写入；
录像在 FFmpeg 停录后再改名为「开始至结束」并搬进该目录。
"""
from __future__ import annotations

import os
import re
import shutil
from datetime import datetime
from typing import Any

_ILLEGAL_FS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_IN_PROGRESS_SUFFIX = "_录制中.mp4"


def sanitize_folder_name(name: str, *, fallback: str = "room", max_len: int = 40) -> str:
    text = _ILLEGAL_FS.sub("_", (name or "").strip())
    text = re.sub(r"_+", "_", text).strip(" ._")
    if not text:
        return fallback
    return text[:max_len]


def streamer_folder_name(
    *,
    streamer_name: str = "",
    stream_title: str = "",
    room_id: str = "",
) -> str:
    if streamer_name.strip():
        return sanitize_folder_name(streamer_name)
    if stream_title.strip():
        return sanitize_folder_name(stream_title)
    short = (room_id or "room")[-6:]
    return sanitize_folder_name(short, fallback="room")


def bundle_folder_name(streamer_names: list[str]) -> str:
    seen: list[str] = []
    for raw in streamer_names:
        name = sanitize_folder_name(raw) if raw else ""
        if not name or name in seen:
            continue
        seen.append(name)
    if not seen:
        return "sync"
    return "+".join(seen)


def room_recording_dir(
    base_dir: str,
    *,
    streamer_name: str = "",
    stream_title: str = "",
    room_id: str = "",
    bundle_dir: str = "",
) -> str:
    leaf = streamer_folder_name(
        streamer_name=streamer_name,
        stream_title=stream_title,
        room_id=room_id,
    )
    parent = bundle_dir if bundle_dir else base_dir
    return os.path.join(parent, leaf)


def recording_in_progress_filename(started_at: datetime) -> str:
    return started_at.strftime("%Y-%m-%d_%H-%M-%S") + _IN_PROGRESS_SUFFIX


def recording_in_progress_path(output_dir: str, started_at: datetime) -> str:
    os.makedirs(output_dir, exist_ok=True)
    return ensure_unique_path(os.path.join(output_dir, recording_in_progress_filename(started_at)))


def finalize_room_recording(room: Any, source_path: str, *, ended_at: datetime | None = None) -> str:
    started_at = getattr(room, "record_started_at", None) or datetime.now()
    ended = ended_at or datetime.now()
    bundle = str(getattr(room, "output_bundle_dir", "") or "")
    if bundle:
        dest_dir = recording_dest_dir(room, bundle)
    else:
        dest_dir = os.path.dirname(source_path) or "."
    return finalize_recording_file(
        source_path,
        started_at=started_at,
        ended_at=ended,
        dest_dir=dest_dir,
    )


def recording_final_filename(started_at: datetime, ended_at: datetime) -> str:
    return (
        f"{started_at.strftime('%Y-%m-%d_%H-%M-%S')}"
        f"_至_"
        f"{ended_at.strftime('%Y-%m-%d_%H-%M-%S')}.mp4"
    )


def ensure_unique_path(path: str) -> str:
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    suffix = 1
    candidate = f"{stem}_{suffix}{ext}"
    while os.path.exists(candidate):
        suffix += 1
        candidate = f"{stem}_{suffix}{ext}"
    return candidate


def resolve_clip_output_dir(room: Any, base_dir: str) -> str:
    bundle = str(getattr(room, "output_bundle_dir", "") or "")
    dest = room_recording_dir(
        base_dir,
        streamer_name=getattr(room, "streamer_name", "") or "",
        stream_title=getattr(room, "stream_title", "") or "",
        room_id=getattr(room, "room_id", "") or "",
        bundle_dir=bundle,
    )
    os.makedirs(dest, exist_ok=True)
    return dest


def bind_rooms_to_bundle(rooms: list[Any], base_dir: str) -> str:
    names = [
        streamer_folder_name(
            streamer_name=getattr(room, "streamer_name", "") or "",
            stream_title=getattr(room, "stream_title", "") or "",
            room_id=getattr(room, "room_id", "") or "",
        )
        for room in rooms
    ]
    bundle_dir = os.path.join(base_dir, bundle_folder_name(names))
    os.makedirs(bundle_dir, exist_ok=True)
    for room in rooms:
        room.output_bundle_dir = bundle_dir
        os.makedirs(
            room_recording_dir(
                base_dir,
                streamer_name=getattr(room, "streamer_name", "") or "",
                stream_title=getattr(room, "stream_title", "") or "",
                room_id=getattr(room, "room_id", "") or "",
                bundle_dir=bundle_dir,
            ),
            exist_ok=True,
        )
    return bundle_dir


def recording_dest_dir(room: Any, base_dir: str) -> str:
    bundle = str(getattr(room, "output_bundle_dir", "") or "")
    dest = room_recording_dir(
        base_dir,
        streamer_name=getattr(room, "streamer_name", "") or "",
        stream_title=getattr(room, "stream_title", "") or "",
        room_id=getattr(room, "room_id", "") or "",
        bundle_dir=bundle,
    )
    os.makedirs(dest, exist_ok=True)
    return dest


def finalize_recording_file(
    source_path: str,
    *,
    started_at: datetime,
    ended_at: datetime,
    dest_dir: str,
) -> str:
    if not source_path:
        return source_path
    os.makedirs(dest_dir, exist_ok=True)
    dest = ensure_unique_path(
        os.path.join(dest_dir, recording_final_filename(started_at, ended_at))
    )
    if os.path.abspath(source_path) == os.path.abspath(dest):
        return dest
    if not os.path.isfile(source_path):
        return source_path
    shutil.move(source_path, dest)
    return dest
