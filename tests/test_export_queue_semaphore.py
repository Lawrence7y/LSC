"""导出队列 semaphore 热更新不得依赖 Semaphore._waiters（空闲时可为 None）。"""
from __future__ import annotations

import asyncio
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_ensure_export_queue_does_not_touch_waiters_len() -> None:
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    ensure_body = source.split("async def _ensure_export_queue(", 1)[1].split("async def ", 1)[0]
    assert "_waiters.__len__" not in ensure_body
    assert "len(_export_semaphore._waiters)" not in ensure_body
    assert "_export_semaphore_limit" in ensure_body
    assert "_export_semaphore_limit != desired" in ensure_body


def test_asyncio_semaphore_waiters_may_be_none() -> None:
    """Python 3.10+：无等待者时 _waiters 为 None，直接 __len__ 会炸。"""
    sem = asyncio.Semaphore(2)
    assert sem._waiters is None
    try:
        sem._waiters.__len__()
        raised = False
    except AttributeError as exc:
        raised = True
        assert "__len__" in str(exc)
    assert raised
