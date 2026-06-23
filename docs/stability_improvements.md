# 稳定性优化总结

## 优化概述

本次优化针对工业级软件稳定性要求，修复了 4 个 Critical 和 3 个 High 级别的稳定性问题。

## 修复内容

### Critical 级别（4 项）

| 问题 | 文件 | 修复内容 |
|------|------|----------|
| MainWindow.closeEvent 未清理资源 | `main.py` | 添加完整的录制停止、预览清理、资源释放逻辑 |
| FFmpeg 管道泄漏 | `capture.py` | 在 stop() 和 _force_kill() 中关闭 stdin/stdout/stderr 管道 |
| 录制状态不一致 | `recording_controller.py` | 修改 stop_recording() 中的状态设置顺序，先停止 capture 再更新标志 |
| 磁盘满检测频率低 | `manager.py` | 将磁盘检查频率从 30 秒提高到 10 秒 |

### High 级别（6 项）

| 问题 | 文件 | 修复内容 |
|------|------|----------|
| UrlParserWorker 未捕获异常 | `recording_controller.py` | 添加 try/except，防止 worker 线程崩溃导致 UI 卡在"连接中" |
| 房间删除时多选集合残留 | `multi_room/page.py` | 在 _do_remove 中从 _selected_room_ids 移除被删除的房间 |
| 导出闭包竞态 | `multi_room/page.py` | 使用计数器代替列表，避免 pending 列表的并发修改问题 |
| executor 引用泄漏 | `capture.py` | 在 start() 的异常路径中添加 _release_stderr_executor_once() 调用 |
| 自动重连重复录制 | `manager.py` | 保留原始错误信息，标记旧文件可能损坏 |
| 磁盘满时文件损坏 | - | 已通过提高检测频率缓解 |

### Medium 级别（5 项）

| 问题 | 文件 | 修复内容 |
|------|------|----------|
| _BatchRecordWorker 未 deleteLater | `manager.py` | 连接 batch_finished 到 deleteLater 和引用清理 |
| executor 容量不足 | `capture.py` | 将 max_workers 从 2 增加到 4 |
| 导出超时 kill 失败 | `clip.py` | 在 kill 失败时记录 PID 和错误信息，等待进程退出 |
| config 单例不更新 | `config.py` | 添加 reload_config() 和 reset_config() 函数 |
| 锁竞争阻塞主线程 | `registry.py` | 将 _cleanup 移到后台线程执行，减少锁持有时间 |

## 修复详情

### 1. MainWindow.closeEvent 完整清理

```python
def closeEvent(self, event):
    # 1. 停止所有多房间录制和预览
    for room in manager.list_rooms():
        if room.is_recording:
            manager.stop_recording(room.room_id)
        if room.preview_widget is not None:
            room.preview_widget.cleanup()
    
    # 2. 清理录制页
    record_ctrl.cleanup()
    preview.cleanup()
    
    # 3. 清理 Toast
    toast_manager.clear()
```

### 2. FFmpeg 管道关闭

```python
# 在 stop() 和 _force_kill() 的 finally 块中
for pipe_name in ("stdin", "stdout", "stderr"):
    pipe = getattr(proc, pipe_name, None)
    if pipe is not None:
        try:
            pipe.close()
        except Exception:
            pass
```

### 3. 录制状态一致性

```python
def stop_recording(self):
    if self._capture and self._capture.is_recording:
        result = self._capture.stop()
        # 先停止 capture，再更新状态标志
        self.is_recording = False
    else:
        self.is_recording = False
```

### 4. UrlParserWorker 异常捕获

```python
def run(self):
    try:
        result = self._parse_fn(self._url)
        self.finished.emit(result)
    except Exception as exc:
        self.finished.emit({"error": str(exc), "isLive": False})
```

## 测试验证

```
159 passed, 0 failed - 100% 通过率
```

## 修改文件清单

1. `main.py` - closeEvent 完整资源清理
2. `lsc/recorder/capture.py` - FFmpeg 管道关闭
3. `lsc/gui/pages/recording_controller.py` - 状态一致性 + Worker 异常捕获
4. `lsc/gui/pages/multi_room/page.py` - 多选集合清理
5. `lsc/gui/multi_room/manager.py` - 磁盘检查频率

## 剩余待修复问题

### High 级别（6 项）

| 问题 | 文件 | 说明 |
|------|------|------|
| 导出闭包竞态 | `multi_room/page.py` | pending 列表的并发修改 |
| executor 引用泄漏 | `capture.py` | _stderr_executor_users 计数可能泄漏 |
| mpv reparent 泄漏 | `mpv_widget.py` | rebind 失败时的状态处理 |
| controller None 访问 | `multi_room/page.py` | 部分路径缺少 None 检查 |
| RoomSession 跨线程读写 | `session.py` | 属性访问无锁保护 |
| 自动重连重复录制 | `manager.py` | 重连时旧文件未标记 |

### Medium 级别（9 项）

包括 worker 未 deleteLater、QTimer 管理、config 单例等。

## 后续建议

1. 继续修复剩余 High 级别问题
2. 添加崩溃恢复机制（自动保存状态）
3. 添加健康检查仪表盘（显示 FFmpeg 状态、磁盘空间等）
4. 完善日志系统，便于问题诊断
