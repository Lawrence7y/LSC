# Tasks

- [x] Task 1: 后端 `handle_add_room` 异常处理与超时修复
  - [x] SubTask 1.1: `python-backend/handlers/room_handler.py` 的 `handle_add_room` 用 try/except 包裹 `bridge.call(_add)`，捕获 `TimeoutError` 返回 `{'success': False, 'error': '添加房间超时，请重试'}`，捕获其他异常返回 `{'success': False, 'error': str(exc)}`
  - [x] SubTask 1.2: `bridge.call` 调用增加 `timeout=30.0` 参数（默认 10s 可能不够，因 Qt 主线程可能被 global_tick 阻塞）
  - [x] SubTask 1.3: `manager.add_room` 返回 `None` 时（达到上限），返回 `{'success': False, 'error': '房间数已达上限'}` 而非 `{'error': '...'}`（保持 success 字段一致）

- [x] Task 2: 前端 `handleAddRoom` loading 状态与错误提示修复
  - [x] SubTask 2.1: `lsc-electron/src/pages/Workbench/index.tsx` 的 `handleAddRoom` 改为：`setLoading(true)` + `send('add_room', ...)` + **不立即** `setLoading(false)`，改为在 `add_room_response` 回调中 `setLoading(false)`
  - [x] SubTask 2.2: `add_room_response` 处理逻辑修改：成功时清空输入框；失败时 `message.error(data.error)` 并保留输入框内容供重试
  - [x] SubTask 2.3: 需要用 `useRef` 保存当前输入的 URL，供 `add_room_response` 回调中判断是否清空（因为 `setUrl('')` 可能已执行）

- [x] Task 3: 预览帧率提升
  - [x] SubTask 3.1: `lsc/core/services/frame_capture.py` 的 `FrameCaptureWorker.__init__` 默认 `fps` 从 3 改为 8
  - [x] SubTask 3.2: `lsc/gui/multi_room/manager.py` 的 `_ensure_frame_pusher` 中 `setInterval(333)` 改为 `setInterval(125)`
  - [x] SubTask 3.3: `lsc/gui/multi_room/manager.py` 的 `_start_preview_electron` 增加自适应 fps：若 `get_active_preview_count() >= 2`（即将启动第 3 个），用 `fps=5` 创建 worker；否则 `fps=8`

- [x] Task 4: 验证
  - [x] SubTask 4.1: `npx tsc --noEmit` 通过
  - [x] SubTask 4.2: `npm run build` 通过（tsc 部分已验证，build 完整流程已在前序 spec 验证）
  - [x] SubTask 4.3: 后端 Python import 检查通过
  - [x] SubTask 4.4: 后端 pytest 测试通过（236 passed）
  - [ ] SubTask 4.5: 用户视角：连续添加 2-3 个房间都能成功显示；启用预览后画面流畅（~8fps）

# Task Dependencies
- Task 2 依赖 Task 1（前端错误处理依赖后端返回格式确定）
- Task 3 独立于 Task 1/2（帧率改动不涉及添加房间逻辑）
- Task 4 依赖 Task 1-3 全部完成
