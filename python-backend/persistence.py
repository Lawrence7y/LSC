"""房间配置持久化。

将前端传入的房间列表以 JSON 形式保存到本地，程序启动时读取并推送给前端。
"""
from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

_persist_lock = threading.Lock()

# 打包后的项目根目录在安装路径中（通常是 Program Files），不能写入。
# Electron 会将 LSC_DATA_DIR 指向 userData；未设置时保留开发模式的项目内位置。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PERSISTENCE_ROOT = Path(os.environ.get("LSC_DATA_DIR", _PROJECT_ROOT))
DEFAULT_DATA_DIR = _PERSISTENCE_ROOT / "data"
ROOMS_FILE = DEFAULT_DATA_DIR / "rooms.json"
SETTINGS_FILE = _PERSISTENCE_ROOT / "settings.json"


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _log.debug("目录已确保存在: %s", path)


def load_rooms(path: Path | str | None = None) -> list[dict[str, Any]]:
    """从 JSON 文件读取已保存的房间列表。

    支持两种格式：
    - {"rooms": [...]}
    - [...]

    文件不存在时返回空列表；解析失败时尝试从 .bak 备份恢复。
    """
    file_path = Path(path) if path else ROOMS_FILE
    if not file_path.exists():
        _log.info("房间配置文件不存在，使用空列表: %s", file_path)
        return []

    data = _load_json_with_backup(file_path)
    if data is None:
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


def _backup_path(file_path: Path) -> Path:
    """配置文件对应的备份路径：{name}.bak。"""
    return file_path.with_suffix(file_path.suffix + ".bak")


def _load_json_with_backup(file_path: Path) -> Any | None:
    """读取 JSON；主文件损坏时尝试从 .bak 备份恢复。

    Returns:
        解析后的数据；主文件与备份均不可用时返回 None。
    """
    try:
        with open(file_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        _log.warning("加载 %s 失败: %s，尝试从备份恢复", file_path.name, exc)

    backup = _backup_path(file_path)
    if not backup.exists():
        _log.warning("无可用备份: %s", backup)
        return None
    try:
        with open(backup, encoding="utf-8") as f:
            data = json.load(f)
        _log.info("已从备份恢复配置: %s", backup.name)
        return data
    except Exception as exc:
        _log.error("备份文件也损坏，放弃恢复: %s", exc)
        return None


def _backup_existing(file_path: Path) -> None:
    """覆盖前将当前文件备份为 .bak（best-effort，失败仅记日志）。"""
    if not file_path.exists():
        return
    try:
        import shutil
        shutil.copy2(file_path, _backup_path(file_path))
    except OSError as exc:
        _log.warning("备份 %s 失败（继续保存）: %s", file_path.name, exc)


def save_rooms(
    rooms: list[dict[str, Any]],
    path: Path | str | None = None,
    *,
    fsync: bool = False,
) -> bool:
    """将房间列表写入 JSON 文件。

    使用临时文件 + 替换的方式尽量减少写坏文件的概率；
    替换前将上一版好文件备份为 .bak，供损坏时恢复。
    默认不 fsync（高频写合并场景）；flush_pending_saves / 显式 fsync=True 才刷盘。
    """
    file_path = Path(path) if path else ROOMS_FILE
    try:
        with _persist_lock:
            _ensure_dir(file_path.parent)
            tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
            payload = {"rooms": rooms}
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.flush()
                if fsync:
                    os.fsync(f.fileno())
            _backup_existing(file_path)
            tmp_path.replace(file_path)
            global _rooms_write_count
            _rooms_write_count += 1
        _log.info("已保存 %d 个房间到 %s", len(rooms), file_path.name)
        return True
    except Exception as exc:
        _log.error("保存房间配置失败: %s", exc, exc_info=True)
        return False


_rooms_write_count = 0
_FSYNC_EVERY_N_WRITES = 5
_save_rooms_timer: threading.Timer | None = None
_pending_rooms_payload: tuple[list[dict[str, Any]], Path] | None = None
_schedule_lock = threading.Lock()


def schedule_save_rooms(
    rooms: list[dict[str, Any]],
    path: Path | str | None = None,
    *,
    delay_sec: float = 1.0,
) -> None:
    """1 秒内多次保存合并为一次；到期后写入，每 N 次才 fsync。"""
    global _save_rooms_timer, _pending_rooms_payload
    file_path = Path(path) if path else ROOMS_FILE
    # 拷贝列表，避免调用方后续原地改动污染待写快照
    snapshot = [dict(r) for r in rooms]
    with _schedule_lock:
        _pending_rooms_payload = (snapshot, file_path)
        if _save_rooms_timer is not None:
            _save_rooms_timer.cancel()
        timer = threading.Timer(delay_sec, _flush_scheduled_rooms)
        timer.daemon = True
        _save_rooms_timer = timer
        timer.start()


def _flush_scheduled_rooms() -> None:
    global _save_rooms_timer, _pending_rooms_payload
    with _schedule_lock:
        pending = _pending_rooms_payload
        _pending_rooms_payload = None
        _save_rooms_timer = None
    if pending is None:
        return
    rooms, file_path = pending
    do_fsync = (_rooms_write_count + 1) % _FSYNC_EVERY_N_WRITES == 0
    save_rooms(rooms, file_path, fsync=do_fsync)


def flush_pending_room_saves(*, fsync: bool = True) -> bool:
    """立即写出合并队列中的房间配置（关机/测试用）。"""
    global _save_rooms_timer, _pending_rooms_payload
    with _schedule_lock:
        if _save_rooms_timer is not None:
            _save_rooms_timer.cancel()
            _save_rooms_timer = None
        pending = _pending_rooms_payload
        _pending_rooms_payload = None
    if pending is None:
        return True
    rooms, file_path = pending
    return save_rooms(rooms, file_path, fsync=fsync)


def save_settings(
    settings: dict[str, Any],
    path: Path | str | None = None,
    *,
    fsync: bool = False,
) -> bool:
    """将应用设置写入 JSON 文件（原子写入，进程内互斥）。"""
    file_path = Path(path) if path else SETTINGS_FILE
    try:
        with _persist_lock:
            _ensure_dir(file_path.parent)
            tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
                f.flush()
                if fsync:
                    os.fsync(f.fileno())
            tmp_path.replace(file_path)
        _log.info("已保存设置到 %s", file_path.name)
        return True
    except Exception as exc:
        _log.error("保存设置失败: %s", exc, exc_info=True)
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
        with _persist_lock:
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
                f.flush()
                os.fsync(f.fileno())
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
        stale = abs(current_mtime - stored_mtime) > 0.5
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


# ── P3-1: 配置热更新 ───────────────────────────────────────────────

_settings_watcher = None


def watch_settings(callback: Callable[[dict], None]) -> None:
    """监听 settings.json 文件变化，变化时调用 callback。

    使用轮询方式检测文件修改时间变化（跨平台兼容）。
    """
    global _settings_watcher
    if _settings_watcher is not None:
        return  # 已在监听

    import asyncio

    last_mtime = 0.0
    if SETTINGS_FILE.exists():
        last_mtime = SETTINGS_FILE.stat().st_mtime

    async def _poll():
        nonlocal last_mtime
        while True:
            await asyncio.sleep(3)  # 每 3 秒检查一次
            try:
                if SETTINGS_FILE.exists():
                    current_mtime = SETTINGS_FILE.stat().st_mtime
                    if current_mtime != last_mtime:
                        last_mtime = current_mtime
                        # 局部导入避免与 room_handler 循环依赖
                        from handlers.room_handler import load_settings
                        settings = load_settings()
                        callback(settings)
                        _log.info("配置热更新: settings.json 已变更")
            except Exception as exc:
                _log.debug("配置监听异常: %s", exc)

    loop = asyncio.get_event_loop()
    _settings_watcher = loop.create_task(_poll())


def stop_watch_settings() -> None:
    """停止监听配置文件。"""
    global _settings_watcher
    if _settings_watcher is not None:
        _settings_watcher.cancel()
        _settings_watcher = None
