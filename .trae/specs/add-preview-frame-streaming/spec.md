# 直播预览帧推送 Spec

## Why
当前 Electron 前端点击"启用预览"后只显示"预览已启用（mpv 渲染中）"占位文案，无实际画面。根因是后端 mpv 通过 `wid` 绑定 Qt 窗口渲染，而 Electron 端无 Qt 窗口宿主，`_ensure_mpv` 因 `isVisible()=False` 永远失败，mpv 从未初始化。需要为 Electron 场景实现独立的帧推送路径：FFmpeg 从直播流抓帧 → JPEG → WebSocket 推送 → 前端 `<img>` 渲染。

## What Changes
- 新增 `FrameCaptureWorker`：用 FFmpeg 子进程从直播流抓帧（`-vf fps=3` + mjpeg 编码到 stdout），Python 读取 JPEG bytes
- 新增 `FramePusher`：QTimer 定时从各房间的 FrameCaptureWorker 取最新帧，base64 编码后通过 `bridge.queue_broadcast` 推送 `preview_frame` 消息
- `manager.py` 的 `start_preview`/`stop_preview` 针对 Electron 场景创建/销毁 FrameCaptureWorker（不创建 MpvWidget）
- `server.py` 调大 `max_size` 到 16MB（base64 JPEG 帧可能超过默认 1MB）
- 前端 `useWebSocket.ts` 新增 `preview_frame` 消息处理，更新 store 中的 `preview_frame_data`（base64 字符串）
- 前端 `RoomCard.tsx` 预览区：`preview_enabled && preview_frame_data` 时用 `<img src="data:image/jpeg;base64,...">` 渲染画面

## Impact
- 受影响代码：
  - `python-backend/handlers/room_handler.py`：enable_preview 区分 electron 模式
  - `python-backend/message_bridge.py`：新增 frame 广播队列
  - `python-backend/server.py`：调大 max_size
  - `lsc/gui/multi_room/manager.py`：start_preview/stop_preview 增加 electron 分支
  - `lsc-electron/src/types/index.ts`：RoomSession 增加 preview_frame_data 字段
  - `lsc-electron/src/hooks/useWebSocket.ts`：处理 preview_frame 消息
  - `lsc-electron/src/store/appStore.ts`：增加 preview frame 状态更新
  - `lsc-electron/src/pages/Workbench/components/RoomCard.tsx`：渲染 `<img>` 画面
- 新增文件：
  - `lsc/core/services/frame_capture.py`：FrameCaptureWorker 类
- 不破坏 Qt 桌面端的 wid 预览路径（通过模式判断分支）
- 不影响录制功能（FFmpeg 抓帧是独立进程，与录制进程互不干扰）

## ADDED Requirements

### Requirement: Electron 端直播预览画面推送
The system SHALL 在 Electron 前端启用预览时，通过 FFmpeg 从直播流抓取 JPEG 帧并经 WebSocket 推送到前端显示。

#### Scenario: 用户启用预览
- **WHEN** 用户点击"启用预览"按钮
- **THEN** 后端启动 FFmpeg 子进程从直播流抓帧（fps=3, 480p, JPEG quality=70）
- **AND** 后端每帧通过 WebSocket 推送 `preview_frame` 消息（`{ room_id, frame_b64 }`）
- **AND** 前端 RoomCard 预览区用 `<img>` 渲染最新帧
- **AND** 预览区显示实际直播画面而非占位文案

#### Scenario: 用户禁用预览
- **WHEN** 用户点击"停止预览"按钮
- **THEN** 后端终止 FFmpeg 子进程
- **AND** 停止推送 preview_frame 消息
- **AND** 前端预览区恢复为"启用预览"按钮状态

#### Scenario: 直播流断开或抓帧失败
- **WHEN** FFmpeg 进程异常退出（流断开、网络错误）
- **THEN** 后端自动重试（最多 3 次，间隔 5 秒）
- **AND** 重试期间前端显示最后已知帧 + "重连中…"提示
- **AND** 重试耗尽后设置 `room.preview_error`，前端显示错误提示

#### Scenario: 多房间同时预览
- **WHEN** 多个房间同时启用预览
- **THEN** 每个房间独立运行 FFmpeg 抓帧进程
- **AND** 帧推送互不干扰，按 room_id 区分
- **AND** 并发预览上限保持 MAX_CONCURRENT_PREVIEWS=4

### Requirement: 预览帧性能约束
The system SHALL 预览帧推送在保证画面可辨识的前提下最小化资源占用。

#### Scenario: 单房间预览
- **WHEN** 1 个房间启用预览
- **THEN** 帧率 3 fps，分辨率 480p（854x480），JPEG quality=70
- **AND** 单帧大小约 15-30 KB，base64 后约 20-40 KB
- **AND** 带宽占用约 60-120 KB/s

#### Scenario: 四房间并发预览
- **WHEN** 4 个房间同时启用预览
- **THEN** 总带宽约 240-480 KB/s
- **AND** WebSocket max_size 已调大到 16MB 避免帧被拒绝
- **AND** 前端仅渲染当前可见房间的帧（不可见房间停止渲染但不停止抓帧）

## MODIFIED Requirements

### Requirement: 房间预览启用/禁用
[原有逻辑：start_preview 创建 MpvWidget 并嵌入 Qt 卡片]
[修改为：根据调用来源区分模式。Electron 模式创建 FrameCaptureWorker（FFmpeg 抓帧），Qt 模式保留原 MpvWidget 逻辑]

## REMOVED Requirements

无（不移除 Qt wid 预览路径，仅新增 Electron 分支）
