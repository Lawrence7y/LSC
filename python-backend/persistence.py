"""房间配置持久化。

将前端传入的房间列表以 JSON 形式保存到本地，程序启动时读取并推送给前端。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# 默认数据目录：项目根目录下的 data 文件夹
# 使用项目内目录，避免沙箱外路径导致写入失败
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = _PROJECT_ROOT / "data"
ROOMS_FILE = DEFAULT_DATA_DIR / "rooms.json"


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def load_rooms(path: Path | str | None = None) -> list[dict[str, Any]]:
    """从 JSON 文件读取已保存的房间列表。

    支持两种格式：
    - {"rooms": [...]}
    - [...]

    文件不存在或解析失败时返回空列表。
    """
    file_path = Path(path) if path else ROOMS_FILE
    if not file_path.exists():
        return []

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []

    if isinstance(data, dict):
        rooms = data.get("rooms", [])
        return rooms if isinstance(rooms, list) else []
    if isinstance(data, list):
        return data
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
        return True
    except Exception:
        return False
