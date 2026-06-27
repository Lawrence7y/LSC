# Tasks

- [x] Task 1: 删除 Workbench 全屏 Modal，改用 CSS 全屏
  - [x] SubTask 1.1: 在 `lsc-electron/src/pages/Workbench/index.tsx` 删除全屏 Modal JSX（约行 1122-1141）
  - [x] SubTask 1.2: 保留 `fullscreenRoomId` 状态与 `handleFullscreen` 回调
  - [x] SubTask 1.3: 添加 Escape 键监听 useEffect，全屏打开时按 Escape 退出（且不触发其他快捷键）
  - [x] SubTask 1.4: 确认 `handleFullscreen` 切换逻辑仍正确（点击同一房间切换全屏）

- [x] Task 2: RoomCard 实现 CSS 全屏容器切换
  - [x] SubTask 2.1: 在 `lsc-electron/src/pages/Workbench/components/RoomCard.tsx` 的预览区包裹 div 上，根据 `fullscreenRoomId === room.room_id` 条件应用 `position: fixed; inset: 0; z-index: 9999; background: #000` 或默认 `position: relative`
  - [x] SubTask 2.2: 全屏时在容器右上角显示"退出全屏"按钮浮层（Button + stopPropagation）
  - [x] SubTask 2.3: 全屏时给 VideoPreview 传 `controls={true}`，非全屏传 `controls={false}`
  - [x] SubTask 2.4: 全屏时原 120px 预览区显示占位文字"预览已全屏"（通过条件渲染 VideoPreview 或占位）
  - [x] SubTask 2.5: 删除传给 VideoPreview 的 `isFullscreenActive` prop（约行 237）

- [x] Task 3: 删除 VideoPreview 的全屏恢复逻辑
  - [x] SubTask 3.1: 在 `lsc-electron/src/components/VideoPreview.tsx` 删除 `isFullscreenActive` prop 定义
  - [x] SubTask 3.2: 删除 `lastFullscreenRef` 与全屏恢复 useEffect（约行 202-321）
  - [x] SubTask 3.3: 确认初始化 useEffect 中的缓存 init 段逻辑（`getMseInitCache`）保留，用于首次挂载加速
  - [x] SubTask 3.4: 确认 registry cleanup 的 player 身份检查逻辑保留

- [x] Task 4: 修复 SourceBuffer trim 链式 updateend 递归
  - [x] SubTask 4.1: 在 `lsc-electron/src/services/mediaSourcePlayer.ts` 的 MsePlayer 类添加 `_isTrimming = false` 私有属性
  - [x] SubTask 4.2: 在 `updateend` 事件回调中，trim 前检查 `!_isTrimming`，trim 时设置 `_isTrimming=true`
  - [x] SubTask 4.3: trim 的 `remove()` 完成后触发的 `updateend` 中，检测 `_isTrimming=true` 则跳过 trim 分支仅处理 `_pendingBuffers`，并重置 `_isTrimming=false`

- [x] Task 5: 优化 500ms 轮询，消除无差别重渲染
  - [x] SubTask 5.1: 在 `lsc-electron/src/pages/Workbench/index.tsx` 的 previewPositions 轮询 useEffect 中，用 `useRef` 存储上次快照
  - [x] SubTask 5.2: 逐个比较每个房间的 currentTime，仅在至少一个房间位置真正变化（差值 > 0.01）时 `setPreviewPositions`

- [x] Task 6: 添加 GPU 加速 CSS
  - [x] SubTask 6.1: 在 `lsc-electron/src/components/VideoPreview.tsx` 的 `<video>` 元素 style 添加 `willChange: 'transform'`、`transform: 'translateZ(0)'`、`backfaceVisibility: 'hidden'`
  - [x] SubTask 6.2: 在容器 div style 添加相同 GPU 加速属性

- [x] Task 7: TypeScript 编译验证
  - [x] SubTask 7.1: 运行 `npx tsc --noEmit` 确认无类型错误

- [x] Task 8: 重启程序并功能验证
  - [x] SubTask 8.1: 终止现有 Electron/Python 进程
  - [x] SubTask 8.2: 在 IDE 外启动 `npm run dev`
  - [x] SubTask 8.3: 验证全屏打开/关闭/Escape/反复切换 10 次均无卡顿

# Task Dependencies

- Task 2 depends on Task 1（删除 Modal 后 RoomCard 承担全屏）
- Task 3 depends on Task 2（RoomCard 不再传 isFullscreenActive 后 VideoPreview 才能删除该 prop）
- Task 4、Task 5、Task 6 互相独立，可并行
- Task 7 depends on Task 1-6 全部完成
- Task 8 depends on Task 7
