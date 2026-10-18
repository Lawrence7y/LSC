"""批量录制收尾守卫（2026-09-24 修复）。

`_BatchRecordJob.run` 的收尾 submit 在邮箱满时抛 RuntimeError：旧实现会让
整个 job 死在这里 ⇒ `batch_record_finished` 永不发出（前端批量录制永远停在
「进行中」）、`_batch_record_job` 残留还会让后续所有批量启动被拒。
"""
from __future__ import annotations

import time

import pytest

from lsc.core.orchestrator import RoomOrchestrator, _BatchRecordJob


@pytest.fixture
def manager(monkeypatch):
    orch = RoomOrchestrator(controller_factory=lambda: object())
    monkeypatch.setattr(orch, "save_rooms", lambda: 0)
    monkeypatch.setattr(orch, "start_recording", lambda *_a, **_k: True)
    return orch


def test_job_clears_handle_and_survives_full_mailbox(manager) -> None:
    """submit 全部失败（邮箱满）时 job 不得抛穿，且必须清理作业句柄。"""
    manager._batch_record_job = object()  # 占用句柄：验证被无条件清理

    def _boom(*_args, **_kwargs):
        raise RuntimeError("command queue full")

    manager.submit = _boom  # type: ignore[method-assign]

    job = _BatchRecordJob(manager, ["r1", "r2"], "/tmp/out", "libx264", 23)
    job.run()  # 不得抛穿

    assert manager._batch_record_job is None


def test_job_emits_finished_with_partial_count_on_enqueue_failure(manager) -> None:
    """入队阶段失败（池已关）时 finished 照发，计数为已完成部分。"""
    finished: list[tuple[int, int]] = []
    manager.bus.subscribe("batch_record_finished", lambda started, total: finished.append((started, total)))
    manager._batch_record_job = object()
    manager.start()  # 编排线程消费 submit 队列，batch_record_finished 才到得了订阅者

    real_submit = manager.submit
    fail_once = {"done": False}

    def _flaky_submit(fn, *_args, **_kwargs):
        # 第一次进度上报失败一次，其余放行：验证进度失败不妨碍终态
        if not fail_once["done"]:
            fail_once["done"] = True
            raise RuntimeError("command queue full")
        return real_submit(fn, *_args, **_kwargs)

    manager.submit = _flaky_submit  # type: ignore[method-assign]

    job = _BatchRecordJob(manager, ["r1"], "/tmp/out", "libx264", 23)
    job.run()

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not finished:
        time.sleep(0.02)

    assert finished == [(1, 1)]
    assert manager._batch_record_job is None
