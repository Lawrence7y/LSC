# 核心稳定性与逻辑问题修复 - The Implementation Plan

## [ ] Task 1: 修复 WebSocket 消息队列过时命令问题
- **Priority**: high
- **Depends On**: None
- **Description**: 
  - 修改 [websocket.ts](file:///d:/Project/直播切片多人/lsc-electron/src/services/websocket.ts) 的 send() 方法，增加消息类型分类
  - 定义"命令类消息"黑名单：`start_recording`, `stop_recording`, `enable_preview`, `disable_preview`, `start_export`, `cancel_export`, `remove_room`, `disconnect_room`
  - 命令类消息在 WebSocket 未连接时直接丢弃（或仅保留最后一条），不进入队列
  - 非命令类消息（查询、设置、状态请求）仍可排队，队列上限保持 100
  - 增加 `_lastCommand` 缓存，重连后只发送最后一条命令（如果是幂等操作）
- **Acceptance Criteria Addressed**: AC-1
- **Test Requirements**:
  - `programmatic` TR-1.1: 断连时发送 5 条 stop_recording 命令，重连后验证 WebSocket 帧中不包含这些命令
  - `programmatic` TR-1.2: 断连时发送 get_settings/get_disk_usage 查询，重连后验证这些消息被正常发送
  - `human-judgement` TR-1.3: 代码审查确认命令类消息黑名单完整
- **Notes**: 队列策略采用"命令丢弃、查询排队"的混合策略，避免断连期间的状态命令在重连后产生意外效果

## [ ] Task 2: 为 WebSocket 连接添加超时机制
- **Priority**: high
- **Depends On**: None
- **Description**: 
  - 修改 [websocket.ts](file:///d:/Project/直播切片多人/lsc-electron/src/services/websocket.ts) 的 connect() 方法
  - 添加 10 秒连接超时计时器，超时后 reject Promise 并触发 scheduleReconnect
  - 确保超时后正确清理 WebSocket 实例和 pendingConnect 引用
- **Acceptance Criteria Addressed**: AC-8
- **Test Requirements**:
  - `programmatic` TR-2.1: 连接到一个被防火墙丢弃的端口（如 192.0.2.1:9876），验证 10 秒后触发超时并进入重连
  - `programmatic` TR-2.2: 正常连接时超时计时器被正确清理，不影响正常流程
- **Notes**: 使用 setTimeout + clearTimeout 实现，超时后调用 ws.close() 清理资源

## [ ] Task 3: 修复 MultiRoomManager 跨线程状态安全
- **Priority**: high
- **Depends On**: None
- **Description**: 
  - 修改 [manager.py](file:///d:/Project/直播切片多人/lsc/gui/multi_room/manager.py) 的 _BatchRecordWorker
  - 将 start_recording 中修改 RoomSession 状态的代码（is_recording, record_output_path 等）通过 QTimer.singleShot(0, ...) 或 Signal 调度回主线程执行
  - _BatchRecordWorker.run() 只负责调用 controller.start_recording_with_crf() 获取结果，不直接修改 room 状态
  - 添加 threading.Lock 保护 _rooms 字典的读写操作（add_room, remove_room, list_rooms, get_room）
  - 确保 _on_connect_finished、_on_probe_finished 等 Signal 槽已在主线程（Qt AutoConnection 保证）
- **Acceptance Criteria Addressed**: AC-2
- **Test Requirements**:
  - `programmatic` TR-3.1: 单元测试验证批量录制工作线程不直接修改 room 属性（mock 检查）
  - `human-judgement` TR-3.2: 4 房间批量录制过程中快速点击开始/停止，验证无崩溃、无状态不一致
  - `programmatic` TR-3.3: 验证 add_room/remove_room 并发调用时 _rooms 字典不报错
- **Notes**: Qt Signal/Slot 跨线程时 AutoConnection 会自动 PostEvent 到接收者线程，但 worker 直接写 room 属性不经过 Signal，这是问题根源

## [ ] Task 4: 分离 refresh worker 字典并正确取消
- **Priority**: high
- **Depends On**: Task 3
- **Description**: 
  - 修改 [manager.py](file:///d:/Project/直播切片多人/lsc/gui/multi_room/manager.py)
  - 将 _refresh_ 前缀的 worker 从 _connect_workers 移到独立的 _refresh_workers 字典
  - remove_room 和 _cancel_connect_worker 中也取消对应的 refresh worker
  - 确保 refresh_stream_url_async 的 finished 回调在 room 已删除时不执行任何操作
- **Acceptance Criteria Addressed**: AC-10
- **Test Requirements**:
  - `programmatic` TR-4.1: 单元测试模拟 refresh 进行中删除房间，验证 worker 被 requestInterruption
  - `programmatic` TR-4.2: 验证 refresh 完成回调中 room 不存在时不崩溃
- **Notes**: 原代码中 `self._connect_workers[f"_refresh_{room_id}"] = worker` 是 hack，应使用独立字典

## [ ] Task 5: 异步化 MseStreamer.start() 避免阻塞
- **Priority**: high
- **Depends On**: None
- **Description**: 
  - 修改 [mse_streamer.py](file:///d:/Project/直播切片多人/lsc/core/services/mse_streamer.py)
  - 将 start() 中的 2 秒 FFmpeg 启动探测移到独立的 _probe_thread 中
  - start() 立即返回 True（表示已启动启动流程），探测结果通过 on_error 回调或新增的 on_ready 回调通知
  - 增加 _started_future 或 _probe_result 用于同步启动状态
  - 调用方（room_handler.py / enable_preview 流程）需要适配异步启动
- **Acceptance Criteria Addressed**: AC-3
- **Test Requirements**:
  - `programmatic` TR-5.1: 调用 start() 后立即返回，不阻塞 2 秒
  - `programmatic` TR-5.2: FFmpeg 启动失败时 on_error 被正确调用
  - `human-judgement` TR-5.3: 点击预览按钮后 UI 立即响应，无卡顿
- **Notes**: 需要确认调用方如何处理异步启动——现有代码是同步返回 True/False 表示是否成功，改为异步后需要调整调用链

## [ ] Task 6: Electron 端添加 Python 依赖检测
- **Priority**: medium
- **Depends On**: None
- **Description**: 
  - 修改 [main.ts](file:///d:/Project/直播切片多人/lsc-electron/electron/main.ts) 的 detectPython() 或新增 verifyPythonDeps()
  - 在检测到 Python 解释器后，运行 `python -c "import PySide6; import websockets"` 验证依赖
  - 如果导入失败，收集缺失的模块名，通过 pythonDetectError 传递给前端
  - 前端（App.tsx 或 MainLayout）监听 backend-error IPC 事件，显示友好错误消息
- **Acceptance Criteria Addressed**: AC-4
- **Test Requirements**:
  - `programmatic` TR-6.1: 模拟缺失 PySide6，验证检测到并生成错误消息
  - `human-judgement` TR-6.2: 启动程序时如果依赖缺失，前端显示明确的安装提示
- **Notes**: 使用 child_process.execSync 运行验证命令，超时设为 5 秒

## [ ] Task 7: 统一批量录制目录回退逻辑
- **Priority**: medium
- **Depends On**: None
- **Description**: 
  - 修改 [manager.py](file:///d:/Project/直播切片多人/lsc/gui/multi_room/manager.py) 的 start_recording_all 和 start_recording_all_async
  - 在批量录制开始前先确定统一的输出目录：
    1. 先对用户指定目录做预检
    2. 如果预检失败，尝试回退目录并预检
    3. 如果回退目录也失败，直接返回失败
    4. 将统一的最终目录传递给每个房间的 start_recording，跳过内部的二次回退
  - 修改 start_recording 增加 final_output_dir 参数，避免单个房间再次回退
- **Acceptance Criteria Addressed**: AC-5
- **Test Requirements**:
  - `programmatic` TR-7.1: 模拟默认目录不可写，验证所有房间录制路径都在回退目录下
  - `programmatic` TR-7.2: 默认目录可写时不回退
- **Notes**: 原问题是 start_recording 内部有回退逻辑，但批量录制时预检后单房间仍可能回退到不同目录

## [ ] Task 8: 修复 openPath 路径白名单包含用户自定义目录
- **Priority**: medium
- **Depends On**: None
- **Description**: 
  - 修改 [main.ts](file:///d:/Project/直播切片多人/lsc-electron/electron/main.ts) 的 _isSafePath()
  - 除了硬编码的 userData 和 ~/LSC，还需要动态添加用户配置的输出目录
  - 增加 IPC 通道让前端在用户修改输出目录后通知主进程更新白名单
  - 或者在 open-path/show-item-in-folder 处理时实时读取 settings 中的 output_dir 加入白名单
  - 持久化白名单目录列表，启动时从设置加载
- **Acceptance Criteria Addressed**: AC-6
- **Test Requirements**:
  - `human-judgement` TR-8.1: 在设置中配置 D:\Videos\直播 作为输出目录，录制后点击打开文件夹能正确打开
  - `programmatic` TR-8.2: 验证不在白名单中的路径（如 C:\Windows\System32）仍被拒绝
- **Notes**: 最简单方案是 _isSafePath 动态读取已持久化的设置文件，或在调用 open-path 时传入 output_dir 参数

## [ ] Task 9: 房间删除时清理 MSE 缓存
- **Priority**: medium
- **Depends On**: None
- **Description**: 
  - 修改 [useWebSocket.ts](file:///d:/Project/直播切片多人/lsc-electron/src/hooks/useWebSocket.ts)
  - 监听 room_removed 或处理 rooms_updated 时检查被删除的房间
  - 当 room_id 从 rooms 列表中消失时，清理 _mseInitCache[room_id] 和 _mseSegmentCache[room_id]
  - 同时清理 __mseInitRetryCount[room_id]
  - 考虑增加 WebSocket 消息类型或在 rooms_updated 中对比 diff 检测删除
- **Acceptance Criteria Addressed**: AC-7
- **Test Requirements**:
  - `programmatic` TR-9.1: 添加房间开启预览后删除，验证缓存中对应条目被移除
  - `programmatic` TR-9.2: 多次添加/删除房间后缓存大小不持续增长
- **Notes**: 需要后端在删除房间时主动推送 room_removed 事件，或前端对比新旧 rooms 列表

## [ ] Task 10: 为广播队列增加大小限制
- **Priority**: medium
- **Depends On**: None
- **Description**: 
  - 修改 [message_bridge.py](file:///d:/Project/直播切片多人/python-backend/message_bridge.py)
  - 查看现有队列实现（queue.Queue 或 list），添加 maxsize=1000
  - 如果队列满，put 时丢弃最旧消息（put_nowait + get 丢弃，或使用 deque）
  - 添加警告日志记录队列溢出事件
- **Acceptance Criteria Addressed**: AC-9
- **Test Requirements**:
  - `programmatic` TR-10.1: 快速推送 2000 条广播消息，验证队列大小不超过 1000
  - `programmatic` TR-10.2: 队列溢出时记录警告日志
- **Notes**: 需要先确认 message_bridge.py 当前队列实现方式

## [ ] Task 11: 修复 Electron before-quit 重复 killBackend
- **Priority**: low
- **Depends On**: None
- **Description**: 
  - 修改 [main.ts](file:///d:/Project/直播切片多人/lsc-electron/electron/main.ts)
  - 添加 killBackend 调用标记，避免 before-quit 和 exit 事件重复执行
  - 移除 process.on('exit') 中的 killBackend 调用（因为 exit 事件中只能执行同步操作，execSync 不安全）
  - 或者在 killBackend 开头添加 _backendKilled 标志防止重入
- **Acceptance Criteria Addressed**: （代码质量改进，无直接 AC）
- **Test Requirements**:
  - `programmatic` TR-11.1: 退出程序时 taskkill 只执行一次
  - `human-judgement` TR-11.2: 程序正常退出，无延迟或挂起
