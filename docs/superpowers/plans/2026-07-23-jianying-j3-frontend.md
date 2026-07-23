# J3 — Jianying Draft Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 导出三选一（MP4 / 草稿 / 两者）、批量一次草稿、会话级入口、设置页目录配置、Electron `_isSafePath` 白名单扩展。默认 `exportTarget='mp4'`，不改变现有 MP4 导出习惯。

**Architecture:** 前端偏好 `localStorage` 键 `lsc.exportTarget`；草稿请求带内联 `clips`（见 J2）；成功后 Modal + `show-item-in-folder` 打开草稿目录。

**Tech Stack:** React + Ant Design（Radio / Segmented / Dropdown / Modal）、Zustand `appStore`（仅 `jianyingDraftDir`）、现有 `send()` WS

**Spec:** `docs/spec-jianying-draft-export.md` §前端实现、H1/H13、里程碑 J3  
**前置:** J1 + J2  
**后继:** 更新 `docs/PROJECT_DESIGN.md` §8

**执行约束：** 工作目录 `D:\Project\直播切片多人`；**不要 git commit**（除非用户要求）。

---

## Spec 校正

| Spec | 实际 |
|------|------|
| 头部已有「更多」⋯ 菜单 | **没有** → 在顶部操作栏右侧新增 `Dropdown` |
| `exportTarget` 进 appStore | 可用 localStorage；appStore 可选镜像。本 plan：localStorage 为主，设置目录进 settings |

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `lsc-electron/src/types/index.ts` | `JianyingDraftOptions` / `JianyingDraftResult` / `ExportTarget` |
| `lsc-electron/src/pages/Workbench/index.tsx` | 弹窗三选一、会话入口、草稿成功 Modal |
| `lsc-electron/src/pages/Workbench/components/ClipList.tsx` | 批量 Segmented |
| `lsc-electron/src/pages/Settings/index.tsx` | 剪映草稿目录行 |
| `lsc-electron/electron/main.ts` | `_isSafePath` + 读 settings 草稿目录 |
| `lsc-electron/src/hooks/useWebSocket.ts` | 可选：settings 里带上 `jianying_draft_dir` |
| `tests/test_jianying_frontend_guards.py` | 源码级 guard（与现有 ux habit guards 同风格） |
| `docs/PROJECT_DESIGN.md` | §8 新增剪映草稿小节 |

---

### Task 1: 类型 + frontend guards 骨架

**Files:**
- Modify: `lsc-electron/src/types/index.ts`
- Create: `tests/test_jianying_frontend_guards.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_jianying_frontend_guards.py
from __future__ import annotations

from pathlib import Path

TYPES = Path("lsc-electron/src/types/index.ts")
WORKBENCH = Path("lsc-electron/src/pages/Workbench/index.tsx")
CLIPLIST = Path("lsc-electron/src/pages/Workbench/components/ClipList.tsx")
SETTINGS = Path("lsc-electron/src/pages/Settings/index.tsx")
MAIN = Path("lsc-electron/electron/main.ts")


def test_types_export_export_target_and_jianying_result():
    text = TYPES.read_text(encoding="utf-8")
    assert "ExportTarget" in text
    assert "JianyingDraftResult" in text
    assert "JianyingDraftOptions" in text


def test_workbench_has_export_target_radio():
    text = WORKBENCH.read_text(encoding="utf-8")
    assert "exportTarget" in text
    assert "generate_jianying_draft" in text


def test_cliplist_batch_draft_sends_once_guard_comment_or_code():
    text = CLIPLIST.read_text(encoding="utf-8")
    assert "exportTarget" in text or "Segmented" in text


def test_settings_jianying_draft_dir_row():
    text = SETTINGS.read_text(encoding="utf-8")
    assert "jianying_draft_dir" in text or "剪映草稿" in text


def test_issafe_path_mentions_jianying():
    text = MAIN.read_text(encoding="utf-8")
    assert "jianying" in text.lower() or "Jianying" in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_jianying_frontend_guards.py::test_types_export_export_target_and_jianying_result -v`  
Expected: FAIL

- [ ] **Step 3: 追加 types**

在 `lsc-electron/src/types/index.ts` 合适位置追加：

```typescript
export type ExportTarget = 'mp4' | 'draft' | 'both'

export interface JianyingDraftOptions {
  include_recordings?: boolean
  include_clips?: boolean
  text_labels?: boolean
  vertical?: boolean
  draft_name?: string
  non_main_volume_zero?: boolean
}

export interface JianyingDraftResult {
  success: boolean
  draft_name?: string
  draft_dir?: string
  tracks?: number
  segments?: number
  warnings?: string[]
  error?: string
  error_code?: string
}
```

- [ ] **Step 4: 跑单测**

Run: `pytest tests/test_jianying_frontend_guards.py::test_types_export_export_target_and_jianying_result -v`  
Expected: PASS

---

### Task 2: 单条导出弹窗三选一

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`

- [ ] **Step 1: 增加 state + localStorage**

在 Workbench 组件内（其他 `useState` 附近）：

```typescript
type ExportTarget = 'mp4' | 'draft' | 'both'
const EXPORT_TARGET_KEY = 'lsc.exportTarget'

function readExportTarget(): ExportTarget {
  try {
    const v = localStorage.getItem(EXPORT_TARGET_KEY)
    if (v === 'mp4' || v === 'draft' || v === 'both') return v
  } catch { /* ignore */ }
  return 'mp4'
}

// inside component:
const [exportTarget, setExportTarget] = useState<ExportTarget>(() => readExportTarget())
const persistExportTarget = (v: ExportTarget) => {
  setExportTarget(v)
  try { localStorage.setItem(EXPORT_TARGET_KEY, v) } catch { /* ignore */ }
}

const [jianyingLoading, setJianyingLoading] = useState(false)
const [jianyingResult, setJianyingResult] = useState<JianyingDraftResult | null>(null)
```

- [ ] **Step 2: 抽取 `requestJianyingDraft`**

```typescript
  const requestJianyingDraft = async (opts: {
    roomIds: string[]
    clips: ClipSegment[]
    includeClips: boolean
    allowSingleFallback?: boolean
  }) => {
    setJianyingLoading(true)
    try {
      const payload = {
        room_ids: opts.roomIds,
        clip_ids: opts.clips
          .map(c => c.clip_snapshot_id || c.clip_id)
          .filter(Boolean),
        clips: opts.clips.map(c => ({
          clip_id: c.clip_id,
          clip_snapshot_id: c.clip_snapshot_id,
          label: c.label,
          common_start: c.common_start,
          common_end: c.common_end,
          mark_in_wallclock: c.mark_in_wallclock,
          mark_out_wallclock: c.mark_out_wallclock,
          mark_precision: c.mark_precision,
          confirm_status: c.confirm_status,
        })),
        options: {
          include_recordings: true,
          include_clips: opts.includeClips,
          text_labels: opts.includeClips,
          vertical: false,
          draft_name: '',
        },
        allow_single_fallback: opts.allowSingleFallback ?? false,
      }
      const res = await send('generate_jianying_draft', payload) as JianyingDraftResult
      // 若 send 不返回 body、只靠 *_response 事件：改为 Promise + once listener
      return res
    } finally {
      setJianyingLoading(false)
    }
  }
```

**对照项目里 `send` 的真实返回约定**：若现有 `export_clip` 靠 `*_response` 事件，则用 `wsClient.once('generate_jianying_draft_response', ...)` 同样模式，不要假设 `send` 返回业务 payload。

- [ ] **Step 3: 改 `handleConfirmExport`**

伪代码逻辑（嵌入现有函数，保留原 MP4 路径）：

```typescript
  const handleConfirmExport = async () => {
    if (!previewClip) return
    const room = rooms.find(r => r.room_id === previewClip.room_id)
    if (!room?.record_output_path && exportTarget !== 'draft') {
      message.error('该房间没有录制文件')
      return
    }

    const doMp4 = exportTarget === 'mp4' || exportTarget === 'both'
    const doDraft = exportTarget === 'draft' || exportTarget === 'both'

    if (doMp4) {
      // —— 保持现有 export_clip / export_clip_by_id 逻辑不变 ——
      // （把原来 handleConfirmExport 主体粘到这里）
    }

    if (doDraft) {
      if (isApproximateClip(previewClip)) {
        message.warning('近似定位切片不进入剪映草稿')
      } else {
        const res = await requestJianyingDraft({
          roomIds: timelineContext
            ? Object.keys(timelineContext.room_snapshots || {})
            : [previewClip.room_id],
          clips: [previewClip],
          includeClips: true,
          allowSingleFallback: true,
        })
        if (res?.success) {
          setJianyingResult(res)
          ;(res.warnings || []).forEach(w => message.warning(w, 4))
        } else if (res) {
          message.error(res.error || '生成剪映草稿失败')
        }
      }
    }

    if (exportTarget === 'draft') {
      setPreviewClip(null)
    }
    // mp4 / both：沿用原关闭时机
  }
```

- [ ] **Step 4: Modal UI**

在导出预览 Modal 的 footer 上方（preset Select 附近）加：

```tsx
<div style={{ marginTop: 12 }}>
  <div style={{ marginBottom: 8 }}><strong>导出方式</strong></div>
  <Radio.Group
    value={exportTarget}
    onChange={(e) => persistExportTarget(e.target.value)}
    options={[
      { value: 'mp4', label: '仅 MP4 切片' },
      { value: 'draft', label: '仅剪映草稿' },
      { value: 'both', label: '两者都要' },
    ]}
  />
</div>
```

确认按钮文案：

```typescript
const exportOkText =
  exportTarget === 'draft' ? '生成草稿'
  : exportTarget === 'both' ? '导出并生成草稿'
  : '确认导出'
```

`footer` 里 primary Button 使用 `exportOkText`，`loading={jianyingLoading}`。

- [ ] **Step 5: 草稿成功 Modal**

```tsx
<Modal
  title="剪映草稿已生成"
  open={!!jianyingResult?.success}
  onCancel={() => setJianyingResult(null)}
  footer={[
    <Button key="close" onClick={() => setJianyingResult(null)}>关闭</Button>,
    <Button
      key="open"
      type="primary"
      onClick={() => {
        const dir = jianyingResult?.draft_dir
        if (dir && window.electronAPI?.showItemInFolder) {
          window.electronAPI.showItemInFolder(dir)
        }
        setJianyingResult(null)
      }}
    >
      打开草稿目录
    </Button>,
  ]}
>
  {jianyingResult && (
    <>
      <p>草稿名：{jianyingResult.draft_name}</p>
      <p>轨道：{jianyingResult.tracks}　片段：{jianyingResult.segments}</p>
      {(jianyingResult.warnings || []).length > 0 && (
        <ul>{jianyingResult.warnings!.map((w, i) => <li key={i}>{w}</li>)}</ul>
      )}
      <p style={{ color: 'var(--text-tertiary)', fontSize: 12 }}>
        剪映中需重启或进出一次草稿以刷新列表
      </p>
    </>
  )}
</Modal>
```

IPC 方法名以 `preload.ts` 为准（可能是 `showItemInFolder` / `show-item-in-folder`）。

---

### Task 3: 批量导出 + ClipList Segmented

**Files:**
- Modify: `ClipList.tsx`
- Modify: `Workbench/index.tsx` — `handleExportMany`

- [ ] **Step 1: ClipList props**

```typescript
  exportTarget?: 'mp4' | 'draft' | 'both'
  onExportTargetChange?: (v: 'mp4' | 'draft' | 'both') => void
```

在「导出全部 / 导出所选」旁加：

```tsx
{onExportTargetChange && (
  <Segmented
    size="small"
    value={exportTarget || 'mp4'}
    onChange={(v) => onExportTargetChange(v as 'mp4' | 'draft' | 'both')}
    options={[
      { value: 'mp4', label: 'MP4' },
      { value: 'draft', label: '草稿' },
      { value: 'both', label: '两者' },
    ]}
  />
)}
```

Workbench 传入同一 `exportTarget` / `persistExportTarget`。

- [ ] **Step 2: 改 `handleExportMany`**

在现有 pending `boundsOnly` 确认逻辑之后、逐条 `export_clip` 循环之前分支：

```typescript
    if (exportTarget === 'draft' || exportTarget === 'both') {
      const draftClips = prepared.filter(c => !isApproximateClip(c))
      const skippedApprox = prepared.length - draftClips.length
      if (skippedApprox > 0) {
        message.warning(`${skippedApprox} 条近似定位切片已跳过（不进草稿）`)
      }
      const roomIds = timelineContext
        ? Object.keys(timelineContext.room_snapshots || {})
        : Array.from(new Set(draftClips.map(c => c.room_id)))
      void (async () => {
        const res = await requestJianyingDraft({
          roomIds,
          clips: draftClips,
          includeClips: true,
          allowSingleFallback: roomIds.length <= 1,
        })
        if (res?.success) {
          setJianyingResult(res)
          ;(res.warnings || []).forEach(w => message.warning(w, 4))
        } else if (res) {
          message.error(res.error || '生成剪映草稿失败')
        }
      })()
      if (exportTarget === 'draft') return
      // both: fall through to MP4 loop
    }
    // 现有逐条 export_clip / export_clip_by_id 循环保持不变
```

**Guard 断言（源码）：**

```python
def test_handle_export_many_draft_path_single_generate():
    text = WORKBENCH.read_text(encoding="utf-8")
    assert "generate_jianying_draft" in text
    # 批量草稿不应在 draft 分支里循环 send export_clip
    assert "exportTarget === 'draft'" in text or "exportTarget === \"draft\"" in text
```

---

### Task 4: 会话级「生成剪映草稿」入口

**Files:**
- Modify: `Workbench/index.tsx` 顶部操作栏

- [ ] **Step 1: 在顶部右侧加 Dropdown**

找到顶部 `Space` 工具按钮区域（「一键对齐」附近），追加：

```tsx
import { MoreOutlined } from '@ant-design/icons'
// ...
<Dropdown
  menu={{
    items: [
      {
        key: 'jianying',
        label: '生成剪映草稿',
        disabled: jianyingLoading,
        onClick: () => { void handleGenerateSessionDraft() },
      },
    ],
  }}
>
  <Button size="small" icon={<MoreOutlined />} />
</Dropdown>
```

- [ ] **Step 2: `handleGenerateSessionDraft`**

```typescript
  const handleGenerateSessionDraft = async () => {
    const selected = Array.from(selectedRoomIds)
    const roomIds = timelineContext
      ? Object.keys(timelineContext.room_snapshots || {})
      : selected.length ? selected : rooms.map(r => r.room_id)

    if (roomIds.length > 1 && !timelineContext) {
      message.warning('多房草稿需先一键对齐；可降级为主房单房草稿')
      // 给用户选择：
      Modal.confirm({
        title: '未对齐',
        content: '多房间尚未一键对齐。是否仅用当前主房生成单房草稿？',
        okText: '主房单房草稿',
        onOk: async () => {
          const mainId = selectedRoomId || roomIds[0]
          const res = await requestJianyingDraft({
            roomIds: [mainId],
            clips: clips.filter(c => canExportClip ? /* use same as ClipList */ true : true),
            includeClips: clips.length > 0,
            allowSingleFallback: true,
          })
          // 无切片时 includeClips=false
          ...
        },
      })
      return
    }

    const confirmed = clips.filter(c => {
      const s = c.confirm_status
      if (s === 'pending' || s === 'refining') return false
      if (isApproximateClip(c)) return false
      return true
    })
    const res = await requestJianyingDraft({
      roomIds,
      clips: confirmed,
      includeClips: confirmed.length > 0,
      allowSingleFallback: roomIds.length <= 1,
    })
    if (res?.success) setJianyingResult(res)
    else message.error(res?.error || '生成失败')
  }
```

实现时复用 `ClipList` 的 `canExportClip` 逻辑（可从 ClipList 导出该函数，或在 Workbench 内联相同判定）。**无切片时 `include_clips: false`**，仍生成录制轨。

---

### Task 5: 设置页

**Files:**
- Modify: `lsc-electron/src/pages/Settings/index.tsx`

- [ ] **Step 1: 在「存储路径」附近增加「剪映草稿目录」**

```tsx
<Form.Item label="剪映草稿目录">
  <Space direction="vertical" style={{ width: '100%' }}>
    <Space>
      <Typography.Text
        ellipsis
        style={{ maxWidth: 360 }}
        type={settings.jianying_draft_dir ? undefined : 'secondary'}
      >
        {settings.jianying_draft_dir
          || detectedJianyingDir
          || '未检测到剪映，请手动选择'}
      </Typography.Text>
      <Button size="small" onClick={async () => {
        const dir = await window.electronAPI?.selectDirectory?.()
        if (dir) handleRecordChange('jianying_draft_dir', dir)
      }}>更改</Button>
      <Button size="small" onClick={() => handleRecordChange('jianying_draft_dir', '')}>
        恢复自动探测
      </Button>
    </Space>
    {!settings.jianying_draft_dir && !detectedJianyingDir && (
      <Typography.Text type="warning">
        自动探测失败：请安装剪映专业版或手动指定草稿目录
      </Typography.Text>
    )}
  </Space>
</Form.Item>
```

挂载时：`send('get_jianying_draft_dir')`，监听 `get_jianying_draft_dir_response` 填 `detectedJianyingDir`。

确保 `save_settings` / 本地 settings state 含 `jianying_draft_dir` 字段。

---

### Task 6: `_isSafePath` 白名单

**Files:**
- Modify: `lsc-electron/electron/main.ts`

- [ ] **Step 1: 读 settings 中的草稿目录**

```typescript
function _readJianyingDraftDirFromSettings(): string | null {
  try {
    // settings.json 在项目根或 userData —— 与 backend 一致的位置：
    // 开发态常在 repo 根 settings.json；打包态可能不同。
    // 复用已有读 settings 的路径（若无，则尝试 path.join(app.getAppPath(), '..', 'settings.json')
    // 以及 path.join(app.getPath('userData'), 'settings.json')）
    const candidates = [
      path.join(process.cwd(), 'settings.json'),
      path.join(app.getPath('userData'), 'settings.json'),
    ]
    for (const fp of candidates) {
      if (!fs.existsSync(fp)) continue
      const raw = JSON.parse(fs.readFileSync(fp, 'utf-8'))
      const dir = (raw.jianying_draft_dir || '').trim()
      if (dir) return path.resolve(dir)
      // 空 = 自动探测
      const local = process.env.LOCALAPPDATA
      if (local) {
        const auto = path.join(local, 'JianyingPro', 'User Data', 'Projects', 'com.lveditor.draft')
        if (fs.existsSync(auto)) return path.resolve(auto)
      }
    }
  } catch (e) {
    appLog('WARN', 'SafePath', `read jianying draft dir failed: ${e}`)
  }
  return null
}

function _isSafePath(p: string): boolean {
  // ... existing checks ...
  const allowedRoots = [
    app.getPath('userData'),
    path.join(app.getPath('home'), 'LSC'),
  ]
  const jy = _readJianyingDraftDirFromSettings()
  if (jy) allowedRoots.push(jy)
  // ... rest unchanged ...
}
```

引入 `fs`（若尚未 import）。

- [ ] **Step 2: guard 测试应变绿**

Run: `pytest tests/test_jianying_frontend_guards.py -v`  
Expected: 全部 PASS（实现完 Task 2–6 后）

---

### Task 7: PROJECT_DESIGN 文档小节

**Files:**
- Modify: `docs/PROJECT_DESIGN.md` §第八部分末尾追加

```markdown
### 8.x 剪映草稿导出

一场直播可生成一个剪映专业版草稿（`pyJianYingDraft`）：每房录制轨 + 每房切片轨 + 可选回合文本轨，时间轴为公共对齐轴。

- 模块：`lsc/exporter/jianying_draft.py`
- WS：`generate_jianying_draft` / `get_jianying_draft_dir`
- 导出目标三选一：MP4 / 草稿 / 两者（默认 MP4）
- 不做剪映 UI 自动导出；用户在剪映内手动导出成片
- 详见 `docs/spec-jianying-draft-export.md`
```

（小节编号按文档现有 8.x 顺延。）

- [ ] **Step 1: tsc**

Run: `cd lsc-electron && npx tsc --noEmit`  
Expected: 无新增错误

- [ ] **Step 2: 全 guard**

Run: `pytest tests/test_jianying_frontend_guards.py tests/test_jianying_ws_guards.py tests/test_jianying_draft.py tests/test_jianying_dependency_guard.py -v`  
Expected: PASS

---

## J3 完成标准

- 默认 MP4 行为与改前一致
- 三种导出目标手动可点通
- 批量草稿只发一次 `generate_jianying_draft`
- 设置页可改/恢复草稿目录
- 「打开草稿目录」在已配置目录下不被 `_isSafePath` 拒绝
- `PROJECT_DESIGN.md` 已更新
