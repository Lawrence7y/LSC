# Checklist

## 后端 handle_add_room 异常处理
- [x] `handle_add_room` 用 try/except 包裹 `bridge.call`，捕获 `TimeoutError` 返回超时错误
- [x] `bridge.call` 调用使用 `timeout=30.0`
- [x] `add_room` 返回 None 时返回 `{'success': False, 'error': '房间数已达上限'}`
- [x] 其他异常返回 `{'success': False, 'error': str(exc)}`

## 前端 handleAddRoom 修复
- [x] `setLoading(true)` 后不在同一函数内 `setLoading(false)`，改为在 `add_room_response` 回调中恢复
- [x] 成功时清空输入框 `setUrl('')`
- [x] 失败时 `message.error(data.error)` 并保留输入框内容
- [x] 用 `useRef` 保存待添加的 URL，供 `add_room_response` 回调判断是否清空

## 预览帧率提升
- [x] `FrameCaptureWorker.__init__` 默认 `fps=8`
- [x] `_ensure_frame_pusher` 间隔 `125ms`
- [x] `_start_preview_electron` 自适应 fps（并发 ≥3 时降为 5）

## 验证
- [x] `npx tsc --noEmit` 通过
- [x] `npm run build` 通过
- [x] 后端 Python import 检查通过
- [x] 后端 pytest 测试通过（236 passed）
- [ ] 用户视角：连续添加 2-3 个房间都能成功显示
- [ ] 用户视角：启用预览后画面流畅（~8fps）
