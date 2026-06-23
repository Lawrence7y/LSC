# 错误恢复增强总结

## 增强概述

本次优化增强了错误恢复机制，包括扩展错误消息映射、添加录制完整性校验、增强重连策略和添加错误统计功能。

## 改进内容

### 1. 扩展错误消息映射

**文件**: `lsc/utils/error_messages.py`

新增错误类型：
- HTTP 错误: 404, 429, 5xx
- 网络错误: Connection reset, Connection timed out, Network unreachable
- 磁盘错误: Read-only file system, File name too long
- 平台错误: Cookie invalid, Login required, Region blocked

新增功能函数：
- `get_error_code(raw)` - 返回错误代码
- `is_recoverable_error(raw)` - 判断错误是否可恢复
- `get_retry_suggestion(raw)` - 返回重试建议

### 2. FFmpeg 错误消息增强

**文件**: `lsc/recorder/capture.py`

新增错误映射：
- Connection reset
- No space left
- permission denied
- Invalid data found
- Server returned 5
- HTTP error
- Cookie

### 3. 录制完整性校验

**文件**: `lsc/recorder/capture.py`, `lsc/recorder/session.py`

新增功能：
- `validate_recording(output_path, min_size_mb)` - 验证录制文件完整性
  - 检查文件是否存在
  - 检查文件大小是否满足最小要求
  - 检查文件头是否有效（MP4/FLV/MKV）
- `SessionResult` 新增 `is_valid` 和 `validation_error` 字段
- 录制停止后自动进行完整性校验

### 4. 增强重连策略

**文件**: `lsc/gui/multi_room/manager.py`

改进内容：
- **指数退避**: 重连延迟按指数增长（2s → 4s → 8s，最大 30s）
- **可恢复错误判断**: 使用 `is_recoverable_error()` 判断是否值得重连
- **可配置参数**:
  - `_MAX_RECONNECT_ATTEMPTS = 3` - 最大重连次数
  - `_RECONNECT_DELAY_SEC = 2.0` - 基础延迟
  - `_RECONNECT_MAX_DELAY_SEC = 30.0` - 最大延迟
  - `_RECONNECT_BACKOFF_FACTOR = 2.0` - 退避因子

### 5. 错误统计功能

**文件**: `lsc/utils/error_stats.py` (新增)

功能：
- `ErrorStats` 类 - 跟踪错误类型和频率
- `record_error(error_code, error_msg)` - 记录错误
- `get_error_count(error_code)` - 获取特定错误计数
- `get_error_rate(error_code, window_seconds)` - 获取错误率（每分钟）
- `get_frequent_errors(threshold)` - 获取频繁发生的错误
- `get_summary()` - 获取错误统计摘要
- `get_error_stats()` - 获取全局错误统计实例

## 测试验证

- 26 个录制和多房间管理器测试全部通过
- 无功能回归

## 修改文件清单

1. `lsc/utils/error_messages.py` - 扩展错误映射，新增功能函数
2. `lsc/utils/error_stats.py` - 新增，错误统计功能
3. `lsc/recorder/capture.py` - 增强 FFmpeg 错误映射，添加录制完整性校验
4. `lsc/recorder/session.py` - 集成录制完整性校验
5. `lsc/gui/multi_room/manager.py` - 增强重连策略（指数退避）

## 使用示例

### 错误消息增强
```python
from lsc.utils.error_messages import humanize_error, is_recoverable_error, get_retry_suggestion

error = "Connection timed out"
print(humanize_error(error))  # "连接超时。请检查网络或稍后重试。"
print(is_recoverable_error(error))  # True
print(get_retry_suggestion(error))  # "建议：服务器响应慢，可稍后重试。"
```

### 录制完整性校验
```python
from lsc.recorder.capture import validate_recording

is_valid, error = validate_recording("path/to/recording.mp4")
if not is_valid:
    print(f"录制文件无效: {error}")
```

### 错误统计
```python
from lsc.utils.error_stats import get_error_stats

stats = get_error_stats()
stats.record_error("timeout", "Connection timed out")
print(stats.get_error_rate("timeout"))  # 错误率
print(stats.get_frequent_errors())  # 频繁错误
```

## 后续建议

1. **集成错误统计到 UI**: 在仪表盘显示错误统计信息
2. **智能重连策略**: 根据错误类型和频率调整重连策略
3. **错误告警**: 当错误率超过阈值时通知用户
4. **错误日志持久化**: 将错误统计保存到文件，便于分析
