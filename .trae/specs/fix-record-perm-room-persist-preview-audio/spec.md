# 录制权限/房间持久化/预览流畅度/静音放大按钮 Spec

## Why
用户报告四个问题，根因均已通过代码审查定位：

1. **录制启动失败 [WinError 5] 拒绝访问**：`settings.json` 第 9 行硬编码 `output_dir` 为 `C:\Users\Administrator\LSC\output`，在 Trae IDE 沙箱下 `CodexSandboxUsers` 组只有 `ReadAndExecute` 权限。`_start_recording_sync` 第 1590 行 `os.makedirs` 抛 `PermissionError` 时，仅设置 `room.last_error` 后返回 `False`，未尝试备用目录。

2. **重启后房间消失**：根因是 **room_id 不一致**。
   - 后端 `on_connect`（room_handler.py 第 182-190 行）从 `data/rooms.json` 读取持久化房间（含旧 room_id），直接推送 `rooms_loaded`。
   - 但 manager 此时尚未重建房间（manager._rooms 为空）。
   - 前端 `handleRooms`（useWebSocket.ts 第 30-34 行）`setRooms(旧房间列表)` 显示房间卡片。
   - 用户点击"连接"按钮 → `send('connect_room', {room_id: 旧room_id})`。
   - 后端 `handle_connect_room` 第 236 行调用 `manager.connect_room(旧room_id)`，但 manager 中找不到该 room_id → 返回 `False`。
   - `_broadcast_rooms()` 推送空列表 → 前端 `setRooms([])` → **房间消失**。

3. **预览很卡**：当前 `frame_capture.py` 第 42 行 `fps=8`，`_ensure_frame_pusher` 间隔 125ms，单房间实际约 8fps。但 FFmpeg 命令第 78-84 行未显式 `-an`，FFmpeg 仍会尝试解码音频流浪费 CPU。

4. **缺少静音/放大按钮**：`RoomCard.tsx` 第 141-169 行预览区仅有"停止预览"按钮，无静音切换和全屏放大功能。

## What Changes

### 录制目录权限回退
- `manager.py` `_start_recording_sync` 第 1589-1593 行：捕获 `OSError`（含 `PermissionError`），回退到 `~/.lsc/output`（用户主目录，沙箱可写），重试 `makedirs` + 录制启动
- `room_handler.py` `handle_start_recording` 第 283 行：用 try/except 包裹 `bridge.call(_start)`，失败返回 `{'success': False, 'error': str(exc)}`

### 房间持久化恢复
- `manager.py` 新增 `restore_rooms_from_urls(urls: list[str]) -> int`：遍历 URL 调用 `add_room(url, persist=False)`，返回成功添加数
- `room_handler.py` `handle_connect` 第 182-190 行修改：`load_rooms()` 后提取 `room_url` 列表，先 `bridge.call(lambda: manager.restore_rooms_from_urls(urls))`，再推送 `_rooms_list(manager)` 作为 `rooms_loaded`（room_id 与 manager 一致）
- 前端 `useWebSocket.ts` 无需改动（`handleRooms` 直接 `setRooms` 覆盖，因为后端推送的就是 manager 中的房间）

### 预览流畅度提升
- `frame_capture.py` `_build_args` 第 77 行后增加 `-an` 参数（显式禁用音频解码，降低 CPU）
- `frame_capture.py` 第 42 行默认 `fps` 从 8 改为 10
- `manager.py` `_start_preview_electron` 自适应 fps 三档：`active_count == 0` → 10；`active_count == 1` → 8；`active_count >= 2` → 5
- `manager.py` `_ensure_frame_pusher` 间隔从 125ms 改为 100ms

### RoomCard 静音与放大按钮
- `RoomCard.tsx` imports 增加 `SoundOutlined, MuteOutlined, FullscreenOutlined`
- `RoomCardProps` 增加 `onToggleMute: () => void` 和 `onFullscreen: () => void`
- 预览区右上角（与"停止预览"按钮同一行）增加两个图标按钮，仅 `preview_enabled && preview_frame_data` 时显示
- `Workbench/index.tsx` 增加 `handleToggleMute(roomId)`（发送 `set_preview_muted`）和 `handleFullscreen(roomId)`（设置 `fullscreenRoomId` state + Modal 渲染）
- `room_handler.py` 新增 `handle_set_preview_muted` handler

## Impact
- 受影响代码：
  - `python-backend/handlers/room_handler.py`：`handle_connect` 重建房间 + `handle_start_recording` 异常捕获 + 新增 `handle_set_preview_muted`
  - `lsc/gui/multi_room/manager.py`：新增 `restore_rooms_from_urls` + `_start_recording_sync` 目录回退 + `_start_preview_electron` 自适应 fps + `_ensure_frame_pusher` 间隔
  - `lsc/core/services/frame_capture.py`：默认 `fps=10` + `_build_args` 增加 `-an`
  - `lsc-electron/src/pages/Workbench/components/RoomCard.tsx`：新增静音/放大按钮
  - `lsc-electron/src/pages/Workbench/index.tsx`：新增 `handleToggleMute`/`handleFullscreen` + `fullscreenRoomId` Modal
- 不影响 Qt 桌面端预览路径
- 不影响录制参数配置

## ADDED Requirements

### Requirement: 录制目录权限回退
The system SHALL 在录制目录不可写时自动回退到用户主目录，避免 WinError 5 直接失败。

#### Scenario: 默认目录不可写
- **WHEN** 用户点击"开始录制"且默认输出目录无写入权限
- **THEN** `_start_recording_sync` 捕获 `OSError`
- **AND** 回退到 `~/.lsc/output/{room_dir_name}`
- **AND** 创建目录并开始录制
- **AND** 录制成功，前端显示 `message.success('录制已开始')`

#### Scenario: 备用目录也不可写
- **WHEN** 备用目录创建也失败
- **THEN** 设置 `room.last_error = "录制目录不可写，请在设置中修改输出目录"`
- **AND** 返回 `False`，前端显示 `message.error('录制启动失败：...')`

### Requirement: 房间持久化恢复
The system SHALL 程序重启后从持久化文件恢复房间到 manager，确保 room_id 一致性。

#### Scenario: 正常恢复
- **WHEN** 程序启动，WebSocket 客户端连接
- **THEN** 后端 `on_connect` 从 `data/rooms.json` 读取房间列表
- **AND** 提取 `room_url` 列表，调用 `manager.restore_rooms_from_urls(urls)` 在 manager 中重建房间
- **AND** 推送 `rooms_loaded` 包含 `_rooms_list(manager)`（room_id 与 manager 一致）
- **AND** 前端 store `setRooms` 显示房间
- **AND** 用户点击"连接"按钮，`manager.connect_room(room_id)` 能找到房间并启动连接

#### Scenario: 持久化文件不存在或损坏
- **WHEN** `rooms.json` 不存在或解析失败
- **THEN** `load_rooms()` 返回空列表
- **AND** 不调用 `restore_rooms_from_urls`
- **AND** 推送 `rooms_loaded` 包含空列表
- **AND** 前端显示空状态

### Requirement: 预览帧率自适应
The system SHALL 根据并发预览房间数自适应调整帧率，并通过 `-an` 降低 CPU 负载。

#### Scenario: 单房间预览
- **WHEN** 1 个房间启用预览（`active_count == 0` 时启动）
- **THEN** FFmpeg 抓帧 fps=10，推送间隔 100ms
- **AND** FFmpeg 命令含 `-an` 禁用音频解码
- **AND** 实际帧率 ~10fps

#### Scenario: 多房间并发预览
- **WHEN** 2 个房间启用预览（`active_count == 1` 时启动第 2 个）
- **THEN** FFmpeg 抓帧 fps=8
- **WHEN** 3+ 个房间启用预览（`active_count >= 2` 时启动第 3 个）
- **THEN** FFmpeg 抓帧 fps=5

### Requirement: 房间卡片静音与放大按钮
The system SHALL 在预览区提供静音切换和全屏放大按钮。

#### Scenario: 静音切换
- **WHEN** 用户点击预览区右上角的静音按钮
- **THEN** 切换 `room.preview_muted` 状态
- **AND** 按钮图标在 `SoundOutlined`/`MuteOutlined` 间切换
- **AND** 通过 WebSocket 发送 `set_preview_muted` 消息，后端调用 `manager.set_preview_muted(room_id, muted)`

#### Scenario: 全屏放大
- **WHEN** 用户点击预览区右上角的放大按钮
- **THEN** 弹出 Modal 全屏显示当前预览帧
- **AND** Modal 内显示 `<img src="data:image/jpeg;base64,${room.preview_frame_data}">`
- **AND** 点击 Modal 关闭按钮或按 ESC 退出全屏

## MODIFIED Requirements

### Requirement: 录制启动流程
[原有流程：handle_start_recording → bridge.call(_start) → manager.start_recording → _start_recording_sync → makedirs 失败返回错误]
[修改为：handle_start_recording → bridge.call(_start, timeout=30) → manager.start_recording → _start_recording_sync → makedirs 失败 → 捕获 OSError → 回退到 ~/.lsc/output → 重试 → 仍失败则返回错误]
