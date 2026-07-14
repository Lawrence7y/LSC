"""settings.json shared_ingest_enabled 必须同步到运行时 LscConfig。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "python-backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def test_apply_shared_ingest_from_settings_mutates_runtime_config():
    from lsc.config import load_config, reset_config

    reset_config()
    from handlers.room_handler import _apply_shared_ingest_from_settings

    _apply_shared_ingest_from_settings({"shared_ingest_enabled": False})
    assert load_config().shared_ingest_enabled is False

    _apply_shared_ingest_from_settings({"shared_ingest_enabled": True})
    assert load_config().shared_ingest_enabled is True


def test_apply_shared_ingest_noop_when_key_missing():
    from lsc.config import load_config, reset_config

    reset_config()
    before = bool(load_config().shared_ingest_enabled)
    from handlers.room_handler import _apply_shared_ingest_from_settings

    _apply_shared_ingest_from_settings({"output_dir": "/tmp"})
    assert bool(load_config().shared_ingest_enabled) is before
