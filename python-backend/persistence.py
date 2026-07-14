"""房间配置持久化。

将前端传入的房间列表以 JSON 形式保存到本地，程序启动时读取并推送给前端。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# 默认数据目录：项目根目录下的 data 文件夹
# 使用项目内目录，避免沙箱外路径导致写入失败
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = _PROJECT_ROOT / "data"
ROOMS_FILE = DEFAULT_DATA_DIR / "rooms.json"


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _log.debug("目录已确保存在: %s", path)


def load_rooms(path: Path | str | None = None) -> list[dict[str, Any]]:
    """从 JSON 文件读取已保存的房间列表。

    支持两种格式：
    - {"rooms": [...]}
    - [...]

    文件不存在或解析失败时返回空列表。
    """
    file_path = Path(path) if path else ROOMS_FILE
    if not file_path.exists():
        _log.info("房间配置文件不存在，使用空列表: %s", file_path)
        return []

    try:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        _log.warning("加载房间配置失败，使用空列表: %s", exc)
        return []

    if isinstance(data, dict):
        rooms = data.get("rooms", [])
        if not isinstance(rooms, list):
            _log.warning("房间配置格式错误: rooms 字段不是列表")
            return []
        _log.info("已加载 %d 个房间", len(rooms))
        return rooms
    if isinstance(data, list):
        _log.info("已加载 %d 个房间 (legacy list format)", len(data))
        return data
    _log.warning("房间配置格式错误: 未知数据结构 %s", type(data).__name__)
    return []


def save_rooms(
    rooms: list[dict[str, Any]],
    path: Path | str | None = None,
) -> bool:
    """将房间列表写入 JSON 文件。

    使用临时文件 + 替换的方式尽量减少写坏文件的概率。
    """
    file_path = Path(path) if path else ROOMS_FILE
    try:
        _ensure_dir(file_path.parent)
        tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
        payload = {"rooms": rooms}
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        tmp_path.replace(file_path)
        _log.info("已保存 %d 个房间到 %s", len(rooms), file_path.name)
        return True
    except Exception as exc:
        _log.error("保存房间配置失败: %s", exc, exc_info=True)
        return False


# ── 高光分析结果持久化 ──────────────────────────────────────
# 存储位置：录制文件同目录 {basename}.analysis.json
# 与录制文件生命周期绑定，删除录制时分析结果自然清理，无需额外管理

_ANALYSIS_SCHEMA_VERSION = 1


def _analysis_json_path(video_path: str) -> Path:
    """录制文件同目录的分析结果 JSON 路径：{basename}.analysis.json。"""
    p = Path(video_path)
    return p.with_name(p.stem + ".analysis.json")


def save_analysis_results(
    video_path: str,
    room_id: str,
    mode: str,
    highlights: list[dict[str, Any]],
    analysis_time_sec: float = 0.0,
    weights: dict[str, float] | None = None,
) -> bool:
    """保存高光分析结果到录制文件同目录。

    与录制文件生命周期绑定，删除录制时分析结果自然清理。
    ``video_mtime`` 用于校验：录制文件被覆盖重录时 mtime 变化，旧结果失效。

    Returns:
        True 保存成功，False 失败（仅记日志，不抛异常）。
    """
    file_path = _analysis_json_path(video_path)
    try:
        mtime = os.path.getmtime(video_path) if os.path.isfile(video_path) else 0.0
        payload = {
            "schema_version": _ANALYSIS_SCHEMA_VERSION,
            "room_id": room_id,
            "video_path": video_path,
            "video_mtime": mtime,
            "mode": mode,
            "analyzed_at": _now_iso(),
            "analysis_time_sec": round(analysis_time_sec, 2),
            "weights": weights or {},
            "highlights": highlights,
        }
        _ensure_dir(file_path.parent)
        tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        tmp_path.replace(file_path)
        _log.info("分析结果已保存: %s (%d 段高光, 耗时=%.1fs)", file_path.name, len(highlights), analysis_time_sec)
        return True
    except Exception as exc:
        _log.error("保存分析结果失败: %s", exc, exc_info=True)
        return False


def load_analysis_results(video_path: str) -> dict[str, Any] | None:
    """读取录制文件同目录的分析结果 JSON。

    Returns:
        完整 dict（含 schema_version/video_mtime/mode/highlights 等），
        文件不存在或解析失败返回 None。调用方应校验 ``video_mtime`` 是否
        匹配当前录制文件（不匹配则视为过期，需重新分析）。
    """
    file_path = _analysis_json_path(video_path)
    if not file_path.exists():
        _log.debug("分析结果文件不存在: %s", file_path)
        return None
    try:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
        _log.info("已加载分析结果: %s (%d 段高光)", file_path.name, len(data.get("highlights", [])))
        return data
    except Exception as exc:
        _log.warning("加载分析结果失败: %s", exc)
        return None


def is_analysis_stale(video_path: str, stored: dict[str, Any]) -> bool:
    """校验已存分析结果是否过期（录制文件 mtime 变化即视为过期）。

    录制文件不存在、mtime 不匹配、或 stored 缺少 video_mtime 字段时返回 True。
    """
    if not os.path.isfile(video_path):
        _log.debug("分析结果过期: 录制文件不存在 %s", video_path)
        return True
    stored_mtime = stored.get("video_mtime", 0.0)
    if not stored_mtime:
        _log.debug("分析结果过期: stored 缺少 video_mtime")
        return True
    try:
        current_mtime = os.path.getmtime(video_path)
        stale = abs(current_mtime - stored_mtime) > 1.0
        if stale:
            _log.info("分析结果已过期: mtime 变化 %.1fs", abs(current_mtime - stored_mtime))
        return stale
    except OSError as exc:
        _log.warning("分析结果过期检查失败: %s", exc)
        return True


def _now_iso() -> str:
    """当前时间的 ISO 8601 字符串（本地时区）。"""
    import datetime as _dt
    return _dt.datetime.now().isoformat(timespec="seconds")
