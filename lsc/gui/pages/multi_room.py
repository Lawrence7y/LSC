"""多房间工作台页面 — 包含卡片网格、详情面板、控制栏和状态栏。

This module re-exports MultiRoomPage for backward compatibility.
The actual implementation is in lsc.gui.pages.multi_room.page.
"""
from lsc.gui.pages.multi_room.page import MultiRoomPage

__all__ = ["MultiRoomPage"]
