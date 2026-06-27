# Checklist

## 后端 FrameCaptureWorker
- [x] `lsc/core/services/frame_capture.py` 存在且实现 `FrameCaptureWorker` 类
- [x] FFmpeg 命令使用 `fps=3,scale=854:480` + mjpeg 编码 + `-q:v 7`
- [x] JPEG 帧解析正确（SOI `0xFF 0xD8` / EOI `0xFF 0xD9` 分割）
- [x] `get_latest_frame()` 返回最新 JPEG bytes
- [x] `stop()` 终止 FFmpeg 进程（terminate → 2秒超时 → kill）
- [x] HTTP headers（Referer/User-Agent）透传给 FFmpeg
- [x] 异常退出自动重试（最多 3 次，间隔 5 秒）

## manager.py Electron 预览模式
- [x] `RoomSession` 有 `frame_capture` 字段
- [x] `start_preview(mode='electron')` 创建 FrameCaptureWorker 而非 MpvWidget
- [x] `stop_preview` 在 electron 模式下停止 FrameCaptureWorker
- [x] `_frame_pusher` QTimer 每 333ms 触发，推送 `preview_frame` 消息
- [x] `remove_room` 清理 frame_capture
- [x] manager 初始化时启动 frame_pusher

## WebSocket 通道
- [x] `server.py` 的 `websockets.serve` max_size 调大到 16MB
- [x] `preview_frame` 消息通过 `queue_broadcast` 投递

## enable_preview 模式区分
- [x] `handle_enable_preview` 读取 `mode` 字段传给 manager
- [x] 前端 `handleTogglePreview` 发送 `mode: 'electron'`

## 前端预览帧渲染
- [x] `RoomSession` 类型有 `preview_frame_data?: string` 字段
- [x] `useWebSocket.ts` 处理 `preview_frame` 消息，更新 store
- [x] `RoomCard.tsx` 在 `preview_enabled && preview_frame_data` 时渲染 `<img>`
- [x] `preview_enabled && !preview_frame_data` 时显示"加载中…"
- [x] 帧渲染有性能优化（避免整卡 re-render）

## 验证
- [x] `npx tsc --noEmit` 通过
- [x] `npm run build` 通过
- [x] 后端 `python -c "import lsc.core.services.frame_capture"` 通过
- [x] 后端测试套件 236 个全部通过（`python -m pytest tests/ -x -q`）
- [ ] 用户视角：启用预览 → 3 秒内看到直播画面 → 禁用预览 → 画面消失
