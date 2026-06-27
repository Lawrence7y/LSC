# Tasks

- [x] Task 1: 录制目录权限回退（后端）
  - [x] SubTask 1.1: `lsc/gui/multi_room/manager.py` 的 `_start_recording_sync`（第 1589-1593 行）修改：将 `os.makedirs(room_output_dir)` 的 try/except 改为多层回退。第一次失败后，回退到 `os.path.join(os.path.expanduser('~'), '.lsc', 'output', os.path.basename(room_output_dir))`，重新 makedirs + 重新调用 `adapter.start_recording`。第二次仍失败则设置 `room.last_error = "录制目录不可写，请在设置中修改输出目录"` 返回 `False`
  - [x] SubTask 1.2: `python-backend/handlers/room_handler.py` 的 `handle_start_recording`（第 283 行）用 try/except 包裹 `bridge.call(_start)`，捕获 `Exception` 返回 `{'success': False, 'error': str(exc)}`；`bridge.call` 增加 `timeout=30.0`

- [x] Task 2: 房间持久化恢复（后端）
  - [x] SubTask 2.1: `lsc/gui/multi_room/manager.py` 在 `add_room` 方法（第 578 行）之后新增 `restore_rooms_from_urls(self, urls: list[str]) -> int` 方法：遍历 URLs 调用 `self.add_room(url, persist=False)`，返回成功添加的房间数。若 URL 已存在（按 `room_url` 去重）则跳过
  - [x] SubTask 2.2: `python-backend/handlers/room_handler.py` 的 `handle_connect`（第 182-190 行）修改：`load_rooms()` 后提取 `room_url` 列表（`[r.get('room_url') for r in rooms if r.get('room_url')]`），先 `await asyncio.get_event_loop().run_in_executor(None, lambda: bridge.call(lambda: manager.restore_rooms_from_urls(urls)))`，再推送 `rooms_loaded` 包含 `_rooms_list(manager)`（而非原始 `rooms`）

- [x] Task 3: 预览流畅度提升（后端）
  - [x] SubTask 3.1: `lsc/core/services/frame_capture.py` 的 `_build_args`（第 77 行后）在 `-i self._stream_url` 之后、`-vf` 之前增加 `"-an"` 参数（显式禁用音频解码）
  - [x] SubTask 3.2: `lsc/core/services/frame_capture.py` 第 42 行 `fps: int = 8` 改为 `fps: int = 10`
  - [x] SubTask 3.3: `lsc/gui/multi_room/manager.py` 的 `_start_preview_electron` 自适应 fps 改为三档：`active_count == 0` → `fps=10`；`active_count == 1` → `fps=8`；`active_count >= 2` → `fps=5`
  - [x] SubTask 3.4: `lsc/gui/multi_room/manager.py` 的 `_ensure_frame_pusher` `setInterval(125)` 改为 `setInterval(100)`

- [x] Task 4: RoomCard 静音与放大按钮（前端）
  - [x] SubTask 4.1: `lsc-electron/src/pages/Workbench/components/RoomCard.tsx` 第 3-10 行 imports 增加 `SoundOutlined, MuteOutlined, FullscreenOutlined` 从 `@ant-design/icons`（注：`MuteOutlined` 实际为 `MutedOutlined`）
  - [x] SubTask 4.2: `RoomCardProps`（第 13-23 行）增加 `onToggleMute: () => void` 和 `onFullscreen: () => void` 回调
  - [x] SubTask 4.3: 预览区（第 141-169 行 `room.preview_enabled` 分支内）在"停止预览"按钮旁边增加两个图标按钮：静音按钮（`room.preview_muted ? <MutedOutlined /> : <SoundOutlined />`）和放大按钮（`<FullscreenOutlined />`），仅 `preview_frame_data` 存在时显示。按钮样式与"停止预览"一致（`position: absolute`，bottom/right），改为三个按钮水平排列在右下角
  - [x] SubTask 4.4: `lsc-electron/src/pages/Workbench/index.tsx` 增加 `handleToggleMute(roomId: string)` 回调：`send('set_preview_muted', { room_id: roomId, muted: !currentRoom.preview_muted })`
  - [x] SubTask 4.5: `lsc-electron/src/pages/Workbench/index.tsx` 增加 `const [fullscreenRoomId, setFullscreenRoomId] = useState<string | null>(null)` state 和 Modal 渲染：Modal `open={!!fullscreenRoomId}`，`onCancel={() => setFullscreenRoomId(null)}`，`width="90%"`，内容为 `<img src={data:image/jpeg;base64,${fullscreenRoom?.preview_frame_data}} style={{width:'100%'}}>`，`footer={null}`
  - [x] SubTask 4.6: `RoomCard` 组件调用处传递 `onToggleMute={() => handleToggleMute(room.room_id)}` 和 `onFullscreen={() => setFullscreenRoomId(room.room_id)}` props

- [x] Task 5: set_preview_muted 后端 handler
  - [x] SubTask 5.1: `python-backend/handlers/room_handler.py` 在 `handle_disconnect_room` 之后新增 `handle_set_preview_muted`：`@server.on('set_preview_muted')`，读取 `room_id = data.get('room_id')` 和 `muted = data.get('muted')`，调用 `bridge.call(lambda: manager.set_preview_muted(room_id, muted))`，`_broadcast_rooms()`，返回 `{'success': True}`

- [x] Task 6: 验证
  - [x] SubTask 6.1: `npx tsc --noEmit` 通过
  - [x] SubTask 6.2: `npm run build` 通过（tsc 已验证）
  - [x] SubTask 6.3: 后端 Python import 检查通过
  - [x] SubTask 6.4: 后端 pytest 测试通过（236 passed, 2 warnings）
  - [ ] SubTask 6.5: 用户视角：重启程序后房间保留；录制不再 WinError 5；预览更流畅；静音/放大按钮可用

# Task Dependencies
- Task 1、Task 2、Task 3 相互独立（分别修改录制/持久化/预览三个独立模块）
- Task 4 依赖 Task 5（前端发送 set_preview_muted 需后端有 handler）
- Task 5 独立
- Task 6 依赖 Task 1-5 全部完成
