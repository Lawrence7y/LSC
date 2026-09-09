"""kill_process_tree 统一强杀入口单测（T-401）。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from lsc.utils import process_launcher as pl


class _FakeProc:
    def __init__(self) -> None:
        self.pid = 4242
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return None

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


def test_kill_process_tree_windows_uses_taskkill(monkeypatch):
    monkeypatch.setattr(pl, "_IS_WINDOWS", True)
    run = MagicMock()
    monkeypatch.setattr(pl.subprocess, "run", run)
    pl.kill_process_tree(SimpleNamespace(pid=99))
    run.assert_called_once()
    assert run.call_args.args[0] == ["taskkill", "/T", "/F", "/PID", "99"]


def test_kill_process_tree_posix_exits_within_grace(monkeypatch):
    """SIGTERM 后进程在宽限期内退出 → 不得升级 SIGKILL。"""
    monkeypatch.setattr(pl, "_IS_WINDOWS", False)
    proc = _FakeProc()
    polls = iter([None, 0])
    monkeypatch.setattr(proc, "poll", lambda: next(polls))
    monkeypatch.setattr(pl.time, "sleep", lambda s: None)
    pl.kill_process_tree(proc, grace_sec=1.0)
    assert proc.terminated and not proc.killed


def test_kill_process_tree_posix_escalates_to_kill(monkeypatch):
    monkeypatch.setattr(pl, "_IS_WINDOWS", False)
    proc = _FakeProc()
    monkeypatch.setattr(pl.time, "sleep", lambda s: None)
    pl.kill_process_tree(proc, grace_sec=0.02)
    assert proc.terminated and proc.killed


def test_kill_process_tree_noop_without_pid():
    run = MagicMock()
    with patch.object(pl.subprocess, "run", run):
        pl.kill_process_tree(SimpleNamespace(pid=None))
    run.assert_not_called()


def test_kill_process_tree_tolerates_taskkill_failure(monkeypatch):
    """taskkill 失败（进程已退出竞态）必须静默，不得抛异常打断停机路径。"""
    monkeypatch.setattr(pl, "_IS_WINDOWS", True)
    monkeypatch.setattr(
        pl.subprocess, "run", MagicMock(side_effect=OSError("process not found"))
    )
    pl.kill_process_tree(SimpleNamespace(pid=7))
