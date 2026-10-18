"""全局派生闸（CLAUDE.md §7.4）的行为测试。

守的是四件事：名额真的封顶、异常路径真的归还、长生命周期站点拿不到名额时按各自
失败路径走、**短命令（分析热路径）绝不因为这道闸被拖慢**。
最后一条是 2026-09-19 的现场教训：`run_hidden` 曾被包进闸，一次几百秒的抽帧把
2 个名额占死，后面每个短探测各白等 5s，持续分析吞吐从 0.93× 掉到 0.42×。
"""
from __future__ import annotations

import subprocess
import threading
import time
from types import SimpleNamespace

import pytest

from lsc.utils import process_launcher as pl


@pytest.fixture(autouse=True)
def _reset_gate(monkeypatch):
    """每个用例一套独立的闸与账本，避免用例间串味。"""
    monkeypatch.setattr(pl, "_spawn_gate", threading.Semaphore(2))
    monkeypatch.setattr(pl, "_live_children", {}, raising=False)
    monkeypatch.setattr(pl, "_inflight_spawns", 0, raising=False)
    monkeypatch.setattr(pl, "_rejected_spawn_total", 0, raising=False)
    monkeypatch.setattr(pl, "_failopen_total", 0, raising=False)
    monkeypatch.setattr(pl, "_transient_spawn_total", 0, raising=False)
    monkeypatch.setattr(pl, "_transient_by_category", {}, raising=False)
    yield


def test_并发占用不超过名额():
    peak = 0
    active = 0
    lock = threading.Lock()

    def worker():
        nonlocal peak, active
        with pl.spawn_slot("test", timeout_sec=5.0):
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            with lock:
                active -= 1

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak <= 2, f"名额只有 2，却同时有 {peak} 个在派生"
    assert pl.budget_snapshot()["inflight_spawns"] == 0


def test_拿不到名额就抛并计数():
    with pl.spawn_slot("hold-a", timeout_sec=1.0), pl.spawn_slot("hold-b", timeout_sec=1.0):
        started = time.monotonic()
        with pytest.raises(pl.SpawnBudgetExhausted), pl.spawn_slot("third", timeout_sec=0.3):
            pass
        assert time.monotonic() - started >= 0.3
    snap = pl.budget_snapshot()
    assert snap["rejected_spawn_total"] == 1
    # 抛出去之前占用的两个名额必须还在（未被误减）
    assert snap["inflight_spawns"] == 0


def test_块内异常也必须归还名额():
    with pytest.raises(RuntimeError, match="boom"), pl.spawn_slot("preview", timeout_sec=1.0):
        raise RuntimeError("boom")
    assert pl.budget_snapshot()["inflight_spawns"] == 0
    # 异常路径归还后，两个名额必须能再次同时取得（漏放一个就会在这里卡住）
    with pl.spawn_slot("a", timeout_sec=1.0), pl.spawn_slot("b", timeout_sec=1.0):
        assert pl.budget_snapshot()["inflight_spawns"] == 2


def test_存活记账按分类且可被注销():
    class _Child:
        def __init__(self, pid):
            self.pid = pid

    pl.note_child(_Child(101), "preview")
    pl.note_child(_Child(102), "preview")
    proc = _Child(103)
    pl.note_child(proc, "record")
    snap = pl.budget_snapshot()
    assert snap["live_total"] == 3
    assert snap["live_by_category"] == {"preview": 2, "record": 1}

    pl.forget_child(proc)
    assert pl.budget_snapshot()["live_by_category"] == {"preview": 2}


def test_无_pid_对象不得污染账本():
    pl.note_child(object(), "preview")
    pl.forget_child(None)
    assert pl.budget_snapshot()["live_total"] == 0


def test_run_hidden_根本不碰派生闸(monkeypatch):
    """短命令不得进入名额竞争：一次几百秒的抽帧会把 2 个名额占死。

    这是 2026-09-19 现场回归的根因（持续分析吞吐 0.93× → 0.42×、3 次 270s 扫描超时），
    所以断言的是"**从未 acquire 过闸**"，而不是"acquire 不到也放行"。
    """
    acquires: list[float | None] = []

    class _RecordingGate:
        def acquire(self, timeout=None):  # noqa: ANN001, ARG002
            acquires.append(timeout)
            return False

        def release(self):
            acquires.append("released")

    monkeypatch.setattr(pl, "_spawn_gate", _RecordingGate())
    calls: list[object] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(pl.subprocess, "run", fake_run)
    pl.run_hidden(["ffmpeg", "-version"])

    assert len(calls) == 1
    assert acquires == [], "run_hidden 不该再向全局闸要名额"
    # 没走闸 ⇒ 也不该产生 fail-open 留痕（那是闸路径的计数）
    assert pl.budget_snapshot()["failopen_total"] == 0


def test_run_hidden_闸被占满时也不阻塞并且有计数(monkeypatch):
    """名额全被长生命周期进程占着时，短命令必须照常跑完（不等 5s 预算）。"""
    monkeypatch.setattr(pl, "_spawn_gate", threading.Semaphore(0))

    def fake_run(cmd, **kwargs):
        time.sleep(0.05)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(pl.subprocess, "run", fake_run)

    started = time.monotonic()
    for _ in range(6):
        pl.run_hidden(["ffprobe", "-v", "error"])
    elapsed = time.monotonic() - started

    # 6 × 0.05s = 0.3s 量级；旧实现是每次等满 5s 才 fail-open（≈30s）
    assert elapsed < 1.0, f"短命令被闸拖慢：{elapsed:.2f}s"
    snapshot = pl.budget_snapshot()
    assert snapshot["transient_spawn_total"] == 6
    assert snapshot["transient_by_category"]["transient"] == 6


def test_run_hidden_按调用方分类计数(monkeypatch):
    """`spawn_category` 仍要被消费掉（不能漏进 subprocess kwargs）并按类记账。"""

    seen_kwargs: list[dict] = []

    def fake_run(cmd, **kwargs):
        seen_kwargs.append(kwargs)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(pl.subprocess, "run", fake_run)
    pl.run_hidden(["ffmpeg", "-i", "x"], spawn_category="ocr")

    assert all("spawn_category" not in kw for kw in seen_kwargs)
    assert pl.budget_snapshot()["transient_by_category"]["ocr"] == 1


def test_进样重启在闸满时削峰而不否决(monkeypatch):
    """录制进样的启动/换线路径必须 `fail_open=True`。

    闸自己的规矩：拿不到名额就不起流 = 让用户丢一整场数据，比多等一个名额贵得多。
    现场见过硬拒之后落成 `upstream ffmpeg start failed` + `failure=TIMESTAMP_DISCONTINUITY`。
    """
    import contextlib

    from lsc.core.services import shared_ingest as si

    recorded: dict[str, object] = {}

    @contextlib.contextmanager
    def fake_slot(category: str, *, timeout_sec: float = 30.0, fail_open: bool = False):
        recorded.update(category=category, timeout_sec=timeout_sec, fail_open=fail_open)
        yield

    class _FakeProc:
        stdin = object()

    monkeypatch.setattr(si, "spawn_slot", fake_slot)
    monkeypatch.setattr(si, "prepare_launch", lambda path: ({}, 0, None))
    monkeypatch.setattr(si.subprocess, "Popen", lambda *args, **kwargs: _FakeProc())
    monkeypatch.setattr(
        "lsc.utils.process_launcher.set_stream_nonblocking",
        lambda stream: None,
    )

    self = SimpleNamespace(room_id="r1")
    si.SharedRoomIngest._launch_process(self, ["ffmpeg", "-hide_banner"])  # type: ignore[arg-type]

    assert recorded["category"] == "shared_ingest:r1"
    assert recorded["fail_open"] is True, "进样重启不能被闸否决"
    # 等待预算仍要明显小于 orchestrator.call 的 10s，否则会把整条 call 通道拖超时
    assert float(recorded["timeout_sec"]) < 10.0


def test_fail_open_放行但不归还未获取的名额(monkeypatch):
    """等不到名额时若照样 release，闸会越放越宽 —— 这比不封顶更危险。

    capture / 进样重启这类"启动失败就丢数据"的路径用 fail_open：宁可放行也不因
    我们的闸丢一场录制。因此必须同时保证：① 不抛；② 计数留痕；
    ③ **绝不 release 一个没拿到的名额**。
    """
    monkeypatch.setattr(pl, "_spawn_gate", threading.Semaphore(0))  # 名额全被占满
    entered: list[str] = []
    with pl.spawn_slot("record", timeout_sec=0.2, fail_open=True):
        entered.append("held")
    assert entered == ["held"], "fail_open 必须放行，录制不该被我们的闸拒绝"
    assert pl.budget_snapshot()["failopen_total"] == 1

    # 关键反证：这次没有持有名额，所以信号量仍是 0，而不是被误 release 成 1
    assert pl._spawn_gate.acquire(timeout=0.1) is False,         "fail_open 路径误 release，闸已被放宽"


def test_硬拒绝路径仍然抛异常给可重试的调用方(monkeypatch):
    """预览这类可重试站点必须拿到异常，否则它以为启动成功了。"""
    monkeypatch.setattr(pl, "_spawn_gate", threading.Semaphore(0))
    with pytest.raises(pl.SpawnBudgetExhausted), pl.spawn_slot("preview", timeout_sec=0.2):
        pass
    assert pl.budget_snapshot()["rejected_spawn_total"] == 1
