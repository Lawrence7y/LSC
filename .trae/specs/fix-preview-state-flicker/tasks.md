# Tasks

- [x] Task 1: VideoPreview 组件职责收窄与依赖稳定化
  - [x] SubTask 1.1: 移除 `VideoPreview.tsx` 的 `initPlayer` 中的 `send('enable_preview', { enabled: true })` 调用（第 62-66 行）
  - [x] SubTask 1.2: 将 `stopPlayer` 拆分为两个函数：`cleanupPlayer`（只清理本地 MsePlayer，不 send）和 `stopAndNotify`（清理 + send disable）。useEffect cleanup 调用 `cleanupPlayer`，"重试"按钮调用 `stopAndNotify`
  - [x] SubTask 1.3: `useEffect` 依赖改为 `[active, roomId]`，将 `initPlayer`/`cleanupPlayer` 逻辑内联到 effect 内部，避免依赖不稳定函数引用
  - [x] SubTask 1.4: `onReady`/`onError` 通过 `useRef` 包裹，避免因引用变化触发重新渲染。在 initPlayer 内部通过 `ref.current` 读取

- [x] Task 2: 后端 MSE 失败自动清理状态
  - [x] SubTask 2.1: `python-backend/handlers/room_handler.py` 的 `_handle_mse_preview` enable 分支中，MseStreamer 的 `on_error` 回调增加状态清理逻辑：通过 `bridge.queue_broadcast` 投递 `rooms_updated` 广播，并通过 `bridge.call` 设置 `room.preview_enabled = False` 和从 `_mse_streamers` 移除
  - [x] SubTask 2.2: `on_error` 回调中使用 `asyncio.run_coroutine_threadsafe` 调度一个协程，该协程通过 `bridge.call` 在 Qt 主线程清理状态并广播 `rooms_updated`
  - [x] SubTask 2.3: 注意线程安全：`_mse_streamers` 的修改需在 `_mse_streamers_lock` 保护下进行

- [x] Task 3: 后端 enable_preview 防重入
  - [x] SubTask 3.1: `_handle_mse_preview` enable 分支增加"正在启动"标志检查，防止 MSE 启动过程中重复请求。可用 `_mse_streamers_lock` 保护一个 `_mse_starting: set[str]` 集合
  - [x] SubTask 3.2: MSE 启动完成后（成功或失败）从 `_mse_starting` 移除 room_id

- [x] Task 4: 验证
  - [x] SubTask 4.1: `npx tsc --noEmit` 通过
  - [x] SubTask 4.2: `python -m pytest -q` 通过（10 个失败均为预存问题：`RoomSession.analysis_highlights`/`DashboardPage.multi_room_requested`/详情面板标签，与本 spec 修改的 MSE/VideoPreview 无关，未新增失败）
  - [ ] SubTask 4.3: 用户视角：点击"启用预览"后状态稳定，不闪烁；FFmpeg 失败后自动回到"启用预览"按钮状态（需用户重启程序验证）

# Task Dependencies
- Task 2 和 Task 3 可并行
- Task 1 独立
- Task 4 依赖 Task 1-3 全部完成
