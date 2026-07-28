"""多房间工作台会话模型（向后兼容重新导出）。

RoomSession 已迁移至 lsc.core.session（核心领域层）。
此模块保留重新导出，避免旧代码断裂。新代码请直接从 lsc.core.session 导入。
"""
from lsc.core.session import RoomSession

__all__ = ["RoomSession"]
