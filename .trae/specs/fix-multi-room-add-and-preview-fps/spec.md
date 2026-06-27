# 多房间添加与预览帧率修复 Spec

## Why
用户反馈两个问题：
1. **只能添加一个房间**：添加第一个房间后，再添加第二个房间无响应（前端不显示新房间，也无错误提示）。根因可能是 `bridge.call` 超时或后端异常被静默吞掉，前端完全无感知。
2. **预览帧率太低很卡**：当前 FFmpeg 抓帧 `fps=3`，`_frame_pusher` 间隔 333ms，实际帧率仅 ~3fps，画面卡顿明显不可用。

## What Changes
- **多房间添加修复**：
  - 后端 `handle_add_room` 增加异常捕获，失败时返回 `{'success': False, 'error': '...'}` 而非静默吞掉
  - 后端 `handle_add_room` 增加 `bridge.call` 超时时间到 30s（`add_room` 需在 Qt 主线程创建 RecordingController + QTimers，可能被 global_tick 阻塞）
  - 前端 `handleAddRoom` 增加 `add_room_response` 错误提示（message.error），让用户感知失败
  - 前端 `handleAddRoom` 修复 `setLoading(true)`/`setLoading(false)` 同步执行导致 loading 状态无效的问题（改为在 `add_room_response` 后才 `setLoading(false)`）
- **预览帧率提升**：
  - FFmpeg 抓帧 `fps` 从 3 提升到 8（`frame_capture.py` 构造默认值）
  - `_frame_pusher` 间隔从 333ms 降低到 125ms（manager.py `_ensure_frame_pusher`）
  - 自适应降级：当并发预览房间数 ≥ 3 时，自动降低 fps 到 5，避免 CPU 过载

## Impact
- 受影响代码：
  - `python-backend/handlers/room_handler.py`：`handle_add_room` 异常处理 + 超时
  - `lsc/core/services/frame_capture.py`：fps 默认值从 3 改为 8
  - `lsc/gui/multi_room/manager.py`：`_ensure_frame_pusher` 间隔 125ms + `_start_preview_electron` 自适应 fps
  - `lsc-electron/src/pages/Workbench/index.tsx`：`handleAddRoom` loading 状态修复 + 错误提示
- 不影响 Qt 桌面端预览路径
- 不影响录制功能

## ADDED Requirements

### Requirement: 多房间添加错误反馈
The system SHALL 在添加房间失败时向前端返回错误信息，前端展示错误提示。

#### Scenario: 添加房间成功
- **WHEN** 用户输入直播间链接并点击"添加"
- **THEN** 后端创建房间并广播 `rooms_updated`
- **AND** 前端 store 更新，新房间卡片出现
- **AND** 输入框清空，loading 状态恢复

#### Scenario: 添加房间失败（bridge.call 超时）
- **WHEN** Qt 主线程被阻塞导致 `bridge.call` 超时
- **THEN** 后端返回 `{'success': False, 'error': '添加房间超时，请重试'}`
- **AND** 前端显示 `message.error` 错误提示
- **AND** loading 状态恢复，输入框内容保留供用户重试

#### Scenario: 添加房间失败（达到上限）
- **WHEN** 房间数已达 MAX_ROOMS(12)
- **THEN** 后端返回 `{'success': False, 'error': '房间数已达上限'}`
- **AND** 前端显示错误提示

### Requirement: 预览帧率提升
The system SHALL 预览帧率提升到 ~8fps，保证画面流畅可辨。

#### Scenario: 单房间预览
- **WHEN** 1 个房间启用预览
- **THEN** FFmpeg 抓帧 fps=8，推送间隔 125ms
- **AND** 实际帧率 ~8fps，画面流畅
- **AND** 单帧大小约 15-30KB，带宽占用 ~120-240KB/s

#### Scenario: 多房间并发预览自适应降级
- **WHEN** 3 个及以上房间同时启用预览
- **THEN** FFmpeg 抓帧自动降为 fps=5，推送间隔保持 125ms
- **AND** 实际帧率 ~5fps，平衡 CPU 负载与流畅度

## MODIFIED Requirements

### Requirement: 添加房间流程
[原有流程：前端 send('add_room') → 后端 bridge.call(_add) → 广播 rooms_updated → 前端 store 更新]
[修改为：前端 send('add_room') 并 setLoading(true) → 后端 bridge.call(_add, timeout=30) → 成功则广播 rooms_updated + 返回 {success:true} → 失败则返回 {success:false, error:msg} → 前端根据响应 setLoading(false) + 错误时 message.error]
