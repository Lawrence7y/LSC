"""Qt 桥接层 — 将核心服务包装为带 Qt 信号的适配器。

此模块是核心层（lsc/core）与 GUI 层（lsc/gui）之间的桥梁。
它将核心服务的函数回调转换为 Qt 信号，使 GUI 层可以使用
熟悉的 signal/slot 模式。

公共 API:
- QtRecordingService: Qt 友好的录制服务
- QtExportService: Qt 友好的导出服务
- RecordingConfig: 录制配置
- MultiRoomRecordingAdapter: 多房间录制管理器（高层封装）
- RoomRecordingState: 房间录制状态快照

使用示例::

    from lsc.core.qt import QtRecordingService, QtExportService

    recording_svc = QtRecordingService(self)
    recording_svc.session_started.connect(self.on_session_started)
    recording_svc.session_stopped.connect(self.on_session_stopped)

多房间使用示例::

    from lsc.core.qt import MultiRoomRecordingAdapter

    adapter = MultiRoomRecordingAdapter(self)
    adapter.start_recordings(rooms, output_dir)
    adapter.recording_started.connect(self.on_room_started)
"""

from lsc.core.qt.export import QtExportService
from lsc.core.qt.multi_room import MultiRoomRecordingAdapter, RoomRecordingState
from lsc.core.qt.recording import QtRecordingService, RecordingConfig

__all__ = [
    "MultiRoomRecordingAdapter",
    "QtExportService",
    "QtRecordingService",
    "RecordingConfig",
    "RoomRecordingState",
]
