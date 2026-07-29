# 工作台四区层级 Implementation Plan

> **给执行代理：** 必须按任务逐项执行；每个步骤使用复选框跟踪。当前工作区有用户未提交改动，任何 `git add` 只能包含本计划实际改动的文件，且未经用户明确要求不得创建提交。

**目标：** 将工作台整理为“会话栏、房间网格、时间轴编辑、切片队列”四区，同时以两个小 hook 移除低耦合的批量房间与导出/草稿流程，不改变时间轴或后端协议。

**架构：** `Workbench/index.tsx` 继续持有时间轴、MSE/Web Audio 对齐、WebSocket 订阅和页面编排。`useRoomBatchActions` 只处理全选、批量录制/停止和批量静音；`useClipWorkflow` 只处理导出提交、导出确认和剪映草稿会话。切片确认/精修与音频对齐留在页面，防止把公共时间轴坐标转换复制到新 hook。

**技术栈：** React 18、TypeScript、Ant Design、Zustand、现有 WebSocket hook、pytest 源码守卫。

---

## 文件结构

| 文件 | 变更职责 |
|---|---|
| `lsc-electron/src/pages/Workbench/index.tsx` | 只重排四区 JSX，接入两个 hook，保留时间轴、对齐、确认/精修和 WebSocket 订阅。 |
| `lsc-electron/src/pages/Workbench/Workbench.css` | 为四区、会话栏、右侧队列和窄屏降级提供语义类；不新增硬编码色值。 |
| `lsc-electron/src/hooks/useRoomBatchActions.ts` | 新增：批量房间操作的稳定回调。 |
| `lsc-electron/src/hooks/useClipWorkflow.ts` | 新增：导出提交、确认弹窗和剪映草稿会话。 |
| `lsc-electron/src/pages/Workbench/components/ClipList.tsx` | 仅把卡片标题与摘要调整为“切片队列”语义；保留虚拟列表、选择、确认、导出行为。 |
| `lsc-electron/src/components/Layout/MainLayout.tsx` | 设置抽屉打开时让菜单正确选中“设置”。 |
| `tests/test_frontend_stability_guards.py` | 新增四区、抽屉选中态和两个 hook 的源码守卫；迁移受提取影响的导出断言。 |

## Task 1：先写四区与抽屉状态守卫

**文件：**

- 修改：`tests/test_frontend_stability_guards.py`

- [ ] **步骤 1：添加会失败的四区与抽屉选中态测试。**

  在文件末尾添加：

  ```python
  def test_workbench_uses_four_semantic_regions_and_queue_owns_analysis_entry() -> None:
      source = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
      for class_name in (
          "workbench-session-bar",
          "workbench-room-area",
          "workbench-editor",
          "workbench-clip-queue",
      ):
          assert f'className="{class_name}"' in source
      queue = source.split('className="workbench-clip-queue"', 1)[1]
      assert "<AnalysisProgress" in queue
      assert "onStartAnalysis={openAnalysisModal}" in queue


  def test_settings_menu_selected_key_tracks_open_drawer() -> None:
      source = (ROOT / "lsc-electron/src/components/Layout/MainLayout.tsx").read_text(encoding="utf-8")
      assert "settingsDrawerOpen ? '/settings' : location.pathname" in source
  ```

- [ ] **步骤 2：确认新守卫当前失败。**

  运行：

  ```powershell
  python -m pytest tests/test_frontend_stability_guards.py -k "four_semantic_regions or settings_menu_selected_key" -q --basetemp .tmp_frontend_four_zone -p no:cacheprovider
  ```

  预期：两个测试失败，指出四个 class 和抽屉选中表达式尚不存在。

## Task 2：实现四区布局和队列归属

**文件：**

- 修改：`lsc-electron/src/pages/Workbench/index.tsx:3379-3763`
- 修改：`lsc-electron/src/pages/Workbench/Workbench.css`
- 修改：`lsc-electron/src/pages/Workbench/components/ClipList.tsx:403-496`
- 修改：`lsc-electron/src/components/Layout/MainLayout.tsx:164`

- [ ] **步骤 1：在 `Workbench.css` 添加四区布局类。**

  添加以下规则；所有颜色都复用现有 token：

  ```css
  .workbench-session-bar { padding: 16px 24px; background: var(--bg-secondary); border-bottom: 1px solid var(--border-default); display: flex; flex-direction: column; gap: 12px; }
  .workbench-session-primary, .workbench-session-tools { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
  .workbench-content { flex: 1; display: flex; min-height: 0; overflow: hidden; }
  .workbench-room-area { flex: 1; display: flex; min-width: 0; flex-direction: column; overflow: hidden; }
  .workbench-editor { flex: 0 0 auto; }
  .workbench-clip-queue { width: 320px; min-height: 0; display: flex; flex-direction: column; overflow: hidden; border-left: 1px solid var(--border-default); background: var(--bg-secondary); }
  .workbench-queue-status { padding: 12px 16px 8px; border-bottom: 1px solid var(--border-default); }
  @media (max-width: 900px) { .workbench-content { flex-direction: column; overflow: auto; } .workbench-clip-queue { width: 100%; max-height: 42vh; border-left: 0; border-top: 1px solid var(--border-default); } }
  ```

- [ ] **步骤 2：重排 `Workbench` 的 render，不复制任何 handler。**

  - 将现有“顶部操作栏”外层替换为 `className="workbench-session-bar"`。
  - 第一行使用 `workbench-session-primary`，仅保留房间数、排序、添加房间按钮、批量录制和批量停止。
  - 第二行使用 `workbench-session-tools`，放全选、对齐、刷新和静音；`handleAlignLive` 及现有禁用条件保持原样。
  - 从顶部移除 `<AnalysisProgress>`、分析开始/停止按钮和草稿重试按钮；将它们置于 `workbench-clip-queue` 内的 `workbench-queue-status`，所有现有 `disabled`、`loading`、`Tooltip` 和回调保持同一表达式。
  - 将“添加直播间”的 `Card` 从右栏移入一个受 `addRoomModalOpen` 控制的 `Modal`；Modal 内复用原 `Input`、`url`、`setUrl`、`handleAddRoom`、`loading` 与回车行为。顶栏的“添加房间”仅执行 `setAddRoomModalOpen(true)`。
  - 为房间网格外层、`ControlBar` 包装层和右栏分别加上 `workbench-room-area`、`workbench-editor`、`workbench-clip-queue`。保留 `workbench-room-scroll`、`RoomCard`、`ControlBar` 和全部 props。

- [ ] **步骤 3：把 `ClipList` 的可见标题改为“切片队列”，但不改变列表算法。**

  将 `Card` 的标题由：

  ```tsx
  title="切片列表"
  ```

  改为：

  ```tsx
  title="切片队列"
  ```

  保持 `VIRTUALIZE_THRESHOLD`、`ROW_HEIGHT`、`contentVisibility`、所有确认/导出按钮和 `onStartAnalysis` 不变。

- [ ] **步骤 4：修正设置抽屉的菜单选中态。**

  在 `MainLayout.tsx` 的 `Menu` 中替换：

  ```tsx
  selectedKeys={[location.pathname]}
  ```

  为：

  ```tsx
  selectedKeys={[settingsDrawerOpen ? '/settings' : location.pathname]}
  ```

- [ ] **步骤 5：运行新守卫和类型检查。**

  运行：

  ```powershell
  cd lsc-electron
  npx tsc --noEmit
  cd ..
  python -m pytest tests/test_frontend_stability_guards.py -k "four_semantic_regions or settings_menu_selected_key or clip_list" -q --basetemp .tmp_frontend_four_zone -p no:cacheprovider
  ```

  预期：退出码 `0`；队列仍能传入 `onStartAnalysis={openAnalysisModal}`。

## Task 3：提取低耦合的批量房间操作

**文件：**

- 新增：`lsc-electron/src/hooks/useRoomBatchActions.ts`
- 修改：`lsc-electron/src/pages/Workbench/index.tsx:836-882`
- 修改：`tests/test_frontend_stability_guards.py`

- [ ] **步骤 1：先添加会失败的 hook 守卫。**

  ```python
  def test_workbench_batch_room_actions_are_extracted() -> None:
      workbench = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
      hook = ROOT / "lsc-electron/src/hooks/useRoomBatchActions.ts"
      assert "useRoomBatchActions" in workbench
      assert hook.is_file()
      source = hook.read_text(encoding="utf-8")
      assert "start_recording" in source
      assert "stop_recording" in source
      assert "set_preview_muted" in source
      assert "Modal.confirm" in source
  ```

- [ ] **步骤 2：确认该守卫失败。**

  运行：

  ```powershell
  python -m pytest tests/test_frontend_stability_guards.py::test_workbench_batch_room_actions_are_extracted -q --basetemp .tmp_frontend_four_zone -p no:cacheprovider
  ```

  预期：失败，提示 hook 文件不存在。

- [ ] **步骤 3：新增 `useRoomBatchActions`，只迁移批量操作。**

  hook 的公共接口固定为：

  ```ts
  import { useCallback, type Dispatch, type MutableRefObject, type SetStateAction } from 'react'

  type SendFn = (type: string, data?: any) => void

export function useRoomBatchActions(send: SendFn): {
  handleBatchRecord: () => void
  handleBatchStop: () => void
  setAllMutedForPreviewRooms: (muted: boolean) => void
}
  ```

  实现要求：

  - 每个 callback 通过 `useAppStore.getState().rooms` 读取最新房间，依赖数组只含 `[send]`。
  - `handleBatchRecord` 保留现有的 `is_connected && !is_recording` 过滤、`Modal.confirm` 和 `is_recording_starting: true` 乐观更新。
  - `handleBatchStop` 保留运行中持续分析的警告文案与危险确认按钮。
  - `setAllMuted` 只对 `preview_enabled` 房间执行 `updateRoom(...preview_muted)` 与 `send('set_preview_muted', ...)`。
  - 不迁移 `handleAlignLive`：它依赖 MSE 播放器、Web Audio 捕获、`aligningRoomIdsRef` 和对齐失败诊断。

- [ ] **步骤 4：在 `Workbench` 接入 hook，删除原批量 callback。**

  ```tsx
  const { handleBatchRecord, handleBatchStop, setAllMutedForPreviewRooms } = useRoomBatchActions(send)
  ```

  顶栏静音按钮改为：

  ```tsx
  onClick={() => {
    const nextMuted = !allMuted
    setAllMuted(nextMuted)
    setAllMutedForPreviewRooms(nextMuted)
  }}
  ```

  保持 `allMuted` 作为页面显示状态；不要把它升级到 Zustand。

- [ ] **步骤 5：运行批量房间、现有房间操作和类型验证。**

  ```powershell
  cd lsc-electron
  npx tsc --noEmit
  cd ..
  python -m pytest tests/test_frontend_stability_guards.py -k "batch_room_actions or workbench_optimistically_updates_connect_record_and_mute or workbench_room_actions_extracted" -q --basetemp .tmp_frontend_four_zone -p no:cacheprovider
  ```

  预期：退出码 `0`；单房间 `useRoomActions` 的乐观更新守卫继续通过。

## Task 4：提取导出提交和剪映草稿会话

**文件：**

- 新增：`lsc-electron/src/hooks/useClipWorkflow.ts`
- 修改：`lsc-electron/src/pages/Workbench/index.tsx:1993-2370`
- 修改：`tests/test_frontend_stability_guards.py:440-486`

- [ ] **步骤 1：先将导出守卫改为读取 workflow hook。**

  在测试文件定义：

  ```python
  def clip_workflow_source() -> str:
      return (ROOT / "lsc-electron/src/hooks/useClipWorkflow.ts").read_text(encoding="utf-8")
  ```

  并把 `test_clip_list_batch_export_includes_pending_confirmable_clips`、`test_batch_export_confirms_each_clip_with_own_bounds` 的 `export_many` 来源改为 `clip_workflow_source()`；断言 workflow 有 `confirmClip` 注入、`syncTargets: false`、`clip.start`、`clip.end` 与空列表提示。

- [ ] **步骤 2：运行守卫，确认它因缺少 hook 失败。**

  ```powershell
  python -m pytest tests/test_frontend_stability_guards.py -k "batch_export_includes_pending or batch_export_confirms_each" -q --basetemp .tmp_frontend_four_zone -p no:cacheprovider
  ```

  预期：失败，`useClipWorkflow.ts` 不存在。

- [ ] **步骤 3：新增 `useClipWorkflow`，明确时间轴边界。**

  接口使用回调注入，禁止 hook 自己读取或转换公共时间轴标记：

  ```ts
  import { type Dispatch, type MutableRefObject, type SetStateAction } from 'react'
  import type { ClipSegment, JianyingDraftResult } from '@/types'

  type SendFn = (type: string, data?: any) => void
  type OnFn = (type: string, handler: (data: any) => void) => () => void
  export type JianyingDraftRequest = {
    roomIds: string[]
    clips: ClipSegment[]
    includeClips: boolean
    includePending?: boolean
    allowSingleFallback?: boolean
    mainRoomId?: string
  }

  export function useClipWorkflow(opts: {
    send: SendFn
    on: OnFn
    exportPresetId: string
    confirmClip: (clip: ClipSegment, opts?: { syncTargets?: boolean; boundsOnly?: boolean }) => ClipSegment | null
    openExportPreview: (clip: ClipSegment) => void
    setJianyingLoading: Dispatch<SetStateAction<boolean>>
    pendingExportJobIdsRef: MutableRefObject<Set<string>>
  }): {
    handleConfirmAndExport: (clip: ClipSegment) => void
    handleExportMany: (clips: ClipSegment[]) => void
    requestJianyingDraft: (opts: JianyingDraftRequest) => Promise<JianyingDraftResult | null>
  }
  ```

  `handleExportMany` 必须先以 `confirmClip(clip, { syncTargets: false, boundsOnly: true })` 处理 pending/refining 片段，再基于各自的 `clip.start`、`clip.end` 发送导出；它不得读取 `commonMarkIn`、`commonMarkOut`、`timelineContext` 或 `localDragMark`。

- [ ] **步骤 4：在 `Workbench` 保留确认/精修，接入导出与草稿 workflow。**

  - 保留 `handleConfirmClip`、`handleSelectClip`、`handleMarkerDrag` 和时间轴坐标转换在 `Workbench`。
  - 使用 `useClipWorkflow` 返回的 `handleConfirmAndExport`、`handleExportMany` 和 `requestJianyingDraft` 传给现有 `ClipList`、快捷键和草稿重试路径。
  - 保持 `useExportProgressListeners` 原样；它继续负责 `export_progress`、`clip_completed`、`clip_failed` 的 WebSocket 监听。

- [ ] **步骤 5：运行导出、确认范围和导出监听验证。**

  ```powershell
  cd lsc-electron
  npx tsc --noEmit
  cd ..
  python -m pytest tests/test_frontend_stability_guards.py -k "clip_list_batch_export or batch_export_confirms or confirm_clip_optimistic or confirm_clip_sync_targets or workbench_updates_clip_export_status or workbench_export_listeners_extracted" -q --basetemp .tmp_frontend_four_zone -p no:cacheprovider
  ```

  预期：退出码 `0`；确认仍按房间范围乐观更新，导出进度监听仍在 `useExportProgressListeners.ts`。

## Task 5：完整回归与人工验收

**文件：**

- 验证：`lsc-electron/src/pages/Workbench/index.tsx`
- 验证：`lsc-electron/src/components/Layout/MainLayout.tsx`
- 验证：`tests/test_frontend_stability_guards.py`

- [ ] **步骤 1：运行完整静态验证。**

  ```powershell
  cd lsc-electron
  npx tsc --noEmit
  cd ..
  python -m pytest tests/test_frontend_stability_guards.py -q --basetemp .tmp_frontend_four_zone -p no:cacheprovider
  git diff --check
  ```

  预期：全部命令退出码 `0`。

- [ ] **步骤 2：进行手工工作流验收。**

  运行 `cd lsc-electron; npm run dev`，逐项验证：

  1. 顶栏“添加房间”弹窗可粘贴多行地址、回车提交，原 loading 与失败后保留输入行为存在。
  2. 首行只显示添加、开始录制、停止录制；全选、对齐、刷新、静音在第二行。
  3. 房间卡选择、单房间录制、批量录制/停止、静音和对齐均可用。
  4. 时间轴的拖拽、I/O、精修、缩放、回到直播和 DVR 左边界行为无回归。
  5. 右栏显示分析状态和切片队列；待确认、导出中、已完成项仍可确认、导出、取消、打开文件或文件夹。
  6. 打开设置抽屉时左侧“设置”菜单高亮；关闭后恢复“多房间管理”高亮。

## 计划自检

| 设计要求 | 对应任务 |
|---|---|
| 四区层级与分析入口归属 | Task 1、Task 2 |
| 设置抽屉选中态 | Task 1、Task 2 |
| 批量房间动作边界 | Task 3 |
| 导出/草稿会话边界 | Task 4 |
| 时间轴与后端协议不变 | Task 2、Task 3、Task 4、Task 5 |
| 类型、守卫和人工验收 | 每个任务的验证步骤及 Task 5 |

占位标记扫描：本计划不含待办占位或延后实现条目。类型一致性：`useRoomBatchActions` 与 `useClipWorkflow` 的接口、调用点和测试路径在各任务中使用同一名称。
