# Checklist

## 阶段一：严重问题（S1-S8）

### S1 IPC 死代码移除（含 M6）
- [x] `preload.ts` 中 `sendToPython` 与 `onPythonMessage` 已删除
- [x] 全局搜索确认无前端代码引用这两个 API
- [x] M6 监听器无法移除问题随删除一并解决

### S2 open-path 路径校验（含 M5）
- [x] `open-path` handler 调用 `_isSafePath` 校验路径在白名单目录内
- [x] 扩展名黑名单（.exe/.bat/.ps1/.cmd/.vbs/.scr）拒绝并返回错误
- [x] handler 返回 Promise 结果给前端（M5 修复）

### S3 MSE 跨线程访问
- [x] `_handle_mse_preview` enable 分支的 `mgr.get_room` 通过 `bridge.call` 调用（参考 disable 分支）
- [x] `room.stream_info`/`room.preview_enabled` 访问通过 bridge 返回的快照
- [x] 不再在 asyncio 线程直接访问 Qt 对象

### S4 _mse_streamers 加锁
- [x] 模块级存在 `_mse_streamers_lock = threading.Lock()`
- [x] 所有 `_mse_streamers` 读写（行 851/910/923）用 `with _mse_streamers_lock:` 保护
- [x] `run_in_executor` 回调（行 910）中也持锁

### S5 信号连接注释（降级为代码质量改进）
- [x] `room_handler.py:353-355` 添加注释说明与 `message_bridge.py:34-36` 的职责区分
- [x] 注释说明两处投递不同类型消息（rooms_updated vs 事件通知）

### S6 _get_current_pos
- [x] `if pos:` 改为 `if pos is not None:`
- [x] pos=0 时返回 0.0 而非录制时长

### S7 start.py 与 server.py 入口
- [x] `start.py` 构造了 bridge 实例
- [x] `start.py` `register_room_handlers(server, bridge)` 调用参数正确
- [x] `server.py:105` 的 `main()` 同样修复
- [x] `python start.py` 能正常启动

### S8 Python 解释器检测
- [x] `spawnBackend()` 优先使用捆绑解释器，fallback 系统 python
- [x] 检测失败时前端显示明确错误
- [x] `package.json` build 配置携带嵌入式 Python 或文档说明

## 阶段二：高优先级问题（H1-H15，H11 不修复）

### H1-H3 WebSocket
- [x] `manualClose` 标志存在，`disconnect()` 设置为 true
- [x] `scheduleReconnect` 实现 1s→2s→4s→8s→15s 指数退避
- [x] `onclose` 检测 `manualClose` 不重连
- [x] `connect()` 开头关闭已有旧连接
- [x] `send()` 断连时入队（上限 100）
- [x] 重连成功后 flush 消息队列

### H4 preview_frame 节流
- [x] 使用 `requestAnimationFrame` 节流
- [x] 组件卸载时 `cancelAnimationFrame`

### H5 killBackend 异步
- [x] POSIX 分支 `sleepSync` 循环改为 `setTimeout` 异步轮询
- [x] 主进程不再阻塞

### H6-H7 确认对话框
- [x] 批量录制有 `Modal.confirm`
- [x] 批量停止有 `Modal.confirm`
- [x] 停止单个录制有 `Modal.confirm`

### H8-H9 导出重试与取消
- [x] `ExportJob` 创建时保存 `preset_id`
- [x] `handleRetryJob` 携带原始 `preset_id`
- [x] `handleCancelJob` 发送 `cancel_export` 消息
- [x] 后端 `cancel_export` handler 终止 FFmpeg 进程

### H10 selectedRoomIds 同步
- [x] `useEffect` 监听 store `selectedRoomId` 反向同步 `selectedRoomIds`
- [x] Dashboard 跳转后 Workbench 选中状态正确

### H12 后端异常响应
- [x] 处理器异常时返回 `{success:false, error}` 响应
- [x] 前端不永久等待

### H13 traceback 显式日志
- [x] `_on_execute` 捕获异常时记录 `traceback.format_exc()`
- [x] `call()` 抛异常时日志含完整 traceback（非仅 str(e)）

### H14-H15 依赖与构建
- [x] `@types/node` 在 devDependencies 显式声明
- [x] `vite.config.ts` external 含 path/fs/child_process/os/crypto

## 阶段三：中优先级问题（M1-M30，M12/M30 不修复）

### Electron 主进程（M1-M8）
- [x] 15 处无日志 catch 块均有日志
- [x] `backendLogStream` 重复创建前关闭旧流
- [x] IPC handler 在 `app.whenReady` 顶层注册一次
- [x] 托盘失败时 `before-quit` 仍可退出（含 minimizeToTray=true 场景）
- [x] env 白名单透传（仅必要项）
- [x] `hiddenInset` 仅 macOS 应用

### 前端（M9-M22）
- [x] `addRoom` 按 room_id 去重
- [x] `addClip` 上限 200 条
- [x] `connectionStatus` 为字面量联合类型
- [x] 重连过程中 `connectionStatus` 更新为 `'connecting'`（M12 状态显示瑕疵修复）
- [x] MSE `_cleanup` endOfStream 有 try/catch
- [x] SourceBuffer 监听器在 cleanup 移除
- [x] `play()` 被阻止时状态一致
- [x] 选区试听定时器无陈旧闭包
- [x] 多 URL 添加 loading 在全部 settle 后关闭
- [x] 磁盘空间不足 Dashboard 有警告（如 >90%）
- [x] 原生 `<select>` 全部替换为 Ant Design Select
- [x] `formatTime` 抽到 utils 单一来源
- [x] light 主题独立变量集（风险评估后添加注释说明技术债）
- [x] `!important` 清理（评估后保留并注释说明，覆盖 antd 样式所必需）

### Python 后端（M23-M29，M30 不修复）
- [x] 22 处 `asyncio.get_event_loop()` 全部替换为 `get_running_loop()`
- [x] `float()` 转换有 try/except
- [x] `save_settings`/`save_rooms` 有校验
- [x] `_broadcast_rooms` 有异常处理
- [x] `stop()` 访问 `_server` 有 None 检查；`close()` 用 `run_coroutine_threadsafe` 调度
- [x] WebSocket 地址可配置
- [x] `extraResources` 路径健壮（评估后保持现状）

## 阶段四：低优先级问题（L1-L22）

### UX/可访问性（L1-L11）
- [x] 最近切片列表有键盘/焦点支持
- [x] 历史记录项有 role 和键盘可访问性
- [x] 房间名截断有 Tooltip（streamer_name 与 stream_title）
- [x] 错误信息截断 + Tooltip
- [x] 导出选择器有 ARIA 标签
- [x] 录制指示条有动画（脉冲 keyframes）
- [x] 断开按钮有 loading
- [x] 统计卡片可点击（房间数/录制中跳转 /workbench）
- [x] Dashboard 重复按钮已合并（删除"开始录制"保留"管理房间"）
- [x] 页面响应式适配（Row/Col xs/sm/md/lg）
- [x] WebSocket 断连有 UI 提示（Workbench 顶部 Alert banner）

### 代码质量（L12-L22）
- [x] `_broadcast_loop` 死代码删除
- [x] `bridge.py` 文件删除（Grep 确认无引用）
- [x] `_shutdown` 标志删除
- [x] `recording_history` 持久化到 recording_history.json
- [x] 未用依赖 `vite-plugin-electron-renderer` 删除
- [x] `electron:build` 恢复 tsc 检查
- [x] `--state-warning` 补 surface/foreground token（按既有命名约定）
- [x] RoomCard onClick 验证已满足（onClick 在 Card 根，按钮 stopPropagation 存在）
- [x] selectedRoomIds 同步验证阶段二 H10 已实现（useEffect）
- [x] Ctrl+A 改为 Ctrl+Shift+A
- [x] Empty 文案改为"暂无房间，请添加直播间地址"

## 阶段五：验证

### 类型与构建
- [x] `npx tsc --noEmit` 通过（exit code 0）
- [x] `npx vite build` 通过（前端 + main + preload 三构建成功）
- [x] `python -m pytest` 全量通过（236 passed, 2 warnings）

### 用户视角（需用户在 Trae IDE 外启动应用手动验证）
- [ ] 全流程：启动→添加→连接→预览→录制→停止→切片→导出 正常
- [ ] 异常断线后 WebSocket 指数退避重连
- [ ] open-path 拒绝可执行文件
- [ ] 批量/停止操作有确认对话框
- [ ] start.py 与 server.py 入口可正常启动

## 不修复项（验证不存在）

- [x] H11：`server.py:86` 列表推导式同步执行有隐式快照，`return_exceptions=True` 处理断连异常，无需修改
- [x] M12：重连由 websocket.ts 内部 `onclose → scheduleReconnect` 保证；状态显示瑕疵归入 M11 修复
- [x] M30：`vite-plugin-electron` 实际为 1.1.0 正式版（非 beta），原描述错误
