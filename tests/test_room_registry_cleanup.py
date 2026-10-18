"""删房时按房间登记的账本必须被注销（此前只增不减）。

缺陷：`remove_room` 过去只清权威快照与预览 registry，从不调用
`_reset_epoch_scoped_clip_state`，于是

* `_manual_clip_counters` / `_ai_clip_counters` 按裸 room_id 增长且全仓无 pop/clear；
* `_refined_round_keys` / `_listed_clip_ids` / `_deleted_clip_keys` 等按
  `room:round_key` 登记的集合同样残留。

后果不只是内存：重新加入同名房间会继承上一场的"已删除 tombstone / 已入列键"，
使新回合的 `clip_queued` 被当成重复而吞掉；长挂机 + 反复增删则无界累积。
"""
from __future__ import annotations

import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_backend_dir = os.path.join(ROOT, "python-backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
_tests_dir = os.path.dirname(__file__)
if _tests_dir not in sys.path:
    sys.path.insert(0, _tests_dir)

from handlers import room_handler  # noqa: E402
from test_room_handler_lifecycle import _FakeBridge, _FakeManager, _FakeServer  # noqa: E402


class _Manager(_FakeManager):
    """共享夹具不管 remove_room；这里只补上被处理器调用到的那一个方法。"""

    def remove_room(self, room_id):  # noqa: D102
        return True


def _seed_and_remove(room_id: str, keep_id: str = "room-keep"):
    """注册 handler、给两个房间铺满账本键，移除 `room_id`，返回存活快照。"""
    server = _FakeServer()
    bridge = _FakeBridge(_Manager([]))
    room_handler.register_room_handlers(server, bridge)
    handler = server.handlers["remove_room"]
    def reg(name):
        return _registry(handler, name)

    for rid in (room_id, keep_id):
        reg("_listed_clip_ids")[f"{rid}:round-000001"] = None
        reg("_refined_round_keys").add(f"{rid}:round-000001")
        reg("_deleted_clip_keys")[f"{rid}:round-000002"] = None
        reg("_manual_clip_counters")[rid] = 7
        reg("_ai_clip_counters")[rid] = 9

    asyncio.run(handler({"room_id": room_id}))

    return {
        "listed": set(reg("_listed_clip_ids")),
        "refined": set(reg("_refined_round_keys")),
        "deleted": set(reg("_deleted_clip_keys")),
        "manual": dict(reg("_manual_clip_counters")),
        "ai": dict(reg("_ai_clip_counters")),
    }


def _registry(handler, name: str):
    """取一个共享注册表：闭包变量优先，回落到模块级全局。

    `_refined_round_keys` / `_listed_clip_ids` / `_deleted_clip_keys` 是
    `register_room_handlers` 的闭包变量（且被嵌套函数层层转捕），
    `_manual_clip_counters` / `_ai_clip_counters` 是模块级全局。
    两类都要能拿到，断言才有意义。
    """
    found: dict[str, object] = {}
    queue = [handler]
    while queue:
        fn = queue.pop()
        code = getattr(fn, "__code__", None)
        if code is None:
            continue
        for cell_name, cell in zip(code.co_freevars, getattr(fn, "__closure__", None) or (), strict=False):
            try:
                value = cell.cell_contents
            except ValueError:
                continue
            if cell_name not in found:
                found[cell_name] = value
            if callable(value) and hasattr(value, "__closure__"):
                queue.append(value)
    if name in found:
        return found[name]
    return getattr(room_handler, name)


def test_移除房间后其账本被注销():
    alive = _seed_and_remove("room-gone")
    assert not any(k.startswith("room-gone:") for k in alive["listed"]), alive["listed"]
    assert not any(k.startswith("room-gone:") for k in alive["refined"]), alive["refined"]
    assert not any(k.startswith("room-gone:") for k in alive["deleted"]), alive["deleted"]
    assert "room-gone" not in alive["manual"]
    assert "room-gone" not in alive["ai"]


def test_不得误伤其它房间的账本():
    """清理必须是 room 作用域的：并发分析时其它房间的冻结边界与入列键要留着。"""
    alive = _seed_and_remove("room-gone")
    assert "room-keep:round-000001" in alive["listed"]
    assert "room-keep:round-000001" in alive["refined"]
    assert alive["manual"].get("room-keep") == 7
    assert alive["ai"].get("room-keep") == 9
