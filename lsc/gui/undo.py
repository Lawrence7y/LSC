"""撤销/重做基础框架 —— Command 模式 UndoStack。

覆盖有破坏性的操作：删除房间、添加/删除/清空切片片段。
不追求全覆盖（普通选区调整、连接/录制等不可逆操作不纳入）。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    """一个可撤销/重做的操作。"""
    description: str
    undo: Callable[[], None]
    redo: Callable[[], None]


class UndoStack:
    """轻量级撤销/重做栈。

    - execute(cmd): 执行命令并压入 undo 栈
    - undo(): 撤销最近一步
    - redo(): 重做最近撤销的操作
    - clear(): 清空所有历史
    """

    def __init__(self, limit: int = 50):
        self._undo: list[Command] = []
        self._redo: list[Command] = []
        self._limit = limit

    def execute(self, cmd: Command) -> None:
        """执行命令并记录到撤销栈。"""
        cmd.redo()
        self._undo.append(cmd)
        self._redo.clear()
        if len(self._undo) > self._limit:
            self._undo.pop(0)

    def undo(self) -> bool:
        """撤销最近一步操作。返回 True 表示成功。"""
        if not self._undo:
            return False
        cmd = self._undo.pop()
        cmd.undo()
        self._redo.append(cmd)
        return True

    def redo(self) -> bool:
        """重做最近撤销的操作。返回 True 表示成功。"""
        if not self._redo:
            return False
        cmd = self._redo.pop()
        cmd.redo()
        self._undo.append(cmd)
        return True

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo_description(self) -> str:
        """返回下一步可撤销的操作描述。"""
        return self._undo[-1].description if self._undo else ""

    def redo_description(self) -> str:
        """返回下一步可重做的操作描述。"""
        return self._redo[-1].description if self._redo else ""

    def clear(self) -> None:
        """清空所有历史。"""
        self._undo.clear()
        self._redo.clear()
