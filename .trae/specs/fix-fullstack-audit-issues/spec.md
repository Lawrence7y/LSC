# 全栈审计问题修复 Spec

## Why
对 Electron + Python 后端的全栈代码审计发现问题若干（经逐项验证：67 项确认存在 / 5 项部分存在 / 3 项不存在），涵盖 IPC 通信断路、安全漏洞（任意文件执行）、Qt 跨线程访问、WebSocket 连接泄漏、React 性能、异常吞没、可访问性缺失等多个层面。严重问题已导致核心链路不可用或存在 RCE 风险，必须系统性修复才能让程序稳定可用。

## 审查结论概览（已逐项验证）

| 分类 | 确认存在 | 部分存在 | 不存在 |
|------|----------|----------|--------|
| 严重 S | S1,S2,S3,S4,S6,S7,S8 | S5（信号重复连接属实，但非"双重 rooms_updated"，投递的是不同类型消息） | 无 |
| 高 H | H1,H2,H3,H4,H5,H6,H7,H8,H9,H10,H12 | H13（traceback 技术上保留于 `__traceback__`，但未显式日志记录，实际调试不可见）, H14（devDep 缺失属实，但类型经传递依赖实际可用） | H11（列表推导式同步执行有隐式快照，`return_exceptions=True` 处理断连异常） |
| 中 M | M1-M11,M13-M22,M23-M29 | 无 | M12（重连由 websocket.ts 内部 `onclose → scheduleReconnect` 保证）, M30（实际为 1.1.0 正式版，非 beta） |
| 低 L | L1-L20 | L22（文案在窄屏下略有误导，常规宽度下准确） | 无 |

**不修复项（验证不存在）：**
- H11：`server.py:86` 的 `[client.send(message) for client in self.clients]` 列表推导式同步执行，隐式快照集合；`return_exceptions=True` 已处理断连异常。无需修改。
- M12：首次 connect 失败后，websocket.ts 的 `onclose` 会无条件调用 `scheduleReconnect()`，3 秒后自动重连。仅存在重连过程中 `connectionStatus` 不更新为 `'connecting'` 的次要状态显示瑕疵（归入 M11 类型安全修复一并处理）。
- M30：`vite-plugin-electron` 实际为 1.1.0 正式版（非 beta）。版本偏旧是事实，但原描述错误，不作为问题修复。（可选：升级到 2.x 稳定版，但非必须）

**S5 重新定性：**
原描述"推送两条相同的 rooms_updated 消息"经验证不成立。两处连接投递的是**不同类型**消息：
- `message_bridge.py:34-36` 的槽函数投递 `room_connect_finished`/`recording_started`/`recording_stopped` 事件通知（含 success/error 详情）
- `room_handler.py:353-355` 的 `_queue_rooms_update` 投递 `rooms_updated` 状态刷新（含完整房间列表）

两者互补，非重复。**但信号连接分散在两处仍属代码组织问题**，修复方案改为：保留两处连接（功能不同），但将 handler 中的连接注释清楚其与 bridge 的职责区分，避免后续误删。不作为严重问题处理，降级为代码质量改进。

## What Changes

### 严重问题（S1-S8，必须修复）
- **S1**：移除 `preload.ts` 中的死代码 `sendToPython`/`onPythonMessage`（IPC 链路从未在主进程注册 `python-message` handler，实际走 WebSocket），避免双通道混淆。同时清理 M6（`onPythonMessage` 监听器无法移除）。
- **S2**：`open-path` IPC 增加路径白名单校验，仅允许打开 output 目录及其下文件，禁止可执行扩展名（.exe/.bat/.ps1/.cmd/.vbs/.scr）。同时修复 M5（handler 未 return Promise 结果）。
- **S3**：`_handle_mse_preview` enable 分支中 `mgr.get_room(room_id)`、`room.stream_info`、`room.preview_enabled` 的直接访问改为通过 `bridge.call()` 跨线程调用（disable 分支已正确桥接，参考其实现）。
- **S4**：`_mse_streamers` 字典增加 `threading.Lock` 保护，所有读写（含 `run_in_executor` 线程池的第 910 行写入）均需持锁。
- **S5**（降级为代码质量改进）：保留两处信号连接（功能不同），在 `room_handler.py:353-355` 添加注释说明与 `message_bridge.py:34-36` 的职责区分，避免后续误删。
- **S6**：`_get_current_pos` 修复 `if pos:` 在 `pos=0` 时的 falsy 判断，改为 `if pos is not None:`。
- **S7**：`start.py:22` 与 `server.py:105` 补齐 `bridge` 参数：构造 `QtManagerBridge` 与 `MultiRoomManager` 实例（参考 `main.py` 的初始化逻辑），调用 `register_room_handlers(server, bridge)`。
- **S8**：`spawnBackend()` 增加 `detectPython()` 检测 `python`/`python3` 可用性，失败时通过 IPC 通知前端显示明确错误页；`package.json` build 配置考虑携带嵌入式 Python 或文档说明使用完整安装包。

### 高优先级问题（H1-H15，H11 不修复）
- **H1**：WebSocket 重连改为指数退避（1s→2s→4s→8s→15s 封顶），`disconnect()` 设置 `manualClose` 标志，`onclose` 检测后不重连。
- **H2**：`connect()` 开头检查并关闭已有连接，避免泄漏。
- **H3**：`send()` 实现真正的消息队列，断连时入队（上限 100），重连后 flush。
- **H4**：`preview_frame` 更新使用 `requestAnimationFrame` 节流，每帧只触发一次 store 更新。
- **H5**：`killBackend` POSIX 分支改为 `setTimeout` 异步轮询，不阻塞主进程。
- **H6**：批量录制/停止增加 `Modal.confirm` 二次确认（两函数绑定快捷键易误触）。
- **H7**：停止单个录制增加 `Modal.confirm` 二次确认（参考删除房间的确认实现）。
- **H8**：导出重试携带原始 `preset_id`，从 ExportJob 对象读取（创建 job 时需保存 preset_id）。
- **H9**：取消导出时通过 WebSocket 通知后端 `cancel_export`，后端终止 FFmpeg 进程。
- **H10**：Workbench `selectedRoomIds` 从 store 的 `selectedRoomId` 初始化，并在 store 变化时同步（当前 useEffect 仅单向同步到 store，从不反向初始化）。
- **H12**：处理器异常时向客户端发送 `{type: '<msg_type>_response', data: {success: false, error: str(e)}}`，前端不永久等待。
- **H13**：`bridge.call()` 在捕获异常时显式记录 `traceback.format_exc()` 到日志（虽然 `__traceback__` 技术上保留，但当前日志只打印 `str(e)` 无 traceback）。
- **H14**：`package.json` devDependencies 显式声明 `@types/node`（虽然经传递依赖可用，但显式声明更健壮）。
- **H15**：`vite.config.ts` external 增加 `path`/`fs`/`child_process`/`os`/`crypto` 等 Node 内置模块。

### 中优先级问题（M1-M30，M12/M30 不修复）
- **Electron 主进程**：M1 catch 块增加日志（15 处无日志）；M2 重复创建前关闭旧流；M3 IPC handler 移到 `app.whenReady` 顶层注册一次（当前在 `createWindow` 内，macOS 激活会二次注册）；M4 托盘失败时确保 `before-quit` 可退出（仅当 `minimizeToTray=true` 时才无法退出）；M7 过滤环境变量（仅透传必要项 PATH/USERPROFILE/APPDATA/TEMP）；M8 `hiddenInset` 仅 macOS 应用。
- **前端**：M9 `addRoom` 按 room_id 去重；M10 `addClip` 限制上限 200 条；M11 `connectionStatus` 改为字面量联合类型（同时修复 M12 的状态显示瑕疵）；M13-M15 MSE 清理与自动播放策略处理；M16 选区试听定时器用 ref 捕获最新值；M17 多 URL 添加 loading 在全部完成后关闭；M18 磁盘空间不足 Dashboard 警告；M19 统一使用 Ant Design Select；M20 `formatTime` 抽到 utils；M21 light 主题改为独立变量集；M22 清理 `!important`（实测 45 处）。
- **Python 后端**：M23 `asyncio.get_event_loop()` 改为 `asyncio.get_running_loop()`（实测 22 处）；M24 `float()` 转换加 try/except；M25 `save_settings`/`save_rooms` 加 schema 校验；M26 `_broadcast_rooms` 加异常处理；M27 `stop()` 访问 `_server` 加 None 检查并正确调度协程；M28 WebSocket 地址改为可配置；M29 `extraResources` 改绝对路径。

### 低优先级问题（L1-L22）
- **UX/可访问性**：L1-L2 列表项加 `role`/`tabIndex`/键盘支持；L3 房间名截断加 Tooltip；L4 错误信息截断 + Tooltip；L5 导出选择器加 ARIA；L6 录制指示条加动画；L7 断开按钮加 loading；L8 统计卡片加点击交互；L9 合并重复按钮；L10 响应式适配；L11 WebSocket 断连 UI 提示。
- **代码质量**：L12 删除 `main.py` `_broadcast_loop` 死代码；L13 删除 `bridge.py` 整个文件；L14 删除 `message_bridge.py` `_shutdown` 标志；L15 `recording_history` 持久化到文件；L16 删除未用依赖 `vite-plugin-electron-renderer`；L17 `electron:build` 恢复 tsc 检查；L18 `--state-warning` 补 token；L19 RoomCard onClick 重构；L20 selectedRoomIds 同步改为 useEffect；L21 Ctrl+A 改为 Ctrl+Shift+A；L22 Empty 文案适配窄屏。

## Impact
- 受影响代码（全栈）：
  - `lsc-electron/electron/main.ts`：IPC handler 整理、路径校验、killBackend 异步化、托盘兜底、环境变量过滤、Python 检测
  - `lsc-electron/electron/preload.ts`：移除死代码
  - `lsc-electron/src/services/websocket.ts`：重连退避、连接泄漏修复、消息队列、地址可配置
  - `lsc-electron/src/hooks/useWebSocket.ts`：preview_frame 节流、connectionStatus 类型与状态显示
  - `lsc-electron/src/store/appStore.ts`：addRoom 去重、addClip 上限
  - `lsc-electron/src/pages/Workbench/index.tsx`：确认对话框、取消导出通知后端、selectedRoomIds 同步、loading 修复、formatTime 抽取
  - `lsc-electron/src/pages/Workbench/components/RoomCard.tsx`：停止确认、Tooltip、ARIA、onClick 重构
  - `lsc-electron/src/pages/Dashboard/index.tsx`：磁盘警告、可访问性、重复按钮合并
  - `lsc-electron/src/services/mediaSourcePlayer.ts`：cleanup 修复、自动播放处理
  - `lsc-electron/src/utils/time.ts`（新增）：formatTime 公共工具
  - `lsc-electron/vite.config.ts`：external Node 模块
  - `lsc-electron/package.json`：补 @types/node、删未用依赖、electron:build 恢复 tsc
  - `lsc-electron/src/styles/tokens.css`/`global.css`：light 主题变量、清理 !important、补 warning token
  - `python-backend/handlers/room_handler.py`：MSE 跨线程修复、_mse_streamers 加锁、S5 注释、_get_current_pos 修复、asyncio API 升级、float 校验、_broadcast_rooms 异常处理、recording_history 持久化
  - `python-backend/message_bridge.py`：traceback 显式日志、_shutdown 清理
  - `python-backend/server.py`：异常响应（H12）、start.py 入口修复、stop 同步
  - `python-backend/start.py`：补 bridge 参数
  - `python-backend/main.py`：删除死代码 _broadcast_loop、stop() 修复
  - `python-backend/bridge.py`：删除整个文件
- 不破坏现有路由与 store 数据结构（仅扩展与修正字段）。
- 不改变 Qt 桌面端预览路径。
- 严重问题 S1-S8 修复后核心链路（IPC、安全、线程安全、入口可用性）才真正可用。

## ADDED Requirements

### Requirement: IPC 路径安全校验
The system SHALL `open-path` IPC 仅允许打开白名单目录内的文件，禁止执行可执行文件。

#### Scenario: 打开合法输出文件
- **WHEN** 前端请求打开 `output/房间名/切片.mp4`
- **THEN** 主进程校验路径在 output 目录内
- **AND** 扩展名不在黑名单（.exe/.bat/.ps1/.cmd/.vbs/.scr）
- **AND** 调用 `shell.openPath` 打开
- **AND** handler 返回 Promise 结果给前端

#### Scenario: 拒绝可执行文件
- **WHEN** 前端请求打开 `恶意.exe`
- **THEN** 主进程返回 `{success:false, error:'不允许打开此类型文件'}`
- **AND** 不调用 `shell.openPath`
- **AND** 日志记录拒绝事件

### Requirement: WebSocket 健壮重连
The system SHALL WebSocket 断线后使用指数退避重连，手动断开不重连。

#### Scenario: 异常断线自动重连
- **WHEN** WebSocket 连接异常关闭
- **THEN** 按 1s→2s→4s→8s→15s 间隔重连
- **AND** 重连成功后 flush 消息队列

#### Scenario: 手动断开不重连
- **WHEN** 调用 `disconnect()`
- **THEN** 设置 `manualClose` 标志
- **AND** `onclose` 触发时不调度重连

#### Scenario: 重复调用 connect 不泄漏
- **WHEN** `connect()` 被多次调用
- **THEN** 开头关闭已有旧连接
- **AND** 不创建多个 WebSocket 实例

### Requirement: 破坏性操作二次确认
The system SHALL 批量录制、批量停止、停止单个录制执行前弹出确认对话框。

#### Scenario: 用户点击批量录制
- **WHEN** 用户点击"批量录制"按钮或触发 Ctrl+R 快捷键
- **THEN** 弹出 `Modal.confirm` 显示将影响的房间数
- **AND** 用户确认后才执行

#### Scenario: 用户点击停止录制
- **WHEN** 用户点击单个房间的"停止"按钮
- **THEN** 弹出确认对话框（参考删除房间的确认实现）
- **AND** 确认后调用 `onStopRecord`

### Requirement: 后端异常响应
The system SHALL WebSocket 消息处理器异常时返回错误响应，前端不永久等待。

#### Scenario: 处理器抛异常
- **WHEN** 处理器 `handle_xxx` 抛出异常
- **THEN** 向客户端发送 `{type: 'xxx_response', data: {success: false, error: str(e)}}`
- **AND** 日志记录完整 traceback（`traceback.format_exc()`）

### Requirement: Python 跨线程访问安全
The system SHALL asyncio 线程访问 Qt 对象必须通过 `bridge.call()`，禁止直接访问。

#### Scenario: MSE 预览启用
- **WHEN** asyncio 线程处理 `enable_mse_preview`
- **THEN** `mgr.get_room(room_id)` 与 `room.stream_info` 通过 `bridge.call` 调用（参考 disable 分支实现）
- **AND** `_mse_streamers` 字典读写持锁

## MODIFIED Requirements

### Requirement: Python 后端启动
[原：主进程 spawn `python main.py`，假设系统有 python 命令]
[改为：主进程启动前检测 python 可用性，优先使用捆绑解释器，fallback 系统 python；失败时前端显示错误并指引安装]

### Requirement: WebSocket 消息发送
[原：send() 断连时日志称 queuing 但实际直接丢弃]
[改为：send() 断连时入队（上限 100），重连后 flush]

### Requirement: 导出取消
[原：handleCancelJob 仅改前端状态，后端 FFmpeg 继续运行]
[改为：handleCancelJob 通过 WebSocket 发送 cancel_export，后端终止 FFmpeg 进程]

## REMOVED Requirements

### Requirement: IPC python-message 通道
**Reason**: `sendToPython`/`onPythonMessage` 从未在主进程注册 handler，是死代码；实际通信走 WebSocket，双通道会引起混淆。同时解决 M6 监听器无法移除问题。
**Migration**: 移除 `preload.ts` 中这两个 API，前端统一使用 WebSocket。

### Requirement: bridge.py LSCBridge 类
**Reason**: 死代码，全项目无任何外部导入，实际使用 `message_bridge.py` 的 `QtManagerBridge`。
**Migration**: 直接删除文件。

### Requirement: main.py _broadcast_loop
**Reason**: 死代码，从未被调用，实际使用协程版 `_broadcast_coroutine`。
**Migration**: 删除函数。

### Requirement: message_bridge.py _shutdown 标志
**Reason**: 初始化为 False 后从未被设为 True 或读取，是死属性。
**Migration**: 删除该属性。
