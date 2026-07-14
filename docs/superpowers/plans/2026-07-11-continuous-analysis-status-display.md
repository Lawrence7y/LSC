# 持续分析状态展示 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Workbench 顶部增加一个持续分析状态显示，让用户能直观看到持续分析是否运行、当前主房间、分析进度和完成结果。

**Architecture:** 复用现有 WebSocket 状态流，把持续分析状态聚合成一个独立的前端状态组件。Workbench 继续负责发起/停止分析，新增的状态组件只负责展示，不承担业务控制逻辑。若后端已有字段不足以支撑完整状态，则补充最小必要事件字段，优先保证前后端状态一致。

**Tech Stack:** React + TypeScript + Ant Design + existing WebSocket bridge + Python backend handlers.

---

### Task 1: 定义持续分析状态数据结构

**Files:**
- Modify: `lsc-electron/src/types/index.ts`
- Modify: `lsc-electron/src/store/appStore.ts`
- Modify: `python-backend/handlers/room_handler.py`

- [ ] **Step 1: Extend the frontend type to describe analysis status**

```ts
type ContinuousAnalysisStatus = {
  running: boolean
  room_id?: string | null
  target_room_ids?: string[]
  mode?: string
  analyzed_duration?: number
  total_highlights?: number
  phase?: 'idle' | 'running' | 'finalizing' | 'completed' | 'error'
  updated_at?: number
  error?: string | null
}
```

- [ ] **Step 2: Add matching store state for the current status payload**

```ts
type AppState = {
  // ... existing state ...
  continuousAnalysisStatus: ContinuousAnalysisStatus | null
  setContinuousAnalysisStatus: (status: ContinuousAnalysisStatus | null) => void
}
```

- [ ] **Step 3: Make the backend status response return the extra fields when available**

```py
@server.on('get_continuous_analysis_status')
async def handle_get_continuous_analysis_status(data):
    if _continuous_tasks:
        active_room_id = next(iter(_continuous_tasks))
        task = _continuous_tasks[active_room_id]
        return {
            'running': True,
            'room_id': active_room_id,
            'target_room_ids': task.get('target_room_ids', []),
            'mode': task.get('mode', 'scene'),
            'analyzed_duration': task.get('last_analyzed', 0.0),
            'total_highlights': len(task.get('highlights', [])),
            'phase': 'running',
            'updated_at': time.time(),
        }
    return {'running': False, 'phase': 'idle', 'updated_at': time.time()}
```

- [ ] **Step 4: Run the relevant type and backend tests**

Run:

```bash
pytest tests/test_shared_ingest.py tests/test_timeline_service.py -v
```

Expected: existing tests stay green; new status fields do not break the handler contract.

- [ ] **Step 5: Commit the data-model update**

```bash
git add lsc-electron/src/types/index.ts lsc-electron/src/store/appStore.ts python-backend/handlers/room_handler.py
git commit -m "feat: extend continuous analysis status payload"
```

### Task 2: Build a reusable status component for the Workbench header

**Files:**
- Create: `lsc-electron/src/components/AnalysisProgress.tsx`
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`

- [ ] **Step 1: Write the new component as a pure renderer of analysis state**

```tsx
import { Alert, Badge, Card, Progress, Space, Tag, Typography } from 'antd'

export default function AnalysisProgress({
  status,
}: {
  status: ContinuousAnalysisStatus | null
}) {
  if (!status?.running) {
    return <Card size="small">持续分析未运行</Card>
  }

  return (
    <Card size="small">
      <Space direction="vertical" size={6} style={{ width: '100%' }}>
        <Space>
          <Badge status="processing" />
          <Typography.Text strong>持续分析运行中</Typography.Text>
          <Tag>{status.mode ?? 'scene'}</Tag>
        </Space>
        <Typography.Text type="secondary">主房间：{status.room_id ?? '-'}</Typography.Text>
        <Progress percent={undefined} status="active" />
        <Typography.Text type="secondary">
          已分析：{formatDuration(status.analyzed_duration ?? 0)} · 高光：{status.total_highlights ?? 0}
        </Typography.Text>
      </Space>
      {status.phase === 'finalizing' && (
        <Alert type="info" message="录制已停止，正在收尾分析" showIcon />
      )}
    </Card>
  )
}
```

- [ ] **Step 2: Wire the current status from websocket/store into Workbench**

```tsx
const continuousAnalysisStatus = useAppStore((state) => state.continuousAnalysisStatus)

<AnalysisProgress status={continuousAnalysisStatus} />
```

- [ ] **Step 3: Feed websocket updates into the store so the component stays in sync**

```ts
unsubs.push(on('continuous_analysis_status', (data: ContinuousAnalysisStatus) => {
  setContinuousAnalysisStatus(data)
}))

unsubs.push(on('continuous_analysis_complete', (data: any) => {
  setContinuousAnalysisStatus({
    running: false,
    room_id: data?.room_id ?? null,
    total_highlights: data?.total_highlights ?? 0,
    phase: 'completed',
    updated_at: Date.now(),
  })
}))
```

- [ ] **Step 4: Run the Workbench frontend tests and lint checks**

Run:

```bash
npm --prefix lsc-electron test -- --runInBand
npm --prefix lsc-electron run lint
```

Expected: the new component renders without breaking the Workbench page.

- [ ] **Step 5: Commit the UI component wiring**

```bash
git add lsc-electron/src/components/AnalysisProgress.tsx lsc-electron/src/pages/Workbench/index.tsx
git commit -m "feat: show continuous analysis status in workbench"
```

### Task 3: Add the complete status lifecycle and error states

**Files:**
- Modify: `python-backend/handlers/room_handler.py`
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`

- [ ] **Step 1: Emit `finalizing` when recording stops but analysis is still draining**

```py
bridge.queue_broadcast({
    'type': 'continuous_analysis_status',
    'data': {
        'running': True,
        'room_id': room_id,
        'target_room_ids': target_room_ids,
        'mode': mode,
        'analyzed_duration': last_analyzed,
        'total_highlights': len(all_highlights),
        'phase': 'finalizing',
        'updated_at': time.time(),
    },
})
```

- [ ] **Step 2: Emit a completed state when the drain finishes**

```py
bridge.queue_broadcast({
    'type': 'continuous_analysis_status',
    'data': {
        'running': False,
        'room_id': room_id,
        'target_room_ids': target_room_ids,
        'mode': mode,
        'analyzed_duration': last_analyzed,
        'total_highlights': len(all_highlights),
        'phase': 'completed',
        'updated_at': time.time(),
    },
})
```

- [ ] **Step 3: Surface backend errors in the status component instead of only toasts**

```tsx
if (status?.phase === 'error' || status?.error) {
  return <Alert type="error" showIcon message="持续分析异常" description={status.error ?? '请重试或查看日志'} />
}
```

- [ ] **Step 4: Verify stop/start flows still work with the new lifecycle states**

Run:

```bash
pytest tests/test_synced_continuous_analysis.py tests/test_room_handler_lifecycle.py -v
```

Expected: start, stop, finalization, and completion all continue to pass.

- [ ] **Step 5: Commit the lifecycle polish**

```bash
git add python-backend/handlers/room_handler.py lsc-electron/src/pages/Workbench/index.tsx
git commit -m "feat: add continuous analysis lifecycle states"
```

### Task 4: Verify the end-to-end UX in the Workbench

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`
- Modify: `lsc-electron/src/components/AnalysisProgress.tsx`

- [ ] **Step 1: Confirm the Workbench header layout still fits on smaller windows**

```tsx
<div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
  <AnalysisProgress status={continuousAnalysisStatus} />
  <Button /* start/stop button stays beside it */ />
</div>
```

- [ ] **Step 2: Ensure the status clears or resets correctly when reconnecting to the backend**

```ts
if (!isConnected) {
  setContinuousAnalysisStatus(null)
}
```

- [ ] **Step 3: Manually validate these states in the app**

Run:

```bash
npm --prefix lsc-electron run dev
```

Check:
- no analysis running
- running analysis
- recording stopped but finalizing
- completed
- backend disconnected and reconnected

- [ ] **Step 4: Confirm there are no new lint errors in the edited files**

Run:

```bash
npm --prefix lsc-electron run lint -- src/components/AnalysisProgress.tsx src/pages/Workbench/index.tsx
```

Expected: zero new lint issues in the touched UI files.

- [ ] **Step 5: Commit the final UX verification cleanup**

```bash
git add lsc-electron/src/components/AnalysisProgress.tsx lsc-electron/src/pages/Workbench/index.tsx
git commit -m "feat: polish continuous analysis status display"
```

## Self-review checklist

- The plan covers the full requested feature: a visible continuous-analysis status area in the frontend.
- The file split is minimal and focused: one reusable component, one page integration point, one backend status source.
- No placeholder requirements remain; every step names exact files, commands, and expected outcomes.
- The status lifecycle is explicit enough for implementation: idle, running, finalizing, completed, error.
- The plan stays scoped to the Workbench and continuous analysis flow, without dragging in unrelated refactors.
