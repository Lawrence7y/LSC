"""`save_rooms`（rooms.json 第二条写入口）的校验行为测试。

修前的缺陷：它只查"是不是字符串"，于是
① 一次批量保存可以写入任意多个房间（绕过 MAX_ROOMS=12 这条产品硬限制）；
② 脏 URL（超长 / 带控制字符 / 非 http(s)）能绕过 `add_room` 的全部约束直接落盘。
判据现在与单条入口共用 `_room_url_shape`，本测试守的就是"两条入口同源"这件事。
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_backend_dir = os.path.join(ROOT, "python-backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
_tests_dir = os.path.dirname(__file__)
if _tests_dir not in sys.path:
    sys.path.insert(0, _tests_dir)

from handlers import room_handler  # noqa: E402
from test_room_handler_lifecycle import _FakeBridge, _FakeManager, _FakeServer  # noqa: E402


def _make_handler(monkeypatch):
    """返回 (dispatch, written)：dispatch 直接收 save_rooms 的原始载荷 dict。"""
    written: list[list] = []
    monkeypatch.setattr(
        room_handler, "save_rooms",
        lambda rooms, *a, **k: written.append(list(rooms)) or True,
    )
    server = _FakeServer()
    bridge = _FakeBridge(_FakeManager([]))
    room_handler.register_room_handlers(server, bridge)
    handler = server.handlers["save_rooms"]

    def dispatch(payload):
        return asyncio.run(handler(payload))

    return dispatch, written


def _room(index: int) -> dict:
    return {"room_id": f"r{index}", "room_url": f"https://www.huya.com/{100000 + index}"}


def test_房间数超过上限必须整批拒绝(monkeypatch):
    dispatch, written = _make_handler(monkeypatch)
    result = dispatch({"rooms": [_room(i) for i in range(13)]})
    assert result["success"] is False
    assert "上限" in result["error"]
    assert written == [], "越界的批量保存一个字节都不该落盘"


def test_上限内的批量保存照常通过(monkeypatch):
    dispatch, written = _make_handler(monkeypatch)
    result = dispatch({"rooms": [_room(i) for i in range(12)]})
    assert result["success"] is True
    assert len(written) == 1 and len(written[0]) == 12


def test_脏URL被拒且定位到具体房间(monkeypatch):
    dispatch, written = _make_handler(monkeypatch)
    bad = {"room_id": "r9", "room_url": "javascript:alert(1)"}
    result = dispatch({"rooms": [_room(1), bad]})
    assert result["success"] is False
    assert "第 2 个房间" in result["error"]
    assert "http" in result["error"]
    assert written == []


def test_超长URL批量与单条入口判据一致(monkeypatch):
    """两条入口必须复用同一份 `_room_url_shape`，不能再各养一套规则。"""
    dispatch, _ = _make_handler(monkeypatch)
    huge = "https://a.com/" + "x" * 3000
    assert dispatch({"rooms": [{"room_id": "r1", "room_url": huge}]})["success"] is False
    single = room_handler._validate_room_url_candidate(huge)
    assert single.get("valid") is not True, "单条入口也应判为链接非法"


def test_room_id_为空必须被拒(monkeypatch):
    dispatch, written = _make_handler(monkeypatch)
    result = dispatch({"rooms": [{"room_id": "   ", "room_url": "https://www.huya.com/123"}]})
    assert result["success"] is False
    assert "room_id" in result["error"]
    assert written == []


def test_非列表载荷仍被拒(monkeypatch):
    dispatch, written = _make_handler(monkeypatch)
    assert dispatch({"rooms": "not-a-list"})["success"] is False
    assert written == []


# ────────────────── 持久化层自身的形状闸（§11.32）──────────────────
# `handle_save_rooms`（WebSocket 入口）校验得很严，但**内部自动保存路径**
# （controller 状态变化 → `schedule_save_rooms` → `save_rooms`）此前不查形状：
# 一旦上游吐出非 dict 条目或缺 `room_id` 的条目，就会被原样写进 `rooms.json`，
# 下次启动直接读不回房间。这里把闸口下沉到持久化层。
def test_save_rooms_rejects_malformed_payload_and_writes_nothing(tmp_path):
    from persistence import load_rooms, save_rooms

    target = tmp_path / "rooms.json"
    good = [{"room_id": "r1", "room_url": "https://www.huya.com/1"}]
    assert save_rooms(good, target) is True
    assert load_rooms(target) == good

    for bad in (
        {},                                    # 空 dict 也是可迭代的非列表载荷
        "not-a-list",
        [{}],                                  # 三个身份字段全无
        [{"room_id": "  ", "room_url": "  ", "url": "  "}],  # 身份字段全是空白
        [{"room_id": "r1"}, "junk"],           # 混入非对象条目
        [None],
    ):
        assert save_rooms(bad, target) is False, f"应拒绝: {bad!r}"
    # 关键：拒绝必须**整次不落盘**，且原文件保持完好（不是写坏再修）
    assert load_rooms(target) == good


def test_save_rooms_accepts_both_legacy_identity_shapes(tmp_path):
    """形状闸门不得比持久层真实契约更严。

    rooms.json 有两种历史写法：orchestrator 落 `url`，handler 快照落 `room_url`；
    重启恢复（room_handler._restore_rooms_from_disk）只读这两个字段。
    闸门若只认 room_id/room_url，就会把老的 orchestrator 自动保存整次拒掉。
    """
    from persistence import load_rooms, save_rooms

    target = tmp_path / "rooms.json"
    rooms = [
        {"url": "https://example.com/a.m3u8", "platform": "direct"},
        {"room_url": "https://www.huya.com/2"},
        {"room_id": "r3", "room_url": "https://www.douyu.com/3"},
    ]
    assert save_rooms(rooms, target) is True
    assert load_rooms(target) == rooms


def test_save_rooms_never_silently_drops_entries(tmp_path):
    """脏条目要导致整次写入失败，而不是被悄悄丢掉几条后「成功」。"""
    from persistence import save_rooms

    target = tmp_path / "rooms.json"
    assert save_rooms([{"room_id": "r1"}, {"note": "无身份"}], target) is False
    assert not target.exists()


def test_scheduled_save_path_is_gated_too(tmp_path):
    """自动保存走 `schedule_save_rooms` → 定时器 → `save_rooms`，同样要受闸。

    这里的时间等待只是「等到落盘发生」的条件轮询（§11.30 的第①类），
    断言本身不依赖挂钟长度：违规载荷后文件内容必须仍是上一次的好数据。
    """
    from persistence import load_rooms, schedule_save_rooms

    target = tmp_path / "rooms.json"
    good = [{"room_id": "r1", "room_url": "https://www.huya.com/1"}]
    try:
        schedule_save_rooms(good, target, delay_sec=0.01)
        deadline = time.monotonic() + 5.0
        while not target.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert load_rooms(target) == good, "正常载荷应照常落盘"

        assert schedule_save_rooms([{"note": "无身份字段"}], target, delay_sec=0.01) is None
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            # 定时器已触发过（违规被拒）⇒ 文件仍是上一次的好数据
            if load_rooms(target) == good and not (tmp_path / "rooms.json.tmp").exists():
                break
            time.sleep(0.01)
        assert load_rooms(target) == good, "违规载荷不得覆盖已有好文件"
    finally:
        if persistence_state_timer() is not None:
            persistence_state_timer().cancel()


def persistence_state_timer():
    import persistence
    return persistence._save_rooms_timer
