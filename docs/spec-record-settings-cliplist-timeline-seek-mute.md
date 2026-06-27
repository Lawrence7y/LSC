# Spec: RecordSettings 布局 + ClipList 改造 + Timeline seek 优化 + 标记删除 + 静音切换

## 概述

6 项改动，覆盖录制设置布局、切片列表改造、时间线 seek 卡死修复、入出点右键删除、静音切换按钮。

---

## Step 1: RecordSettings 画质/编码器标签和下拉框同一行

**文件**: `RecordSettings.tsx`

**问题**: 画质/编码器标签在上方，下拉框在下方，占两行。用户要求标签和下拉框在同一行。

**方案**: 改为 flex 水平布局，标签 `flexShrink: 0, width: 50`，下拉框 `flex: 1`。

```tsx
<div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
  <span style={{ fontSize: 12, color: 'var(--text-tertiary)', flexShrink: 0, width: 36 }}>画质</span>
  <Select ... style={{ flex: 1 }} size="small" />
</div>
```

同理编码器。

---

## Step 2: CRF Slider 优化

**文件**: `RecordSettings.tsx`

**问题**: min=0 max=51 范围太大，跟随 tooltip 占空间。

**方案**:
- `min={18}` `max={28}`
- `tooltip={{ open: false }}`
- marks 只保留 `{ 18: '18', 23: '23', 28: '28' }`
- 左右标签改为 `18（小体积）` / `28（高质量）`

---

## Step 3: 删除 ExportQueue + 最近切片栏，ClipList 改造

### 3.1 删除 ExportQueue 和最近切片栏

**文件**: `Workbench/index.tsx`

- 删除 `<ExportQueue>` 渲染（行1067-1074）
- 删除最近切片栏渲染（行1085-1140）
- 删除 `recentClips` 相关变量和 `addRecentClip` 导入（保留 store 中的定义以防其他地方引用）

### 3.2 ClipSegment 增加导出状态字段

**文件**: `types/index.ts`

```typescript
export interface ClipSegment {
  start: number
  end: number
  label: string
  thumbnail_path?: string
  room_id?: string
  room_name?: string
  exported?: boolean         // 新增：是否已导出
  outputPath?: string        // 新增：导出文件路径
}
```

### 3.3 ClipList 改造

**文件**: `ClipList.tsx`

新增 props:
```typescript
interface ClipListProps {
  clips: ClipSegment[]
  onDelete: (index: number) => void
  onExport: (clip: ClipSegment) => void
  onOpenFile?: (path: string) => void       // 新增
  onOpenFolder?: (path: string) => void      // 新增
}
```

每个切片项的按钮逻辑:
- 未导出: 显示"导出"按钮（`ExportOutlined`）
- 已导出: 显示"打开"按钮（`FolderOpenOutlined`）+ "打开文件夹"按钮（`FolderOutlined`）
- 始终显示"删除"按钮

### 3.4 Workbench 中 clip_completed 更新切片状态

**文件**: `Workbench/index.tsx`

监听 `clip_completed` 事件时，更新对应 clip 的 `exported: true` 和 `outputPath`:

```typescript
unsubs.push(on('clip_completed', (data: any) => {
  if (data && typeof data.start === 'number') && typeof data.end === 'number') {
    // 更新切片的导出状态
    setClips(prev => prev.map(c => 
      c.start === data.start && c.end === data.end && c.room_id === data.room_id
        ? { ...c, exported: true, outputPath: data.output_path }
        : c
    ))
  }
}))
```

### 3.5 Workbench 传递新 props 给 ClipList

```tsx
<ClipList
  clips={clips}
  onDelete={handleDeleteClip}
  onExport={handleExportClip}
  onOpenFile={handleOpenExportFile}
  onOpenFolder={handleOpenExportFolder}
/>
```

---

## Step 4: Timeline seek 优化

**文件**: `Timeline/index.tsx`

**问题**: 拖拽时间线时每帧都 `onSeek` → `mseSeek` → `video.currentTime = time`，MSE 直播流 seek 到 buffered 范围外会导致 `waiting` + `play()` Promise pending → 卡死。

**方案**:

1. **拖拽时节流**: 用 `requestAnimationFrame` 节流，每帧最多 seek 一次
2. **松开时检查 buffered**: `handleMouseUp` 中检查目标时间是否在 buffered 范围内
   - 在范围内: 正常 seek
   - 不在范围内: seek 到 live edge（buffered.end - 0.5）+ play()
3. **拖拽中不实际 seek video**: 拖拽中只更新 UI 显示（`hoverTime`），松开时才真正 seek

```typescript
const rafRef = useRef<number | null>(null)
const pendingSeekTime = useRef<number | null>(null)

const handleMouseMove = useCallback((e: React.MouseEvent) => {
  const time = getTimeFromX(e.clientX)
  setHoverTime(time)
  if (draggingMarker && onMarkerDrag) {
    // 标记拖拽用 RAF 节流
    pendingSeekTime.current = time
    if (rafRef.current === null) {
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = null
        if (pendingSeekTime.current !== null && draggingMarker) {
          const snapped = snapTime(pendingSeekTime.current)
          onMarkerDrag(draggingMarker, snapped)
        }
      })
    }
    return
  }
  if (isDragging) {
    // 时间线拖拽用 RAF 节流
    pendingSeekTime.current = time
    if (rafRef.current === null) {
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = null
        if (pendingSeekTime.current !== null) {
          const snapped = snapTime(pendingSeekTime.current)
          onSeek(snapped)
        }
      })
    }
  }
}, [getTimeFromX, isDragging, onSeek, snapTime, draggingMarker, onMarkerDrag])

const handleMouseUp = useCallback(() => {
  setIsDragging(false)
  setDraggingMarker(null)
  // 取消未执行的 RAF
  if (rafRef.current !== null) {
    cancelAnimationFrame(rafRef.current)
    rafRef.current = null
  }
}, [])
```

**文件**: `Workbench/index.tsx`

`mseSeek` 增加 buffered 范围检查:

```typescript
const mseSeek = useCallback((roomId: string, time: number) => {
  const registry = (window as any).__msePlayers
  const video = registry?.[roomId]?.player?.videoElement as HTMLVideoElement | undefined
  if (video) {
    if (video.buffered.length > 0) {
      const bufStart = video.buffered.start(0)
      const bufEnd = video.buffered.end(video.buffered.length - 1)
      if (time >= bufStart && time <= bufEnd) {
        try { video.currentTime = time } catch {}
      } else {
        // 超出缓冲范围：seek 到 live edge
        video.currentTime = Math.max(bufStart, bufEnd - 0.5)
        video.play().catch(() => {})
      }
    }
  }
  send('seek', { room_id: roomId, time })
}, [send])
```

---

## Step 5: 入出点标记右键删除

**文件**: `Timeline/index.tsx`

新增 props:
```typescript
interface TimelineProps {
  // ... 现有 ...
  onDeleteMarker?: (type: 'in' | 'out') => void
}
```

标记元素添加 `onContextMenu`:

```typescript
{markerInPct !== null && (
  <div
    className="lsc-timeline__marker lsc-timeline__marker--in"
    style={{ left: `${markerInPct}%` }}
    onMouseDown={(e) => handleMarkerMouseDown(e, 'in')}
    onContextMenu={(e) => {
      e.preventDefault()
      e.stopPropagation()
      onDeleteMarker?.('in')
    }}
  >
    <span className="lsc-timeline__marker-label">入 {markIn !== null ? formatTime(markIn) : ''}</span>
  </div>
)}
```

同理出点标记。

**文件**: `Workbench/index.tsx`

```typescript
const handleDeleteMarker = useCallback((type: 'in' | 'out') => {
  selectedRoomIds.forEach(rid => {
    if (type === 'in') {
      send('set_mark_in', { room_id: rid, time: null })
    } else {
      send('set_mark_out', { room_id: rid, time: null })
    }
  })
}, [selectedRoomIds, send])
```

**文件**: `ControlBar.tsx`

传递 `onDeleteMarker` prop 给 Timeline。

**文件**: `room_handler.py` (后端)

`set_mark_in` / `set_mark_out` handler 需要处理 `time: null` 的情况:

```python
if time_value is not None:
    room.mark_in = float(time_value)
else:
    room.mark_in = None
    room.mark_in_wallclock = None
```

---

## Step 6: 静音按钮改为切换状态

**文件**: `Workbench/index.tsx`

```typescript
const [allMuted, setAllMuted] = useState(false)

const handleToggleAllMute = useCallback(() => {
  const newMuted = !allMuted
  setAllMuted(newMuted)
  rooms.forEach(r => {
    if (r.preview_enabled) {
      send('set_preview_muted', { room_id: r.room_id, muted: newMuted })
    }
  })
  message.info(newMuted ? '已全部静音' : '已取消静音')
}, [allMuted, rooms, send])

// 按钮渲染:
<Button
  size="small"
  type={allMuted ? 'primary' : 'default'}
  icon={allMuted ? <MutedOutlined /> : <SoundOutlined />}
  onClick={handleToggleAllMute}
  disabled={rooms.length === 0}
>
  {allMuted ? '取消静音' : '静音'}
</Button>
```

需要导入 `MutedOutlined`。

---

## 实施顺序

```
Step 1: RecordSettings 画质/编码器标签同一行
Step 2: CRF Slider min=18 max=28 移除tooltip
Step 3: types/index.ts ClipSegment 增加字段
Step 3: ClipList.tsx 改造（打开/打开文件夹按钮）
Step 3: Workbench/index.tsx 删除ExportQueue+最近切片栏+clip_completed更新
Step 4: Timeline/index.tsx RAF节流+拖拽优化
Step 4: Workbench/index.tsx mseSeek buffered检查
Step 5: Timeline/index.tsx 标记右键删除
Step 5: Workbench/index.tsx handleDeleteMarker
Step 5: room_handler.py set_mark_in/out 处理 null
Step 6: Workbench/index.tsx 静音切换
Step 7: 验证 typecheck + 构建 + 重启
```

## 风险评估

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| 删除ExportQueue后导出进度无处查看 | 中 | 用户不知道导出状态 | ClipList 中切片导出按钮变为进度指示器 |
| RAF 节流导致 seek 延迟 | 低 | 拖拽手感不跟手 | RAF 每帧执行一次，60fps 下延迟<16ms |
| 后端 set_mark_in 收到 null 报错 | 低 | 清除入点失败 | handler 中检查 time_value is not None |
