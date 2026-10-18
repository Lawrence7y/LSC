"""中央派生账 note_child/forget_child 配对守卫（CLAUDE.md §7.4，2026-09-24 修复）。

生产默认 shared_ingest_enabled=True：修复前 shared_ingest 从不
note_child、capture 正常 stop() 从不 forget_child ⇒ budget_snapshot 的
live_total 对生产主体失明、MAX_LIVE_CHILDREN 软上限告警成死代码，
capture 路径每停一次还泄漏一条记录。本文件锁住两条链路的登记/注销配对。
"""
from __future__ import annotations

from unittest.mock import MagicMock

from lsc.utils.process_launcher import budget_snapshot, forget_child, note_child


def _live_by_category() -> dict:
    return budget_snapshot()["live_by_category"]


def test_shared_ingest_launch_registers_and_teardown_unregisters(monkeypatch) -> None:
    """shared_ingest._launch_process 必须登记，_terminate_process_object 必须注销。"""
    from lsc.core.services import shared_ingest as si

    class _FakePopen:
        pid = 4242
        stdin = None

        def __init__(self, *_args, **_kwargs) -> None:
            return None

    monkeypatch.setattr(si.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(si, "prepare_launch", lambda _path: ({}, 0, None))

    ingest = si.SharedRoomIngest.__new__(si.SharedRoomIngest)
    ingest.room_id = "room-budget-test"

    before = _live_by_category().get("shared_ingest", 0)
    proc = ingest._launch_process(["ffmpeg", "-version"])
    assert _live_by_category().get("shared_ingest", 0) == before + 1

    ingest._terminate_process_object(proc)
    assert _live_by_category().get("shared_ingest", 0) == before


def test_capture_stop_unregisters_the_noted_child(sample_config, tmp_path) -> None:
    """legacy capture 正常 stop() 必须注销 start() 时登记的进程（旧实现只在
    _force_kill 注销 ⇒ 每停一次泄漏一条记录，长期运行计数虚高）。"""
    from lsc.recorder.capture import CaptureStatus, StreamCapture

    capture = StreamCapture(sample_config)
    proc = MagicMock()
    proc.poll.return_value = 0  # 已退出：stop() 的三级停止立即收敛
    proc.pid = 777
    output = tmp_path / "rec.mp4"
    output.write_bytes(b"\x00\x00\x00\x18ftypmp42")  # 让 stop() 末尾的 isfile 检查通过

    capture._status = CaptureStatus.RECORDING
    capture._process = proc
    capture._output_path = str(output)

    # 模拟 start() 成功后的记账（真正 start 需要真实 FFmpeg）
    note_child(proc, "record")
    before = _live_by_category().get("record", 0)
    assert before >= 1

    result = capture.stop()

    assert result.success
    assert _live_by_category().get("record", 0) == before - 1


def test_capture_crash_paths_unregister_the_noted_child(sample_config) -> None:
    """check_health / check_and_handle_crash 检测到进程退出时同样要注销。"""
    from lsc.recorder.capture import CaptureStatus, StreamCapture

    for probe in ("check_health", "check_and_handle_crash"):
        capture = StreamCapture(sample_config)
        proc = MagicMock()
        proc.poll.return_value = 3  # 已崩溃退出
        proc.returncode = 3
        proc.pid = 778
        capture._status = CaptureStatus.RECORDING
        capture._process = proc
        capture._output_path = "rec.mp4"
        capture._last_file_size = 0
        capture._stderr_tail = []
        capture._stderr_reader_started = True

        note_child(proc, "record")
        before = _live_by_category().get("record", 0)

        getattr(capture, probe)()

        assert _live_by_category().get("record", 0) == before - 1, probe


def test_forget_child_is_idempotent_and_survives_unknown_pid() -> None:
    """重复注销 / 注销未登记的对象都必须静默无害（pid 复用窗口的防护）。"""
    forget_child(MagicMock(pid=999_999))
    ghost = MagicMock(pid=999_998)
    note_child(ghost, "test-ghost")
    forget_child(ghost)
    forget_child(ghost)  # 第二次：pop 不到条目也不得抛
    assert "test-ghost" not in _live_by_category()
    # 没有 pid 属性的对象直接放过
    forget_child(object())
