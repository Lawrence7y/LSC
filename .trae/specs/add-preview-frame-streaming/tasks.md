# Tasks

- [x] Task 1: 后端 FrameCaptureWorker 实现
  - [x] SubTask 1.1: 新建 `lsc/core/services/frame_capture.py`，实现 `FrameCaptureWorker` 类：用 `subprocess.Popen` 启动 FFmpeg 进程（`ffmpeg -i <stream_url> -vf fps=3,scale=854:480 -f image2pipe -vcodec mjpeg -q:v 7 -`），从 stdout 读取 JPEG bytes
  - [x] SubTask 1.2: 实现 JPEG 帧解析：FFmpeg stdout 是连续的 JPEG 流，用 `0xFF 0xD8`（SOI）和 `0xFF 0xD9`（EOI）标记分割单帧
  - [x] SubTask 1.3: 实现 `get_latest_frame() -> bytes | None`：返回最新已解析的 JPEG bytes，若无则返回 None
  - [x] SubTask 1.4: 实现 `stop()`：终止 FFmpeg 子进程（`process.terminate()` → 等 2 秒 → `kill`），清理资源
  - [x] SubTask 1.5: 实现 HTTP headers 透传：直播流可能需要 Referer/User-Agent，从 RoomSession 透传给 FFmpeg（`-headers` + `-user_agent`）
  - [x] SubTask 1.6: 实现异常退出检测与自动重试：FFmpeg 进程退出时，若 `retry_count < 3`，等 5 秒后重启；超限则设置 error 状态

- [x] Task 2: manager.py 集成 Electron 预览模式
  - [x] SubTask 2.1: `RoomSession` 新增 `frame_capture: FrameCaptureWorker | None = None` 字段（session.py）
  - [x] SubTask 2.2: `start_preview` 新增 `mode: str = 'qt'` 参数，`mode='electron'` 时不创建 MpvWidget，改为创建 `FrameCaptureWorker` 并启动
  - [x] SubTask 2.3: `stop_preview` 在 electron 模式下调用 `frame_capture.stop()` 并置 None
  - [x] SubTask 2.4: 新增 `_frame_pusher` QTimer（每 333ms 触发）：遍历 `preview_enabled` 且 `frame_capture` 非空的房间，取最新帧，base64 编码，通过 `bridge.queue_broadcast({'type':'preview_frame','data':{'room_id':...,'frame_b64':...}})` 投递
  - [x] SubTask 2.5: `remove_room` 时清理 `frame_capture`（stop + 置 None）
  - [x] SubTask 2.6: manager 初始化时启动 `_frame_pusher` QTimer，析构时停止

- [x] Task 3: WebSocket 通道适配
  - [x] SubTask 3.1: `server.py` 调大 `websockets.serve` 的 `max_size` 到 `16 * 1024 * 1024`（16MB），避免大帧被拒
  - [x] SubTask 3.2: `message_bridge.py` 确认 `queue_broadcast` 已支持任意 dict（preview_frame 消息复用此通道），无需额外改动

- [x] Task 4: room_handler.py enable_preview 区分模式
  - [x] SubTask 4.1: `handle_enable_preview` 从 data 读取 `mode` 字段（默认 'qt'），传给 `manager.start_preview(room_id, mode=mode)`
  - [x] SubTask 4.2: 前端 `Workbench/index.tsx` 的 `handleTogglePreview` 发送 `enable_preview` 时附带 `mode: 'electron'`

- [x] Task 5: 前端预览帧渲染
  - [x] SubTask 5.1: `types/index.ts` 的 `RoomSession` 增加 `preview_frame_data?: string` 字段（base64 JPEG）
  - [x] SubTask 5.2: `useWebSocket.ts` 新增 `preview_frame` 消息处理：解析 `{ room_id, frame_b64 }`，调用 `updateRoom(room_id, { preview_frame_data: frame_b64 })` 更新 store
  - [x] SubTask 5.3: `appStore.ts` 确认 `updateRoom` 已支持 Partial<RoomSession> 合并（preview_frame_data 自动合并）
  - [x] SubTask 5.4: `RoomCard.tsx` 预览区逻辑调整：`preview_enabled && preview_frame_data` 时渲染 `<img src={\`data:image/jpeg;base64,${preview_frame_data}\`} style={{width:'100%',height:'100%',objectFit:'contain'}} />`；`preview_enabled && !preview_frame_data` 时显示"加载中…"占位
  - [x] SubTask 5.5: `RoomCard.tsx` 帧渲染使用 `useMemo` 或 key 优化，避免每帧触发整卡 re-render（img src 变化只更新 img 元素）

- [x] Task 6: 验证
  - [x] SubTask 6.1: `npx tsc --noEmit` 通过（lsc-electron 目录）
  - [x] SubTask 6.2: `npm run build` 通过
  - [x] SubTask 6.3: 后端 Python 语法检查通过（`python -c "import lsc.core.services.frame_capture"`）
  - [ ] SubTask 6.4: 用户视角验证：启用预览 → 3 秒内看到直播画面 → 禁用预览 → 画面消失

# Task Dependencies
- Task 2 依赖 Task 1（FrameCaptureWorker 需先实现）
- Task 3 可与 Task 1 并行（独立改动 server.py）
- Task 4 依赖 Task 2（manager 需先支持 mode 参数）
- Task 5 可与 Task 1-4 部分并行（前端类型/渲染逻辑独立，但 useWebSocket 依赖后端消息格式确定）
- Task 6 依赖 Task 1-5 全部完成
