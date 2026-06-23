# 全局心跳优化总结

## 优化概述

本次优化针对 `MultiRoomManager` 的全局心跳机制进行了改进，通过分层频率控制和智能 UI 更新策略，减少了不必要的计算和 UI 刷新开销。

## 改进点

### 1. 分层心跳机制

**文件**: `lsc/gui/multi_room/manager.py`

将原来每秒执行所有操作的单一心跳，改为三个频率层级：

| 频率 | 间隔 | 操作 |
|------|------|------|
| 高频 | 1秒 | 录制时长更新、播放位置同步 |
| 中频 | 5秒 | 文件大小查询、FFmpeg 健康检查 |
| 低频 | 30秒 | 磁盘空间检查 |

**关键代码**:
```python
# 新增常量
_HIGH_FREQ_INTERVAL = 1
_MEDIUM_FREQ_INTERVAL = 5
_LOW_FREQ_INTERVAL = 30

# 心跳计数器
self._tick_counter: int = 0
```

### 2. Dirty Flag 机制

在状态变化时设置标记，避免重复的 UI 刷新：

```python
self._dirty_recording: bool = False
self._dirty_connection: bool = False
```

在以下位置设置 dirty flag：
- `start_recording()` - 录制状态变化
- `stop_recording()` - 录制状态变化
- `_apply_stream_info()` - 连接状态变化

### 3. UI 层智能更新

**文件**: `lsc/gui/pages/multi_room.py`

#### 3.1 位置缓存

添加位置缓存字典，仅当播放位置变化超过阈值时更新 UI：

```python
self._last_positions: dict[str, float] = {}
self._POSITION_THRESHOLD = 0.5  # 秒
```

#### 3.2 按需更新

仅更新正在录制或预览的房间卡片时间线，跳过空闲房间：

```python
for room_id, room in self._manager._rooms.items():
    if not (room.is_recording or room.preview_enabled):
        continue
    # Throttle: only update if position changed significantly
    position = self._manager.get_preview_position(room_id)
    last_pos = self._last_positions.get(room_id, -1)
    if abs(position - last_pos) >= self._POSITION_THRESHOLD:
        self._update_card_timeline(room_id)
        self._last_positions[room_id] = position
```

#### 3.3 缓存清理

在房间移除时清理位置缓存：

```python
def _do_remove() -> None:
    # ... existing code ...
    # Clean up position cache
    self._last_positions.pop(room_id, None)
```

## 性能测试结果

```
Rooms: 4
  High-freq only: 17.03 ms
  Full tick (old): 41.15 ms
  Speedup: 2.42x

Rooms: 8
  High-freq only: 25.12 ms
  Full tick (old): 53.34 ms
  Speedup: 2.12x

Rooms: 12
  High-freq only: 74.12 ms
  Full tick (old): 87.01 ms
  Speedup: 1.17x
```

## 测试验证

所有现有测试通过：
- `tests/test_multi_room_manager.py` - 10 个测试
- `tests/gui/test_multi_room_page.py` - 27 个测试

## 修改文件清单

1. `lsc/gui/multi_room/manager.py`
   - 添加心跳间隔常量
   - 添加计数器和 dirty flag
   - 实现分层心跳逻辑

2. `lsc/gui/pages/multi_room.py`
   - 添加位置缓存字典
   - 优化 `_on_global_tick` 方法
   - 添加缓存清理逻辑

3. `tests/benchmark_heartbeat.py`
   - 新增性能测试脚本

## 后续优化建议

1. **事件驱动更新**: 将 QTimer 改为纯事件驱动，仅在状态变化时触发更新
2. **虚拟化**: 对于大量房间（>6），使用虚拟化技术仅渲染可见区域
3. **批量更新**: 使用 `QApplication.processEvents()` 批量处理多个 UI 更新
4. **异步文件大小**: 将文件大小查询改为完全异步，避免 QThreadPool 开销
