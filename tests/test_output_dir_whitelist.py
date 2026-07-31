from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

_backend_dir = os.path.join(os.path.dirname(__file__), "..", "python-backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from handlers import room_handler as rh


@pytest.mark.parametrize(
    "path",
    [
        os.path.join(os.environ.get("SYSTEMROOT", r"C:\Windows"), "System32"),
        "/etc",
        "/etc/passwd",
        r"C:\Program Files",
    ],
)
def test_is_allowed_output_dir_rejects_system_paths(path: str) -> None:
    assert rh._is_allowed_output_dir(path) is False


def test_is_allowed_output_dir_allows_home_lsc_recordings() -> None:
    allowed = os.path.join(os.path.expanduser("~"), "LSC", "recordings")
    assert rh._is_allowed_output_dir(allowed) is True
    assert rh._is_allowed_output_dir("~/LSC/recordings") is True


@pytest.mark.parametrize(
    "path",
    [
        "D:\\desktop\\新建文件夹 (2)",
        "E:\\videos\\clips",
        "D:\\",
    ],
)
def test_is_allowed_output_dir_allows_user_data_drives(path: str) -> None:
    """用户数据盘（非系统盘）的导出目录必须合法，不得误伤。"""
    assert rh._is_allowed_output_dir(path) is True


def test_is_allowed_output_dir_rejects_empty_and_non_string() -> None:
    assert rh._is_allowed_output_dir("") is False
    assert rh._is_allowed_output_dir("   ") is False
    assert rh._is_allowed_output_dir(None) is False  # type: ignore[arg-type]


def test_save_settings_rejects_disallowed_output_dir(monkeypatch, tmp_path) -> None:
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(rh, "SETTINGS_FILE", str(settings_file))
    monkeypatch.setattr(rh, "_settings_cache", None)
    monkeypatch.setattr(rh, "_settings_cache_mtime", 0.0)
    monkeypatch.setattr(rh, "_settings_cache_time", 0.0)

    base = rh.load_settings()
    with pytest.raises(ValueError, match="导出目录不在允许范围内"):
        rh.save_settings({**base, "output_dir": r"C:\Windows\System32"})

    assert not settings_file.exists()


def test_save_settings_allows_home_lsc_recordings(monkeypatch, tmp_path) -> None:
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(rh, "SETTINGS_FILE", str(settings_file))
    monkeypatch.setattr(rh, "_settings_cache", None)
    monkeypatch.setattr(rh, "_settings_cache_mtime", 0.0)
    monkeypatch.setattr(rh, "_settings_cache_time", 0.0)

    allowed = os.path.join(os.path.expanduser("~"), "LSC", "recordings")
    base = rh.load_settings()
    rh.save_settings({**base, "output_dir": allowed})

    assert settings_file.is_file()
    assert rh.load_settings()["output_dir"] == allowed


@pytest.mark.asyncio
async def test_handle_save_settings_rejects_disallowed_without_writing(monkeypatch, tmp_path) -> None:
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(rh, "SETTINGS_FILE", str(settings_file))
    monkeypatch.setattr(rh, "_settings_cache", None)
    monkeypatch.setattr(rh, "_settings_cache_mtime", 0.0)
    monkeypatch.setattr(rh, "_settings_cache_time", 0.0)

    server = MagicMock()
    bridge = MagicMock()
    handlers: dict = {}

    def on(event_name):
        def decorator(fn):
            handlers[event_name] = fn
            return fn
        return decorator

    server.on = on
    rh.register_room_handlers(server, bridge)

    base = rh.load_settings()
    result = await handlers["save_settings"]({**base, "output_dir": r"C:\Windows\System32"})

    assert result == {"success": False, "error": "导出目录不在允许范围内"}
    assert not settings_file.exists()
