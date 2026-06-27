# 修复预览状态闪烁 Spec

## Why
用户点击"启用预览"后，房间状态在"预览前"（显示"启用预览"按钮）和"预览后"（显示 VideoPreview）之间反复闪动，导致 UI 不可用。根因是 VideoPreview 组件的 `useEffect` 依赖不稳定导致 init/stop 循环，且 VideoPreview 与父组件（RoomCard/Workbench）职责重叠，双方都向后端发送 `enable_preview` 请求，后端状态在 true/false 之间反复切换。

## What Changes
- **VideoPreview 组件职责收窄**：移除 `initPlayer` 中的 `send('enable_preview', { enabled: true })` 和 `stopPlayer` 中的 `send('enable_preview', { enabled: false })`。VideoPreview 只负责 MsePlayer 生命周期管理和渲染，不再与后端通信。`enable_preview` 请求由父组件（RoomCard → Workbench.handleTogglePreview）统一发送。
- **VideoPreview useEffect 依赖稳定化**：`initPlayer`/`stopPlayer` 用 `useRef` 包裹或移除不稳定依赖（`send`/`onReady`/`onError`），确保组件重新渲染时不触发 init/stop 循环。
- **VideoPreview stopPlayer 拆分**：将"清理本地 MsePlayer"（cleanup）与"通知后端停止"（send disable）分离。useEffect cleanup 只清理本地资源，不通知后端。
- **后端 MSE 失败自动清理**：MseStreamer 的 `on_error` 回调中，除了广播 `mse_error`，还应自动清理 `preview_enabled = False` 和 `_mse_streamers` 字典，并广播 `rooms_updated`，确保前端状态与后端一致。
- **后端 enable_preview 防重入**：MSE 已在运行时直接返回成功，不重复启动。MSE 正在启动中时拒绝重复请求。

## Impact
- 受影响代码：
  - `lsc-electron/src/components/VideoPreview.tsx`：移除 `enable_preview` 通信，稳定 useEffect 依赖，拆分 cleanup 与 stop 逻辑
  - `python-backend/handlers/room_handler.py`：MseStreamer `on_error` 回调增加状态清理，`_handle_mse_preview` 增加防重入检查
- 不影响 Qt 桌面端预览路径
- 不影响录制功能
- 不影响 frame 模式预览（仅影响 MSE 模式）

## ADDED Requirements

### Requirement: VideoPreview 组件不与后端通信
VideoPreview SHALL 只负责 MsePlayer 的创建、销毁和渲染，SHALL NOT 向后端发送任何 WebSocket 消息。`enable_preview` 请求由父组件（RoomCard → Workbench.handleTogglePreview）统一发送。

#### Scenario: 用户点击启用预览
- **WHEN** 用户点击 RoomCard 中的"启用预览"按钮
- **THEN** Workbench.handleTogglePreview 发送 `enable_preview { enabled: true, mode: 'mse' }`
- **AND** 后端启动 MSE 流，设置 `preview_enabled = True`，广播 `rooms_updated`
- **AND** 前端收到 `rooms_updated`，`preview_enabled` 变为 `true`
- **AND** RoomCard 渲染 VideoPreview 组件
- **AND** VideoPreview 创建 MsePlayer 并开始接收 MSE 段
- **AND** VideoPreview 不发送 `enable_preview` 请求（避免重复）

#### Scenario: 用户点击停止预览
- **WHEN** 用户点击 RoomCard 中的"停止预览"按钮
- **THEN** Workbench.handleTogglePreview 发送 `enable_preview { enabled: false, mode: 'mse' }`
- **AND** 后端停止 MSE 流，设置 `preview_enabled = False`，广播 `rooms_updated`
- **AND** 前端收到 `rooms_updated`，`preview_enabled` 变为 `false`
- **AND** RoomCard 卸载 VideoPreview 组件
- **AND** VideoPreview 的 useEffect cleanup 只清理本地 MsePlayer，不发送 `enable_preview`

#### Scenario: VideoPreview 组件重新渲染
- **WHEN** VideoPreview 因父组件重新渲染而重新渲染（但 `active` 未变）
- **THEN** VideoPreview 的 useEffect 不触发 init/stop 循环
- **AND** MsePlayer 保持运行
- **AND** 不向后端发送任何 `enable_preview` 请求

### Requirement: 后端 MSE 失败自动清理状态
后端 SHALL 在 MseStreamer 的 `on_error` 回调中自动清理预览状态，包括 `preview_enabled = False`、`_mse_streamers` 字典移除、广播 `rooms_updated`，确保前端状态与后端一致。

#### Scenario: FFmpeg 进程崩溃
- **WHEN** MseStreamer 的 FFmpeg 进程意外退出
- **THEN** `on_error` 回调广播 `mse_error` 消息
- **AND** `on_error` 回调通过 bridge.call 在 Qt 主线程设置 `room.preview_enabled = False`
- **AND** `on_error` 回调从 `_mse_streamers` 字典移除该 streamer
- **AND** `on_error` 回调广播 `rooms_updated`
- **AND** 前端收到 `rooms_updated`，`preview_enabled` 变为 `false`
- **AND** RoomCard 卸载 VideoPreview，显示"启用预览"按钮

### Requirement: 后端 enable_preview 防重入
后端 SHALL 在 MSE 已运行或正在启动时拒绝重复启动请求，直接返回成功或"正在启动中"。

#### Scenario: MSE 已在运行时重复请求启用
- **WHEN** 前端发送 `enable_preview { enabled: true, mode: 'mse' }` 但 MSE 已在运行
- **THEN** 后端直接返回 `{'success': True, 'note': 'already streaming'}`
- **AND** 不重复创建 MseStreamer
- **AND** 不重复设置 `preview_enabled`

## MODIFIED Requirements

### Requirement: VideoPreview useEffect 依赖稳定
VideoPreview 的 useEffect SHALL 只依赖 `active` 和 `roomId`，SHALL NOT 依赖不稳定的函数引用（`initPlayer`/`stopPlayer`/`send`/`onReady`/`onError`）。不稳定引用 SHALL 通过 `useRef` 包裹或移到 effect 内部。

#### Scenario: 父组件重新渲染
- **WHEN** 父组件（RoomCard）重新渲染，传入新的 `send`/`onReady`/`onError` 引用
- **THEN** VideoPreview 的 useEffect 不重新触发
- **AND** MsePlayer 保持运行状态
- **AND** 不出现 init/stop 循环
