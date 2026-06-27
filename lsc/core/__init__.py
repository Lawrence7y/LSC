"""LSC 核心领域层 — 纯业务逻辑，无 GUI 依赖。

此模块包含：
- 领域模型（models）：RecordingSession, Clip, ExportProfile 等
- 服务接口（services）：RecordingService, ExportService 等 Protocol
- 服务实现：RecordingServiceImpl, ExportServiceImpl 等

设计原则：
1. 核心层不依赖 PySide6 / Qt，便于单元测试和复用
2. 所有状态变更通过返回值或回调，不依赖 Qt 信号
3. 领域模型使用 dataclass，保持简单纯粹
"""

from lsc.core.models import (
    Clip,
    ExportOptions,
    RecordingSession,
    RecordingStatus,
    RoomInfo,
    StreamQuality,
)
from lsc.core.services.export_service import ExportService
from lsc.core.services.recording_service import RecordingService

__all__ = [
    "Clip",
    "ExportOptions",
    "ExportService",
    "RecordingService",
    "RecordingSession",
    "RecordingStatus",
    "RoomInfo",
    "StreamQuality",
]
