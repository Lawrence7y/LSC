"""次引导入口收敛守卫（2026-09-24 修复，CLAUDE.md §2.2 / §11.3）。

三条 WS 引导入口此前行为分叉：main.py 有日志/hook/端口打印/广播异常恢复，
server.py 的 __main__ 与 start.py 两个入口全部没有。start.py 的广播协程
一条未预期异常即永久停播，且它缺 logging 配置 ⇒ root logger 无 handler、
端口不打印 ⇒ Electron 拿 9876 兜底而它实际监听 19876，直连失败。
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(name: str) -> str:
    return (ROOT / "python-backend" / name).read_text(encoding="utf-8")


def test_all_bootstrap_entries_forward_broadcast_seq() -> None:
    """三个入口都必须透传 _seq/_coalesced（缺任何一个 ⇒ 缺号告警在该入口空转）。"""
    for name in ("main.py", "server.py", "start.py"):
        src = _read(name)
        assert "seq=msg.get('_seq')" in src, name
        assert "coalesced=msg.get('_coalesced')" in src, name


def test_secondary_entries_have_broadcast_loop_recovery() -> None:
    """广播协程一条异常不得永久停播（main.py 主入口同款兜底）。"""
    assert "broadcast error, retrying in 1s" in _read("main.py")
    assert "broadcast error in __main__ entry, retrying in 1s" in _read("server.py")
    assert "broadcast error, retrying in 1s" in _read("start.py")


def test_start_entry_converges_to_main_infrastructure() -> None:
    """start.py 必须复用主入口的日志配置与 excepthook，并打印端口。"""
    src = _read("start.py")
    assert "from main import _install_exception_hook, _setup_logging" in src
    # 前端靠 Electron 正则匹配这行取端口（CLAUDE.md §2.2）
    assert "WebSocket server ready at ws://127.0.0.1:" in src
    # ws 线程的 run_until_complete 被 loop.stop 打断时要留痕而非静默死亡
    assert "ws thread event loop stopped unexpectedly" in src


def test_server_main_shutdown_finally_is_observable() -> None:
    """停机 finally 的清理失败必须留痕：rooms.json 未落盘的取证死角。"""
    src = _read("server.py")
    assert '_log.exception("manager shutdown failed")' in src
    assert "flush_pending_room_saves raised: rooms.json 未落盘" in src
