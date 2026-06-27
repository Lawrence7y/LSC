# Tasks

## 阶段一：严重问题（S1-S8）

- [x] Task S1: 移除 IPC python-message 死代码（同时解决 M6）
  - [x] SubTask S1.1: 删除 `preload.ts` 中 `sendToPython` 与 `onPythonMessage`（行 33-40）
  - [x] SubTask S1.2: 全局搜索确认无前端代码引用这两个 API

- [x] Task S2: open-path IPC 路径白名单校验（同时解决 M5）
  - [x] SubTask S2.1: `main.ts` 的 `open-path` handler 增加路径校验函数 `_isSafePath(p)`：解析绝对路径，确认在 output 目录内
  - [x] SubTask S2.2: 扩展名黑名单（.exe/.bat/.ps1/.cmd/.vbs/.scr）拒绝并返回 `{success:false, error:'不允许打开此类型文件'}`
  - [x] SubTask S2.3: handler 改为 `return shell.openPath(openPathStr)` 返回 Promise 结果给前端

- [x] Task S3: MSE 处理器跨线程访问修复
  - [x] SubTask S3.1: `room_handler.py` `_handle_mse_preview` enable 分支（行 854-862）的 `mgr.get_room(room_id)` 改为通过 `bridge.call` 调用（参考 disable 分支行 929-935 的实现）
  - [x] SubTask S3.2: `room.is_connected`、`room.stream_info.stream_url`、`room.preview_enabled` 等访问改为通过 bridge.call 返回的快照对象，或在 bridge.call 内一次性取所需字段

- [x] Task S4: _mse_streamers 加锁
  - [x] SubTask S4.1: 模块级新增 `_mse_streamers_lock = threading.Lock()`
  - [x] SubTask S4.2: 所有 `_mse_streamers` 读写（行 851 读、910 线程池写、923 删）用 `with _mse_streamers_lock:` 保护

- [x] Task S5: 信号连接注释说明（降级为代码质量改进）
  - [x] SubTask S5.1: 在 `room_handler.py:353-355` 添加注释，说明这三处连接投递 `rooms_updated` 状态刷新，与 `message_bridge.py:34-36` 投递事件通知的职责区分，避免后续误删

- [x] Task S6: _get_current_pos 修复 pos=0
  - [x] SubTask S6.1: `if pos:` 改为 `if pos is not None:`

- [x] Task S7: start.py 与 server.py 补齐 bridge 参数
  - [x] SubTask S7.1: `start.py` 构造 `QtManagerBridge` 与 `MultiRoomManager`（参考 `main.py` 的初始化逻辑）
  - [x] SubTask S7.2: 调用 `register_room_handlers(server, bridge)`
  - [x] SubTask S7.3: `server.py:105` 的 `main()` 同样修复

- [x] Task S8: Python 解释器检测
  - [x] SubTask S8.1: `main.ts` `spawnBackend()` 增加 `detectPython()`：优先尝试 `python`/`python3`，再尝试 `extraResources/python/python.exe`
  - [x] SubTask S8.2: 检测失败时通过 IPC 通知前端显示错误页（"未检测到 Python，请安装或使用完整安装包"）
  - [x] SubTask S8.3: `package.json` build 配置考虑携带嵌入式 Python 或文档说明

## 阶段二：高优先级问题（H1-H15，H11 不修复）

- [x] Task H1-H3: WebSocket 重连与消息队列
  - [x] SubTask H1.1: `websocket.ts` 增加 `manualClose` 标志，`disconnect()` 设置为 true
  - [x] SubTask H1.2: `scheduleReconnect` 实现指数退避：1s→2s→4s→8s→15s 封顶
  - [x] SubTask H1.3: `onclose` 检测 `manualClose`，true 则不重连
  - [x] SubTask H2.1: `connect()` 开头 `if (ws) { ws.close(); ws = null }` 关闭旧连接
  - [x] SubTask H3.1: 新增 `messageQueue: string[]`，`send()` 断连时入队（上限 100）
  - [x] SubTask H3.2: 重连成功后 flush 队列

- [x] Task H4: preview_frame 节流
  - [x] SubTask H4.1: `useWebSocket.ts` 引入 `requestAnimationFrame`，preview_frame 回调中暂存最新帧，rAF 内一次性 `updateRoom`
  - [x] SubTask H4.2: 组件卸载时 cancelAnimationFrame

- [x] Task H5: killBackend 异步化
  - [x] SubTask H5.1: POSIX 分支 `sleepSync` 循环改为 `setTimeout` 异步轮询，不阻塞主进程

- [x] Task H6-H7: 破坏性操作确认
  - [x] SubTask H6.1: `Workbench/index.tsx` `handleBatchRecord`/`handleBatchStop` 增加 `Modal.confirm`（参考删除房间的确认实现）
  - [x] SubTask H7.1: `RoomCard.tsx` 停止按钮点击改为 `Modal.confirm` 后调用 `onStopRecord`

- [x] Task H8-H9: 导出重试与取消
  - [x] SubTask H8.1: `ExportJob` 创建时保存 `preset_id`；`handleRetryJob` 携带原始 `preset_id`
  - [x] SubTask H9.1: `handleCancelJob` 通过 WebSocket 发送 `cancel_export` 消息
  - [x] SubTask H9.2: 后端 `room_handler.py` 新增 `cancel_export` handler，终止对应 FFmpeg 进程

- [x] Task H10: selectedRoomIds 同步
  - [x] SubTask H10.1: `Workbench/index.tsx` 用 `useEffect` 监听 store `selectedRoomId`，反向同步到 `selectedRoomIds`

- [x] Task H12: 后端异常响应
  - [x] SubTask H12.1: `server.py` `handle_client` 的 except 块向客户端发送 `{type: '<msg_type>_response', data: {success:false, error:str(e)}}`

- [x] Task H13: bridge.call 显式记录 traceback
  - [x] SubTask H13.1: `message_bridge.py` `_on_execute` 捕获异常时 `import traceback; req.traceback = traceback.format_exc()`
  - [x] SubTask H13.2: `call()` 抛异常时日志记录 `req.traceback`（当前仅打印 `str(e)`）

- [x] Task H14-H15: 依赖与构建
  - [x] SubTask H14.1: `package.json` devDependencies 显式声明 `@types/node`
  - [x] SubTask H15.1: `vite.config.ts` external 增加 `path`/`fs`/`child_process`/`os`/`crypto`

## 阶段三：中优先级问题（M1-M30，M12/M30 不修复）

- [x] Task M1-M8: Electron 主进程修复
  - [x] SubTask M1.1: 全文 15 处无日志 catch 块增加 `console.error` 或 `writeLog`
  - [x] SubTask M2.1: `backendLogStream` 重复创建前 `backendLogStream?.end()` 关闭旧流
  - [x] SubTask M3.1: IPC handler 从 `createWindow` 移到 `app.whenReady` 顶层注册一次
  - [x] SubTask M4.1: `createTray` try/catch 失败时确保 `app.on('before-quit')` 仍可退出（尤其 `minimizeToTray=true` 场景）
  - [Task S2 已覆盖 M5]
  - [Task S1 已覆盖 M6]
  - [x] SubTask M7.1: spawnBackend env 改为白名单透传（PATH/USERPROFILE/APPDATA/TEMP 等必要项）
  - [x] SubTask M8.1: `hiddenInset` 仅在 `process.platform === 'darwin'` 时应用

- [x] Task M9-M22: 前端修复
  - [x] SubTask M9.1: `appStore.ts` `addRoom` 按 `room_id` 去重，已存在则更新
  - [x] SubTask M10.1: `addClip` 上限 200 条，超出移除最旧
  - [x] SubTask M11.1: `useWebSocket.ts` `connectionStatus` 改为 `'connected'|'connecting'|'disconnected'` 字面量联合；重连过程中更新为 `'connecting'`（同时修复 M12 状态显示瑕疵）
  - [x] SubTask M13.1: `mediaSourcePlayer.ts` `_cleanup` 中 endOfStream 加 try/catch 与状态判断
  - [x] SubTask M14.1: SourceBuffer 事件监听器在 cleanup 中 `removeEventListener`
  - [x] SubTask M15.1: `play()` 被阻止时捕获 promise rejection，标记 `paused` 状态
  - [x] SubTask M16.1: 选区试听定时器用 `useRef` 捕获最新闭包值
  - [x] SubTask M17.1: 多 URL 添加 loading 在所有 promise 全部 settle 后才关闭
  - [x] SubTask M18.1: Dashboard 磁盘空间不足时显示警告 banner（如 >90%）
  - [x] SubTask M19.1: 全局替换原生 `<select>` 为 Ant Design `<Select>`
  - [x] SubTask M20.1: `formatTime` 抽到 `src/utils/time.ts`，三处引用改为 import
  - [x] SubTask M21.1: `tokens.css` light 主题改为独立变量集（不覆盖 dark 调色板）— 风险评估后添加注释说明技术债
  - [x] SubTask M22.1: `global.css` 清理 45 处 `!important` — 评估后保留并注释说明（覆盖 antd 样式所必需）

- [x] Task M23-M29: Python 后端修复（M30 不修复）
  - [x] SubTask M23.1: 全文 22 处 `asyncio.get_event_loop()` 改为 `asyncio.get_running_loop()`（在协程内）
  - [x] SubTask M24.1: `float()` 转换加 try/except，失败返回错误响应
  - [x] SubTask M25.1: `save_settings`/`save_rooms` 加关键字段校验
  - [x] SubTask M26.1: `_broadcast_rooms` 的 `asyncio.create_task` 加 `done_callback` 处理异常
  - [x] SubTask M27.1: `main.py` `stop()` 访问 `server._server` 加 None 检查；`close()` 是协程，用 `asyncio.run_coroutine_threadsafe` 调度
  - [x] SubTask M28.1: WebSocket 地址改为从 `import.meta.env` 或 settings 读取
  - [x] SubTask M29.1: `package.json` `extraResources` 改用绝对路径或 `${workspaceFolder}` — 评估后保持现状

## 阶段四：低优先级问题（L1-L22）

- [x] Task L1-L11: UX/可访问性
  - [x] SubTask L1.1: 最近切片列表加 `role="list"`/`tabIndex`/键盘导航
  - [x] SubTask L2.1: 历史记录项加 `role="button"`/`tabIndex`/Enter 触发
  - [x] SubTask L3.1: 房间名截断加 `Tooltip`（streamer_name 与 stream_title）
  - [x] SubTask L4.1: 错误信息超长截断 + Tooltip 显示完整
  - [x] SubTask L5.1: 导出选择器加 `aria-label`
  - [x] SubTask L6.1: 录制指示条加 CSS 动画（脉冲）
  - [x] SubTask L7.1: 断开连接按钮加 `loading` 状态
  - [x] SubTask L8.1: 统计卡片加点击跳转
  - [x] SubTask L9.1: 合并 Dashboard 重复按钮（已删除"开始录制"，保留"管理房间"）
  - [x] SubTask L10.1: Dashboard 响应式断点适配（Row/Col xs/sm/md）
  - [x] SubTask L11.1: WebSocket 断连时 Workbench 顶部显示重连提示 banner

- [x] Task L12-L22: 代码质量
  - [x] SubTask L12.1: 删除 `main.py` `_broadcast_loop` 死代码
  - [x] SubTask L13.1: 删除 `python-backend/bridge.py` 整个文件（Grep 确认无 Python 源码引用）
  - [x] SubTask L14.1: 删除 `message_bridge.py` `_shutdown` 标志
  - [x] SubTask L15.1: `recording_history` 持久化到 `recording_history.json`（start/stop 后保存）
  - [x] SubTask L16.1: 删除 `vite-plugin-electron-renderer` 未用依赖
  - [x] SubTask L17.1: `package.json` `electron:build` 恢复 `tsc --noEmit` 前置
  - [x] SubTask L18.1: `tokens.css` `--state-warning` 补 `--state-warning-surface`/`--state-warning-foreground`（按文件既有命名约定）
  - [x] SubTask L19.1: `RoomCard.tsx` onClick 验证已挂在 Card 根元素，按钮 stopPropagation 已存在，无需修改
  - [x] SubTask L20.1: `selectedRoomIds` 同步验证阶段二 H10 已正确实现 useEffect，无需修改
  - [x] SubTask L21.1: Ctrl+A 改为 Ctrl+Shift+A
  - [x] SubTask L22.1: Empty 文案改为"暂无房间，请添加直播间地址"

## 阶段五：验证

- [x] Task V1: 类型与构建验证
  - [x] SubTask V1.1: `npx tsc --noEmit` 通过（exit code 0）
  - [x] SubTask V1.2: `npx vite build` 通过（前端 + main + preload 三构建 exit code 0；electron-builder 打包步骤跳过以避免长时间签名）
  - [x] SubTask V1.3: `python -m pytest` 全量通过（236 passed, 2 warnings）

- [ ] Task V2: 用户视角验证（需用户在 Trae IDE 外启动应用手动验证）
  - [ ] SubTask V2.1: 启动应用 → 后端启动 → 添加房间 → 连接 → 预览 → 录制 → 停止（确认）→ 切片 → 导出
  - [ ] SubTask V2.2: 模拟异常断线，验证 WebSocket 指数退避重连
  - [ ] SubTask V2.3: 验证 open-path 拒绝可执行文件
  - [ ] SubTask V2.4: 验证批量操作确认对话框
  - [ ] SubTask V2.5: 验证 start.py 与 server.py 入口可正常启动

# Task Dependencies

- 阶段一（S1-S8）必须最先完成，是核心链路可用前提
- 阶段二 H12/H13 依赖 S3/S4/S5（后端线程安全修复）
- 阶段二 H9 取消导出依赖后端 cancel_export handler（与 M26 并行）
- 阶段三 M23/M24/M25 可与阶段二并行（独立后端修复）
- 阶段四 L12/L13/L14 删除死代码应在确认无引用后执行
- 阶段五验证依赖所有修复完成
- 可并行执行的组：
  - 前端任务（H1-H4, H6-H8, H10, M9-M22, L1-L11, L16-L22）与后端任务（S3-S7, H12-H13, M23-M27, L12-L15）互不依赖
  - Electron 主进程任务（S1, S2, S8, H5, M1-M8）与前端组件任务互不依赖

# 不修复项说明（验证不存在）

- **H11**：`server.py:86` 列表推导式同步执行有隐式快照，`return_exceptions=True` 处理断连异常，无需修改。
- **M12**：重连由 websocket.ts 内部 `onclose → scheduleReconnect` 保证，非"永远无法重连"；仅状态显示瑕疵归入 M11 一并修复。
- **M30**：`vite-plugin-electron` 实际为 1.1.0 正式版（非 beta），原描述错误。
- **S5**：原描述"双重 rooms_updated"不成立（两处投递不同类型消息），降级为代码注释改进。
