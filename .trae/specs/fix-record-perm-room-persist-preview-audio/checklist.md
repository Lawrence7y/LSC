# Checklist

## 录制目录权限回退
- [x] `_start_recording_sync` makedirs 失败时回退到 `~/.lsc/output/{basename}`
- [x] 回退成功继续录制，回退失败返回错误"录制目录不可写，请在设置中修改输出目录"
- [x] `handle_start_recording` 用 try/except 包裹 `bridge.call`，`timeout=30.0`
- [x] `handle_start_recording` 失败返回 `{'success': False, 'error': str(exc)}`

## 房间持久化恢复
- [x] `manager.restore_rooms_from_urls(urls)` 方法存在，按 `room_url` 去重
- [x] `handle_connect` 推送 rooms_loaded 前先调用 `restore_rooms_from_urls`
- [x] 推送的 rooms_loaded 包含 `_rooms_list(manager)`（room_id 一致）
- [x] 前端 `handleRooms` 直接 `setRooms` 覆盖（无需改动）

## 预览流畅度提升
- [x] `FrameCaptureWorker._build_args` 在 `-i` 后、`-vf` 前包含 `-an`
- [x] `FrameCaptureWorker.__init__` 默认 `fps=10`
- [x] `_start_preview_electron` 自适应 fps（0→10, 1→8, 2+→5）
- [x] `_ensure_frame_pusher` 间隔 `100ms`

## RoomCard 静音与放大按钮
- [x] imports 包含 `SoundOutlined, MutedOutlined, FullscreenOutlined`
- [x] `RoomCardProps` 有 `onToggleMute` 和 `onFullscreen` 回调
- [x] 预览区右下角有静音、放大、停止预览三个按钮水平排列（仅 preview_frame_data 存在时显示）
- [x] 静音按钮图标随 `preview_muted` 切换
- [x] `Workbench/index.tsx` 有 `handleToggleMute` 发送 `set_preview_muted`
- [x] `fullscreenRoomId` state 和 Modal 渲染存在
- [x] Modal 显示当前预览帧的 `<img>`，`width="90%"`，`footer={null}`
- [x] `RoomCard` 调用处传递 `onToggleMute` 和 `onFullscreen` props

## set_preview_muted handler
- [x] `handle_set_preview_muted` handler 存在
- [x] 调用 `manager.set_preview_muted(room_id, muted)`
- [x] 调用 `_broadcast_rooms()`
- [x] 返回 `{'success': True}`

## 验证
- [x] `npx tsc --noEmit` 通过
- [x] `npm run build` 通过
- [x] 后端 Python import 检查通过
- [x] 后端 pytest 测试通过（236 passed, 2 warnings）
- [ ] 用户视角：重启程序后房间保留
- [ ] 用户视角：录制不再 WinError 5
- [ ] 用户视角：预览更流畅（~10fps）
- [ ] 用户视角：静音/放大按钮可用
