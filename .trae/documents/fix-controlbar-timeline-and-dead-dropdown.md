# 修复控制栏时间线无效 + 删除导出下拉死代码

## 背景与现状

用户反馈两个问题：
1. **点击选择房间后，下方的总控制状态栏和时间线无效**（时间线播放头不动）
2. **房间内的导出下拉选择框有什么作用**（询问用途）

### 根因分析

**问题 1：时间线播放头不动**

[ControlBar.tsx](file:///d:/Project/直播切片多人/lsc-electron/src/pages/Workbench/components/ControlBar.tsx) 第 96-111 行的 `currentTime` 计算逻辑：

```tsx
const { duration, currentTime } = useMemo(() => {
  let dur = 3600, cur = 0
  if (room?.mark_out > 0) dur = Math.max(dur, room.mark_out)
  if (room?.is_recording && room?.record_started_at) {
    const elapsed = (Date.now() - new Date(room.record_started_at).getTime()) / 1000
    dur = Math.max(dur, elapsed); cur = elapsed
  }
  if (room?.mark_in > 0) cur = room.mark_in  // ★ 一旦设置入点，播放头被钉死在入点
  return { duration: dur, currentTime: cur }
}, [room?.mark_out, room?.is_recording, room?.record_started_at, room?.mark_in, tick])
```

- Electron/MSE 模式下，预览的实际播放位置由前端 `window.__msePlayers[roomId].player.videoElement.currentTime` 决定
- `RoomSession` 类型（[types/index.ts](file:///d:/Project/直播切片多人/lsc-electron/src/types/index.ts)）无 `current_pos` 字段，后端从未上报预览位置
- 当前 `currentTime` 计算：未设置入点时用录制时长（与实际播放位置不符），设置入点后被钉死在 `mark_in`（完全不反映实时播放）
- 结论：时间线播放头永远不跟随 MSE player 实际播放位置移动 → 视觉上"时间线无效"

**问题 2：导出下拉选择框是死代码**

[RoomCard.tsx](file:///d:/Project/直播切片多人/lsc-electron/src/pages/Workbench/components/RoomCard.tsx) 第 559-576 行的下拉框：

```tsx
{onToggleIncludeExport && (
  <span onClick={e => e.stopPropagation()}>
    <Select
      value={includeInExport ? 'true' : 'false'}
      onChange={() => onToggleIncludeExport(room.room_id)}
      options={[
        { value: 'false', label: '导出' },
        { value: 'true', label: '✓ 导出' },
      ]}
    />
  </span>
)}
```

- `includeInExportIds` 是 [Workbench/index.tsx](file:///d:/Project/直播切片多人/lsc-electron/src/pages/Workbench/index.tsx) 第 50 行的纯前端本地 state
- `handleToggleIncludeExport`（第 429-436 行）只切换本地 Set，**从未通过 WebSocket 上报后端**
- 后端的 `include_in_cut` 字段与前端 `includeInExportIds` 完全断裂
- 下拉框只有"导出 / ✓ 导出"两个选项，切换后除了改变本地 state 外**没有任何实际效果**（不影响导出、不影响录制、不影响切片）
- 结论：这是无作用的死代码，应该删除

## 已完成的修改（前一会话）

- ✅ 修改 1：`handleControlMarkIn`/`handleControlMarkOut` 传 `time` 参数（[index.tsx#L450-L462](file:///d:/Project/直播切片多人/lsc-electron/src/pages/Workbench/index.tsx#L450-L462)）
- ✅ 修改 2：`handleControlAddClip` 支持多选（[index.tsx#L464-L467](file:///d:/Project/直播切片多人/lsc-electron/src/pages/Workbench/index.tsx#L464-L467)）

## 待实施的修改

### 修改 3：新增 previewPositions 轮询（Workbench/index.tsx）

**文件**：[d:\Project\直播切片多人\lsc-electron\src\pages\Workbench\index.tsx](file:///d:/Project/直播切片多人/lsc-electron/src/pages/Workbench/index.tsx)

**位置**：在第 50 行 `includeInExportIds` state 附近（待删除）添加，或直接在 `fullscreenRoomId` state（第 53 行）之后添加。

**新增代码**：
```tsx
// 预览播放位置（从 MSE player 定期读取，驱动时间线播放头）
const [previewPositions, setPreviewPositions] = useState<Record<string, number>>({})
useEffect(() => {
  const id = setInterval(() => {
    const registry = (window as any).__msePlayers
    if (!registry) return
    const next: Record<string, number> = {}
    let changed = false
    for (const rid of Object.keys(registry)) {
      const entry = registry[rid]
      const t = entry?.player?.videoElement?.currentTime
      if (typeof t === 'number' && t >= 0) {
        next[rid] = t
        changed = true
      }
    }
    if (changed) setPreviewPositions(next)
  }, 500)
  return () => clearInterval(id)
}, [])
```

**为什么**：500ms 轮询 `window.__msePlayers` 注册表（[VideoPreview.tsx#L160](file:///d:/Project/直播切片多人/lsc-electron/src/components/VideoPreview.tsx#L160) 确认 registry 结构为 `{ [roomId]: { feedInit, feedMedia, player } }`，player 含 `videoElement`），读取实际播放位置驱动时间线播放头。500ms 间隔平衡流畅度与性能。

### 修改 4：ControlBar 接收 previewPos 并用于 currentTime（ControlBar.tsx）

**文件**：[d:\Project\直播切片多人\lsc-electron\src\pages\Workbench\components\ControlBar.tsx](file:///d:/Project/直播切片多人/lsc-electron/src/pages/Workbench/components/ControlBar.tsx)

**4.1 Props 接口新增 previewPos**（第 17-31 行接口内）：
```tsx
interface ControlBarProps {
  room: RoomSession | undefined
  multiSelectCount?: number
  loopPreview?: boolean
  clips?: ClipSegment[]
  previewPos?: number  // ★ 新增：MSE player 实际播放位置
  onSeek: (time: number) => void
  // ... 其余不变
}
```

**4.2 比较器新增 previewPos 检查**（第 37-63 行 `areControlBarPropsEqual` 内）：
在 `if (prev.onToggleLoop !== next.onToggleLoop) return false` 之后添加：
```tsx
if (prev.previewPos !== next.previewPos) return false
```

**4.3 函数参数解构新增 previewPos**（第 65-79 行）：
```tsx
export const ControlBar = memo(function ControlBar({
  room,
  multiSelectCount = 0,
  loopPreview = false,
  clips = [],
  previewPos = 0,  // ★ 新增
  // ...
}: ControlBarProps) {
```

**4.4 currentTime 计算优先使用 previewPos**（第 96-111 行）：
```tsx
const { duration, currentTime } = useMemo(() => {
  let dur = 3600
  let cur = 0
  if (room?.mark_out !== null && room?.mark_out !== undefined && room.mark_out > 0) {
    dur = Math.max(dur, room.mark_out)
  }
  if (room?.is_recording && room?.record_started_at) {
    const elapsed = (Date.now() - new Date(room.record_started_at).getTime()) / 1000
    dur = Math.max(dur, elapsed)
  }
  // ★ 优先使用 MSE player 实际播放位置，回退到入点
  if (previewPos > 0) {
    cur = previewPos
  } else if (room?.mark_in !== null && room?.mark_in !== undefined && room.mark_in > 0) {
    cur = room.mark_in
  }
  return { duration: dur, currentTime: cur }
}, [room?.mark_out, room?.is_recording, room?.record_started_at, room?.mark_in, previewPos, tick])
```

**4.5 Workbench 调用处传入 previewPos**（[index.tsx#L889-L903](file:///d:/Project/直播切片多人/lsc-electron/src/pages/Workbench/index.tsx#L889-L903)）：
```tsx
<ControlBar
  room={selectedRoom}
  multiSelectCount={selectedRoomIds.size > 1 ? selectedRoomIds.size : 0}
  loopPreview={loopPreview}
  clips={clips}
  previewPos={previewPositions[selectedRoom?.room_id ?? ''] ?? 0}  // ★ 新增
  onSeek={handleTimelineSeek}
  // ... 其余不变
/>
```

**为什么**：让时间线播放头跟随 MSE player 实际播放位置移动，解决"时间线无效"的视觉问题。回退到 mark_in 保证未播放时仍有合理位置。

### 修改 5：删除 RoomCard 导出下拉选择框（RoomCard.tsx）

**文件**：[d:\Project\直播切片多人\lsc-electron\src\pages\Workbench\components\RoomCard.tsx](file:///d:/Project/直播切片多人/lsc-electron/src/pages/Workbench/components/RoomCard.tsx)

**删除第 559-576 行**整个 `{onToggleIncludeExport && (...)}` 块：
```tsx
// 删除这一整块
<Space size={2}>
  {/* 导出下拉选项 — 与删除按钮同行 */}
  {onToggleIncludeExport && (
    <span onClick={e => e.stopPropagation()}>
      <Select ... />
    </span>
  )}
  <Tooltip title="删除房间">...</Tooltip>
</Space>
```

**保留**：删除房间按钮（Tooltip + Button）需要保留，只是把外层 `<Space>` 内的下拉框删掉。具体操作：删除第 561-576 行的 `{onToggleIncludeExport && (...)}` 块，保留第 559 行 `<Space size={2}>` 和第 577 行开始的删除房间 Tooltip。

**为什么**：下拉框是死代码，切换不影响任何后端逻辑，删除避免用户困惑。

### 修改 6：清理 Workbench includeInExportIds 死代码（Workbench/index.tsx）

**文件**：[d:\Project\直播切片多人\lsc-electron\src\pages\Workbench\index.tsx](file:///d:/Project/直播切片多人/lsc-electron/src/pages/Workbench/index.tsx)

**6.1 删除第 50 行 state**：
```tsx
const [includeInExportIds, setIncludeInExportIds] = useState<Set<string>>(new Set())  // 删除
```

**6.2 删除第 429-436 行 handler**：
```tsx
const handleToggleIncludeExport = useCallback((roomId: string) => {
  setIncludeInExportIds(prev => { ... })
}, [])  // 删除
```

**6.3 删除第 878-879 行 RoomCard 调用处的 props**：
```tsx
includeInExport={includeInExportIds.has(room.room_id)}      // 删除
onToggleIncludeExport={handleToggleIncludeExport}           // 删除
```

**为什么**：清理与后端断裂的纯前端死代码，避免维护负担和用户困惑。

### 修改 7：清理 RoomCardProps includeInExport 字段（RoomCard.tsx）

**文件**：[d:\Project\直播切片多人\lsc-electron\src\pages\Workbench\components\RoomCard.tsx](file:///d:/Project/直播切片多人/lsc-electron/src/pages/Workbench/components/RoomCard.tsx)

**7.1 删除接口字段**（第 19-37 行 `RoomCardProps`）：
- 删除第 23 行 `includeInExport?: boolean`
- 删除第 34 行 `onToggleIncludeExport?: (roomId: string) => void`

**7.2 删除比较器检查**（第 62-102 行 `areRoomPropsEqual`）：
- 删除第 65 行 `if (prev.includeInExport !== next.includeInExport) return false`
- 删除第 76 行 `if (prev.onToggleIncludeExport !== next.onToggleIncludeExport) return false`

**7.3 删除函数参数解构**（第 104-121 行）：
- 删除第 108 行 `includeInExport = false,`
- 删除第 119 行 `onToggleIncludeExport,`

**7.4 清理 import**（第 2 行）：
- 检查 `Select` 是否还在其他地方使用，若不再使用则从 antd import 中删除

**为什么**：完成死代码清理，保持类型定义与实际使用一致。

## 验证步骤

1. **TypeScript 编译验证**：
   ```bash
   cd d:\Project\直播切片多人\lsc-electron && npx tsc --noEmit
   ```
   预期无错误（特别是 RoomCard 不再引用已删除的 props）。

2. **重启程序验证**：
   - 重启 Electron 前端（Vite 热重载通常足够，但保险起见重启）
   - 无需重启 Python 后端（本次修改仅涉及前端）

3. **功能验证（用户操作）**：
   - **时间线播放头**：添加房间 → 连接 → 启用预览 → 点击选中该房间 → 观察底部时间线播放头是否随预览画面实时移动（每 500ms 更新）
   - **入点/出点按钮**：点击控制栏"入点"/"出点"按钮 → 时间线上出现标记 → 验证标记位置与预览当前位置对应
   - **导出下拉框**：确认房间卡片右下角不再有"导出/✓ 导出"下拉框，只剩删除按钮
   - **多选场景**：Ctrl+点击多选 2+ 房间 → 控制栏显示"X 个房间同步"横幅 → 时间线播放头显示主选房间的位置

## 假设与决策

1. **轮询间隔 500ms**：平衡流畅度（用户视觉上能看到播放头移动）与性能（避免高频 setState 触发重渲染）。ControlBar 用 `memo` + 字段级比较器，仅 `previewPos` 变化时才重渲染。
2. **不修改后端**：本次问题纯属前端展示问题，后端 `set_mark_in`/`set_mark_out` 已支持接收 `time` 参数（前一会话修复），无需后端改动。
3. **不修复 toggle_play_pause/seek_relative 在 Electron 模式下的无效**：后端 handler 依赖 `widget.seek`（PySide6 控件），Electron/MSE 模式下预览由前端 MSE player 控制，后端无法直接影响。这属于已知架构限制，不在本次修复范围。用户主要痛点是时间线播放头不动，本次通过前端轮询解决。
4. **保留 ControlBar 的 tick 机制**：录制中每秒刷新时间显示的逻辑保留，与 previewPos 轮询互不冲突（tick 影响 duration 计算，previewPos 影响 currentTime 计算）。
