"""回归：save_settings 后设置缓存被毒化，load_settings 永久返回 None。

生产序列（2026-09-13 真机抓到，connect_room 因此全线崩溃）：
1. 前端发送 save_settings → handle_save_settings 调 save_settings(data)
   （内部 _atomic_write_json + 刷新 _settings_cache/_settings_cache_mtime）
2. handler 随后执行 `_settings_cache = None`（room_handler.handle_save_settings，
   原 5671 行）——只清缓存不清 mtime
3. 下一次 load_settings()：缓存为 None 跳过 TTL 快路径 → 走文件分支 →
   `mtime == _settings_cache_mtime` 成立（文件就是刚存的）→ 直接
   `return _settings_cache` = None，且永不自愈（mtime 不再变化）。

后果：save_settings 之后所有读设置的请求（connect_room 的 quality、预览画质、
导出配置…）全部拿到 None 而崩溃。
"""

from __future__ import annotations

import os
import sys

_backend_dir = os.path.join(os.path.dirname(__file__), "..", "python-backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from handlers import room_handler as rh


def _reset_cache(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(rh, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr(rh, "_settings_cache", None)
    monkeypatch.setattr(rh, "_settings_cache_mtime", 0.0)
    monkeypatch.setattr(rh, "_settings_cache_time", 0.0)


_VALID = {"output_dir": os.path.join(os.path.expanduser("~"), "LSC", "output"), "quality": "原画"}


def test_load_after_save_then_handler_wipe_returns_dict(monkeypatch, tmp_path) -> None:
    """生产事故序列：save → handler 置 None → load 必须返回 dict 而非 None。"""
    _reset_cache(monkeypatch, tmp_path)

    rh.save_settings(dict(_VALID))
    assert rh.load_settings().get("quality") == "原画"

    # handle_save_settings 的失效逻辑：只清缓存、不动 _settings_cache_mtime
    monkeypatch.setattr(rh, "_settings_cache", None)

    loaded = rh.load_settings()
    assert isinstance(loaded, dict), (
        "save_settings 后缓存被置 None，load_settings 走 mtime 相等分支返回 None"
    )
    assert loaded.get("quality") == "原画"


def test_cache_wipe_does_not_leak_across_mtime_unchanged_reads(monkeypatch, tmp_path) -> None:
    """连续多次 load（TTL 过期后重入 mtime 相等分支）必须持续返回 dict。"""
    _reset_cache(monkeypatch, tmp_path)

    rh.save_settings(dict(_VALID))
    monkeypatch.setattr(rh, "_settings_cache", None)
    monkeypatch.setattr(rh, "_settings_cache_time", 0.0)  # 强制 TTL 过期

    first = rh.load_settings()
    monkeypatch.setattr(rh, "_settings_cache_time", 0.0)
    second = rh.load_settings()
    assert isinstance(first, dict) and isinstance(second, dict)
    assert first.get("quality") == second.get("quality") == "原画"


def test_ttl_fast_path_still_returns_cached_dict(monkeypatch, tmp_path) -> None:
    """修复不得破坏 TTL 内快路径语义。"""
    _reset_cache(monkeypatch, tmp_path)

    rh.save_settings(dict(_VALID))
    cached = rh.load_settings()
    assert cached.get("quality") == "原画"
