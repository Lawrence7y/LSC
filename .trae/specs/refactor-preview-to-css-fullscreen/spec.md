# CSS 全屏单播放器架构重构 Spec

## Why

全屏预览切换时创建全新的 VideoPreview + MsePlayer 实例，导致 1-2 秒初始化延迟；反复切换时 registry 竞态使小预览 player 饿死、状态被破坏，累积开销导致卡顿。应去除 Modal 全屏，改为对 RoomCard 中已有 VideoPreview 容器应用 CSS `position: fixed`，使同一个 `<video>` 与 MsePlayer 实例始终存活，从根上消除重建与竞态。

## What Changes

- 用 CSS `position: fixed` 全屏替换 Ant Design Modal 全屏，VideoPreview 容器在小预览与全屏之间瞬时切换
- 删除 Workbench 中的全屏 Modal 及其重复 VideoPreview 实例
- 删除 VideoPreview 的 `isFullscreenActive` prop 及其触发的 player 重建/恢复 useEffect（约 120 行）
- 修复 SourceBuffer trim 链式 updateend 递归问题，引入 `_isTrimming` 标志
- 优化 500ms 轮询：仅在 currentTime 真正变化时 setState，消除无差别重渲染
- 为 `<video>` 与容器添加 GPU 加速 CSS（`will-change`、`translateZ(0)`、`backfaceVisibility`）
- 全屏时显示关闭按钮浮层与原生视频控件，非全屏时隐藏
- 支持 Escape 键退出全屏

## Impact

- Affected specs: 预览播放、全屏交互、多房间同步
- Affected code:
  - `lsc-electron/src/pages/Workbench/index.tsx`：删除全屏 Modal（行 1122-1141），添加 Escape 键监听，优化轮询
  - `lsc-electron/src/pages/Workbench/components/RoomCard.tsx`：VideoPreview 容器应用条件 CSS，添加全屏浮层按钮，移除 `isFullscreenActive` prop 传递
  - `lsc-electron/src/components/VideoPreview.tsx`：删除 `isFullscreenActive` prop 与恢复 useEffect，添加 GPU 加速 CSS
  - `lsc-electron/src/services/mediaSourcePlayer.ts`：引入 `_isTrimming` 标志防止 trim 递归

## ADDED Requirements

### Requirement: CSS 全屏切换

系统 SHALL 通过 CSS `position: fixed; inset: 0; z-index: 9999` 将 RoomCard 内的 VideoPreview 容器从 120px 小预览区瞬时浮起为全屏，不创建新的 MsePlayer 或 MediaSource 实例。

#### Scenario: 打开全屏
- **WHEN** 用户点击房间卡片的"全屏"按钮
- **THEN** 该房间的 VideoPreview 容器应用 `position: fixed; inset: 0; z-index: 9999`，瞬时覆盖整个视口
- **AND** `<video>` 元素显示原生控件（`controls={true}`）
- **AND** 右上角显示"退出全屏"按钮浮层
- **AND** 原 120px 预览区显示占位文字"预览已全屏"
- **AND** MsePlayer 实例、MediaSource、SourceBuffer 不被销毁或重建

#### Scenario: 关闭全屏
- **WHEN** 用户点击"退出全屏"按钮或按 Escape 键
- **THEN** VideoPreview 容器恢复 `position: absolute`（相对于预览区）
- **THEN** `<video>` 元素隐藏原生控件（`controls={false}`）
- **AND** 预览立即继续播放，无黑屏、无重建延迟
- **AND** 原 120px 预览区恢复显示视频

#### Scenario: 反复切换无累积开销
- **WHEN** 用户连续打开/关闭全屏 10 次
- **THEN** 每次切换延迟均 < 50ms
- **AND** 不产生 ffmpeg 进程泄漏
- **AND** 不出现小预览卡死

### Requirement: Escape 键退出全屏

系统 SHALL 在全屏状态下监听 Escape 键，按下时退出全屏。

#### Scenario: Escape 退出
- **WHEN** 全屏打开且用户按下 Escape 键
- **THEN** 全屏关闭，恢复小预览
- **AND** 不触发其他快捷键（如 mark_in）

## MODIFIED Requirements

### Requirement: VideoPreview 组件接口

VideoPreview SHALL 移除 `isFullscreenActive` prop，因为不再需要全屏恢复逻辑。组件应通过外部 CSS 控制尺寸，自身不感知全屏状态。

### Requirement: SourceBuffer 缓冲区 trim

MsePlayer 的 `_flushPending` SHALL 在 trim 操作期间设置 `_isTrimming` 标志，trim 触发的 `updateend` 事件不再递归进入 trim 分支，仅处理待追加段，避免链式 updateend 导致 `updating=true` 卡死。

#### Scenario: trim 不递归
- **WHEN** `updateend` 触发且 buffered 超过 60 秒
- **THEN** 设置 `_isTrimming=true`，调用 `SourceBuffer.remove()`
- **WHEN** remove 完成触发新的 `updateend`
- **THEN** 检测到 `_isTrimming=true`，跳过 trim 分支，仅处理 `_pendingBuffers`
- **AND** 重置 `_isTrimming=false`

### Requirement: 预览位置轮询

Workbench 的 500ms 轮询 SHALL 用 `useRef` 缓存上次的 `previewPositions` 快照，逐个比较 currentTime，仅在至少一个房间位置真正变化时调用 `setPreviewPositions`，避免无差别重渲染。

## REMOVED Requirements

### Requirement: 全屏 Modal
**Reason**: Modal 创建重复 VideoPreview + MsePlayer 实例，导致 1-2 秒初始化延迟、registry 竞态、累积卡顿
**Migration**: 改用 CSS `position: fixed` 全屏，删除 Modal 及其内部 VideoPreview，删除 `isFullscreenActive` prop 与恢复 useEffect
