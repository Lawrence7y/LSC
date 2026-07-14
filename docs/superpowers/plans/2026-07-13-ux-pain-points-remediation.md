> **Status (2026-07-14):** 主体已在代码落地；剩余项并入 `2026-07-14-next-iteration-trust-platform-hygiene.md`。请勿按本文件空 checkbox 从头重做。

# UX 十一痛点整改 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除用户高频路径上最伤信任与最易误触的 11 个体验问题，让「连接 → 预览 → 录制 → 标记 → 对齐 → 导出」结果可信、操作可预期。

**Architecture:** 分三阶段推进。P0 修「结果不准 / 假成功 / 误杀录制 / 状态撒谎」；P1 修「慢、黑、绑死、门槛不清」；P2 做体验打磨（画质提示、分析引导、文档一致）。原则：先改契约与守卫测试，再改 UI；不扩大产品范围（不做 NLE、不做自动更新安装）。

**Tech Stack:** Electron React + Zustand；Python WebSocket `room_handler` + `MultiRoomManager`；pytest 源码守卫 + 行为测试。

---

## 0. 痛点总表与分期

| # | 痛点 | 用户感受 | 分期 | 主要落点 |
|---|------|----------|------|----------|
| 1 | 拖拽标记导出偏几秒 | 调了时间线反而更不准 | P0 | `Workbench` 拖拽 + `set_mark_*` + `queue_export` |
| 2 | 列表导出被当前墙钟覆盖 | 多个切片导成同一段 | P0 | `ClipSegment` 快照 + `export_clip` / `queue_export` |
| 3 | 断开无确认却停录；`R` 键绕过确认 | 误触直接丢录制 | P0 | `RoomCard` / `handleDisconnect` / `record:toggle` |
| 4 | 连接态撒谎 / 假成功 | 已连却拒录；点连接像立刻成功 | P0 | `connect_room` 响应语义 + `is_connected` 治愈/广播 |
| 5 | 对齐假成功 + 自动静音 | 看着齐导出不齐；声音突然没了 | P0 | `handleAlignLive` / 对齐响应文案 |
| 6 | 抖音 Cookie / B站刷流慢黑屏 | 突然全挂；点预览干等 | P1 | Cookie 引导 UI + 预览 loading/进度 |
| 7 | 多路开录被 Semaphore(2) 卡住 | 一键开录后几路像失灵 | P1 | 录制队列进度广播 + 前端排队态 |
| 8 | 共享进样录预览绑死 | 录一抖预览跟着黑 | P1 | 默认策略/设置说明 + 故障提示 |
| 9 | 未录制也能走完标记 | 最后导出才报没有文件 | P1 | `handleAddClip` / ControlBar 禁用与提示 |
| 10 | 长按刷新是核弹 | 误长按全场停录 | P0（安全） | `RefreshButton` + `handleRefreshLongPress` |
| 11 | 预览画质静默降 + 分析门槛绕 | 糊了不知道；按钮一直灰 | P2 | 降级横幅 + 分析 tooltip/对齐组检测 |

> 说明：上轮口述把「危险操作」拆成断开/`R`/长按刷新；本表将长按刷新单列为 #10，与 #3 同属「操作安全」但实现独立。

---

## 1. 文件职责图

| 文件 | 职责 |
|------|------|
| `lsc-electron/src/types/index.ts` | `ClipSegment` 增加墙钟快照字段；对齐结果类型 |
| `lsc-electron/src/pages/Workbench/index.tsx` | 加切片快照、对齐诚实文案、危险确认、未录制拦截、分析门槛 |
| `lsc-electron/src/pages/Workbench/components/RoomCard.tsx` | 断开确认；连接/预览加载态 |
| `lsc-electron/src/pages/Workbench/components/RefreshButton.tsx` | 长按二次确认 |
| `lsc-electron/src/pages/Workbench/components/ClipList.tsx` | 近似标记徽章（若需） |
| `lsc-electron/src/pages/Settings/index.tsx` | 共享进样风险说明；Cookie 引导入口 |
| `lsc-electron/src/components/Timeline/index.tsx` | 拖拽标记后 UI 提示「近似」 |
| `python-backend/handlers/room_handler.py` | `queue_export` 优先用请求内快照；`connect_room` 语义；预览/录制进度；`is_live`（若需要） |
| `lsc/gui/multi_room/manager.py` | `is_connected` 治愈不撒谎；共享进样故障文案 |
| `tests/test_frontend_stability_guards.py` | 前端行为守卫 |
| `tests/test_clip_snapshot_handlers.py` 或新建 | 导出时间契约测试 |

---

## 2. 目标行为契约（全阶段共用）

### 2.1 导出时间优先级（P0 核心）

```
若请求带 mark_in_wallclock + mark_out_wallclock + recording_start_mono（切片快照）
  → 使用快照计算 export_start/end（再减 content_offset）
否则若 source == 'ai_highlight'
  → 使用传入 start/end
否则若房间当前有完整墙钟且请求显式 use_room_marks=true（仅「导当前选区」）
  → 使用房间当前墙钟
否则
  → 使用传入 start/end + content_offset，并在响应里带 precision='approximate'
```

**禁止**：列表导出时静默用「房间当前 mark_*_wallclock」覆盖切片自己的 `start/end`。

### 2.2 对齐结果分级（P0）

| 结果 | UI | `align_group_id` | `content_offset` |
|------|-----|------------------|------------------|
| 精确成功（均分置信度 ≥ 0.3） | success + 可选静音说明 | 写入 | 写入有效 offset |
| 部分成功 | warning 列出低置信房间 | 仅写入高置信房间 | 低置信强制 0 |
| 仅缓冲区对齐 / 捕获失败 | **error/warning「未精确对齐」**，禁止写「已对齐成功」 | **不写入** | 不覆盖已有精确值（或保持 0） |

### 2.3 危险操作统一（P0）

凡会停止录制的路径，必须二次确认（Modal），文案写明「将停止录制」：
- 断开连接
- `R` / 单房间停止录制（与按钮一致）
- 长按刷新（全场停录）
- 批量停止（已有，保持）

---

## Phase P0 — 信任与安全（优先执行）

### Task 1: 切片入队时快照墙钟（痛点 #2）

**Files:**
- Modify: `lsc-electron/src/types/index.ts`
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`（`handleAddClip`）
- Modify: `python-backend/handlers/room_handler.py`（`queue_export` / `export_clip`）
- Test: `tests/test_frontend_stability_guards.py` + `tests/test_clip_export_time_mapping.py`（新建）

- [ ] **Step 1: 写失败测试 — 列表导出不得被房间当前墙钟覆盖**

```python
# tests/test_clip_export_time_mapping.py
def test_queue_export_prefers_request_wallclock_snapshot_over_room_marks():
    """export_clip 携带快照墙钟时，不得改用房间当前 mark_*_wallclock。"""
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    body = source.split("async def queue_export(", 1)[1].split("async def ", 1)[0]
    assert "mark_in_wallclock" in body
    assert "snapshot" in body or "req_mark_in_wc" in body or "data.get('mark_in_wallclock')" in source
    # 契约：存在「请求快照优先于 room.mark_*_wallclock」分支
    assert "export_start = max(0.0, mark_in_wc - rec_start" in body  # 现有
    # 新逻辑必须先读请求字段
    assert "data.get('mark_in_wallclock')" in source or "payload.get('mark_in_wallclock')" in source or \
           "mark_in_wallclock=data.get" in source.replace(" ", "")
```

同时加前端守卫：

```python
def test_add_clip_snapshots_wallclock_fields() -> None:
    workbench = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    body = workbench.split("const handleAddClip = useCallback", 1)[1].split("}, [addClip])", 1)[0]
    assert "mark_in_wallclock" in body
    assert "recording_start_mono" in body
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/test_frontend_stability_guards.py::test_add_clip_snapshots_wallclock_fields tests/test_clip_export_time_mapping.py -v
```

Expected: FAIL（字段/分支不存在）

- [ ] **Step 3: 扩展 `ClipSegment` 并在 `handleAddClip` 写入快照**

`types/index.ts` 增加：

```ts
mark_in_wallclock?: number | null
mark_out_wallclock?: number | null
recording_start_mono?: number | null
recording_media_start_mono?: number | null
mark_precision?: 'exact' | 'approximate'
```

`handleAddClip` 核心：

```ts
const newClip: ClipSegment = {
  start: room.mark_in,
  end: room.mark_out,
  label: `${room.streamer_name} - 片段 ${currentClips.length + 1}`,
  room_id: roomId,
  mark_in_wallclock: room.mark_in_wallclock ?? null,
  mark_out_wallclock: room.mark_out_wallclock ?? null,
  recording_start_mono: room.recording_start_mono ?? null,
  recording_media_start_mono: room.recording_media_start_mono ?? null,
  mark_precision:
    room.mark_in_wallclock != null && room.mark_out_wallclock != null
      ? 'exact'
      : 'approximate',
}
```

- [ ] **Step 4: `export_clip` / `queue_export` 优先用请求快照**

在 `handle_export_clip` 把前端字段传入 `queue_export`；`queue_export` 签名增加可选墙钟参数：

```python
async def queue_export(..., mark_in_wallclock=None, mark_out_wallclock=None,
                       recording_start_mono=None, recording_media_start_mono=None,
                       use_room_marks=False):
    ...
    if source == 'ai_highlight':
        export_start = max(0.0, start_sec)
        export_end = max(0.0, end_sec)
    else:
        snap_in = mark_in_wallclock
        snap_out = mark_out_wallclock
        snap_rec = recording_media_start_mono or recording_start_mono
        if snap_in is not None and snap_out is not None and snap_rec is not None:
            content_offset = float(getattr(room, 'content_offset', 0.0) or 0.0)
            export_start = max(0.0, snap_in - snap_rec - content_offset)
            export_end = max(0.0, snap_out - snap_rec - content_offset)
            precision = 'exact'
        elif use_room_marks:
            # 仅「导当前选区」显式开启
            ...  # 现有 room.mark_*_wallclock 逻辑
            precision = 'exact'
        else:
            content_offset = float(getattr(room, 'content_offset', 0.0) or 0.0)
            export_start = max(0.0, start_sec - content_offset)
            export_end = max(0.0, end_sec - content_offset)
            precision = 'approximate'
            _log.warning("导出降级：无墙钟快照，使用 start/end (room=%s)", room_id)
```

`handleExportMany` / `handleConfirmExport` 发送：

```ts
send('export_clip', {
  room_id: clip.room_id,
  start: clip.start,
  end: clip.end,
  label: clip.label,
  mark_in_wallclock: clip.mark_in_wallclock,
  mark_out_wallclock: clip.mark_out_wallclock,
  recording_start_mono: clip.recording_start_mono,
  recording_media_start_mono: clip.recording_media_start_mono,
  use_room_marks: false,
  ...
})
```

- [ ] **Step 5: 跑测试通过并提交**

```bash
pytest tests/test_clip_export_time_mapping.py tests/test_frontend_stability_guards.py -v -k "clip or wallclock or export"
```

```bash
git add lsc-electron/src/types/index.ts lsc-electron/src/pages/Workbench/index.tsx \
  python-backend/handlers/room_handler.py tests/test_clip_export_time_mapping.py \
  tests/test_frontend_stability_guards.py
git commit -m "$(cat <<'EOF'
fix: export clips from list using wallclock snapshots

Prevent queue_export from overriding per-clip ranges with the room's
current mark wallclock, which made multi-clip exports all cut the same segment.
EOF
)"
```

---

### Task 2: 拖拽标记标明「近似」并避免假精确（痛点 #1）

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`（timeline `live: false` 路径）
- Modify: `lsc-electron/src/components/Timeline/index.tsx` 或 ControlBar 状态展示
- Test: `tests/test_frontend_stability_guards.py`

**设计选择（已定）：** 不在本阶段发明新的 MSE→墙钟映射算法（易引入更大偏差）。改为：
1. 拖拽仍 `live: false`（不写墙钟）
2. UI 明确显示「近似定位」
3. 从近似标记添加的切片 `mark_precision='approximate'`，导出前 toast 警告
4. 精确导出仍引导用户用 `I`/`O`

- [ ] **Step 1: 写失败测试**

```python
def test_scrub_mark_surfaces_approximate_precision() -> None:
    workbench = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    assert "live: false" in workbench
    assert "approximate" in workbench or "近似" in workbench
```

- [ ] **Step 2: 跑测失败 → 实现 UI 提示 + 导出警告 → 跑通 → commit**

```bash
git commit -m "fix: label scrub marks as approximate to avoid false precision"
```

---

### Task 3: 危险操作统一确认（痛点 #3 + #10）

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`（`handleDisconnect`、`record:toggle`、`handleRefreshLongPress`）
- Modify: `lsc-electron/src/pages/Workbench/components/RoomCard.tsx`（断开按钮可改为回调内确认，避免双重 Modal）
- Modify: `lsc-electron/src/pages/Workbench/components/RefreshButton.tsx`（tooltip 文案加强）
- Test: `tests/test_frontend_stability_guards.py`

- [ ] **Step 1: 写失败测试**

```python
def test_destructive_stop_recording_paths_require_confirm() -> None:
    workbench = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    disconnect = workbench.split("const handleDisconnect = useCallback", 1)[1].split("}, [send])", 1)[0]
    assert "Modal.confirm" in disconnect
    # R 键停止录制不得直接 handleStopRecord 而无确认
    toggle = workbench.split("case 'record:toggle'", 1)[1].split("case '", 1)[0]
    assert "Modal.confirm" in toggle or "confirmStopRecording" in toggle
    longpress = workbench.split("const handleRefreshLongPress", 1)[1].split("}, [", 1)[0]
    assert "Modal.confirm" in longpress
```

- [ ] **Step 2: 抽取共用确认**

```ts
const confirmStopRecording = (title: string, content: string, onOk: () => void) => {
  Modal.confirm({
    title,
    content,
    okText: '确认',
    okButtonProps: { danger: true },
    cancelText: '取消',
    onOk,
  })
}
```

- `handleDisconnect`：若 `room.is_recording`，先 confirm「断开将停止录制」，再执行现有停录/停预览/断开。
- `record:toggle` 停止分支：与 RoomCard 停止按钮同一 confirm。
- `handleRefreshLongPress`：confirm「将停止全部房间的录制、预览与分析，然后重启预览」。

- [ ] **Step 3: 避免双重 Modal** — RoomCard 断开若父级已确认，则 `onDisconnect` 不再二次确认；或 RoomCard 只负责点击、确认集中在 Workbench。

- [ ] **Step 4: 测试通过并 commit**

```bash
git commit -m "fix: require confirm before actions that stop recordings"
```

---

### Task 4: 对齐结果诚实化（痛点 #5）

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`（`handleAlignLive` + `align_preview_audio_response`）
- Modify: `python-backend/handlers/room_handler.py`（对齐失败时不写 `align_group_id`）
- Test: 现有 `test_workbench_alignment_*` 需同步改期望（从「允许假成功」改为「禁止假成功」）

- [ ] **Step 1: 更新/新增守卫测试**

```python
def test_alignment_buffer_fallback_is_not_success() -> None:
    workbench = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    # 捕获失败或仅缓冲对齐不得 message.success 宣称精确对齐
    assert "已使用缓冲区对齐" in workbench
    # 成功文案不得在 fallback 路径复用
    align_fail = workbench.split("音频对齐失败", 1)[1][:400]
    assert "message.success" not in align_fail
```

```python
def test_low_confidence_align_does_not_write_group_for_failed_rooms() -> None:
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    # 对齐 handler 仅对可信 offset 写 align_group_id（保持或加强现有逻辑）
    assert "align_group_id" in source
```

- [ ] **Step 2: 前端文案与静音策略**

1. 捕获不足 / 后端 `success=false`：`message.warning('未精确对齐：已仅做预览缓冲区对齐，导出可能不同步')`，**不**调用会写入 group 的成功路径。
2. 低置信度房间：汇总 `message.warning(\`精确对齐 ${ok} 路，${bad} 路置信度不足已跳过\`)`。
3. 自动静音：保留消除回声，但改为可选——默认仍静音，toast 必须说明「已静音 N 个快房间（可手动取消静音）」；设置项可后续 P2 再加开关。

- [ ] **Step 3: 后端确保 fallback 不写 `align_group_id`**

检查 `handle_align_preview_audio`：仅在至少 2 个房间有效 offset 时写 group；否则返回 `success: false` 或 `precision: 'buffer_only'`。

- [ ] **Step 4: 跑对齐相关测试 → commit**

```bash
pytest tests/test_frontend_stability_guards.py -v -k "align"
git commit -m "fix: stop presenting buffer-only alignment as success"
```

---

### Task 5: 连接态语义可信（痛点 #4）

**Files:**
- Modify: `python-backend/handlers/room_handler.py`（`handle_connect_room`）
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`（`connect_room_response`）
- Modify: `lsc/gui/multi_room/manager.py`（预览失败勿误清 `is_connected`；保持 heal 逻辑）
- Test: `tests/test_room_handler_lifecycle.py` / `tests/test_multi_room_manager.py`

- [ ] **Step 1: 明确 API 契约**

`connect_room` 在 `async_mode=True` 时返回：

```python
{'success': True, 'accepted': True, 'room_id': room_id, 'async': True}
```

前端：
- `success && async` → **不** toast「连接成功」
- 仅 `room_connect_finished.success` → toast 成功/失败
- `connect_room_response` 若 `accepted=false` 才立刻回滚 `is_connecting`

- [ ] **Step 2: 守卫 — 预览刷新失败不得在有缓存时清连接**

已有 `test_start_recording_heals_stale_is_connected_when_stream_cache_exists`；补充：

```python
def test_preview_refresh_failure_keeps_connected_when_cache_exists():
    # 断言 room_handler 中 _mark_disconnected_if_no_stream 在 has_url 时 return
    ...
```

- [ ] **Step 3: 实现 + 测试 + commit**

```bash
git commit -m "fix: make connect_room async acceptance explicit and protect connected state"
```

---

## Phase P1 — 卡顿、门槛、绑死

### Task 6: 预览/平台等待态可见（痛点 #6）

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/components/RoomCard.tsx` / `VideoPreview.tsx`
- Modify: `python-backend/handlers/room_handler.py`（可选：`preview_starting` 广播）
- Modify: `lsc-electron/src/pages/Settings/index.tsx`（抖音 Cookie 状态与一键打开指引）

- [ ] **Step 1: 预览启动中显示「正在拉流/转码…」**（`preview_enabled && !firstFrame`）
- [ ] **Step 2: 抖音 `requires_cookies` / 验证码错误 → 卡片/通知带「去设置 Cookie」按钮
- [ ] **Step 3: 测试守卫（文案/字段存在）→ commit**

```bash
git commit -m "feat: show preview starting state and clearer Douyin cookie guidance"
```

---

### Task 7: 多路开录排队可见（痛点 #7）

**Files:**
- Modify: `python-backend/handlers/room_handler.py`（`handle_start_recording` / batch）
- Modify: `lsc-electron/src/pages/Workbench/components/RoomCard.tsx`

- [ ] **Step 1: 进入 semaphore 前广播** `recording_queue: { room_id, position, waiting: true }`
- [ ] **Step 2: 前端 `is_recording_starting` 文案区分「排队中」/「启动 FFmpeg」
- [ ] **Step 3: 测试 + commit**

```bash
git commit -m "feat: surface recording start queue position under concurrency limit"
```

---

### Task 8: 共享进样风险可见（痛点 #8）

**Files:**
- Modify: `lsc-electron/src/pages/Settings/index.tsx`
- Modify: `lsc/config.py`（**不强制改默认**，除非产品确认；本任务默认只加说明）
- Optional: 录制故障广播附加 `preview_coupled: true`

- [ ] **Step 1: 设置页 `shared_ingest_enabled` 旁增加警告文案**  
  「开启后预览与录制共用同一进程：录制中断会导致预览中断，预览转码可能影响录制稳定性。」
- [ ] **Step 2: 若产品确认改默认 → 另开子任务改 `shared_ingest_enabled: bool = False` 并更新 `tests/test_config.py`
- [ ] **Step 3: commit**

```bash
git commit -m "docs(ui): explain shared ingest coupling risk in settings"
```

---

### Task 9: 未录制禁止「假完整」切片路径（痛点 #9）

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`（`handleAddClip`、ControlBar 添加按钮）
- Modify: `lsc-electron/src/pages/Workbench/components/ControlBar.tsx`

- [ ] **Step 1: 测试**

```python
def test_add_clip_requires_recording_file() -> None:
    workbench = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    body = workbench.split("const handleAddClip = useCallback", 1)[1].split("}, [addClip])", 1)[0]
    assert "record_output_path" in body
```

- [ ] **Step 2: 无 `record_output_path` 时 `message.warning('请先开始录制后再添加切片')` 并 return；按钮 disabled + tooltip
- [ ] **Step 3: commit**

```bash
git commit -m "fix: block adding clips when room has no recording file"
```

---

## Phase P2 — 打磨与引导

### Task 10: 预览画质降级明示（痛点 #11 前半）

**Files:**
- Modify: `python-backend/handlers/room_handler.py`（`_compute_preview_quality_params` 返回 `degraded`/`reason`）
- Modify: Workbench 顶栏或 RoomCard 小条：「多路预览已降为 360p 以保流畅」

- [ ] 广播或 `enable_preview_response` 带上实际 `width/height/fps`
- [ ] UI 展示一次（可关闭）
- [ ] commit: `feat: show preview quality degradation banner`

---

### Task 11: 持续分析门槛可理解（痛点 #11 后半）

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`（`analysisEnabled` / tooltip）

- [ ] tooltip 按缺失条件分支：
  - 未选房间
  - 无录制文件
  - 多房间但 `align_group_id` 不一致 / 为空（含「仅缓冲区对齐」）
- [ ] 灰按钮旁提供「去对齐」快捷动作
- [ ] commit: `fix: explain continuous analysis prerequisites in tooltip`

---

### Task 12: 文档与快捷键一致（顺带）

**Files:**
- Modify: `CLAUDE.md` §12.1（`Ctrl+A` → `Ctrl+Shift+A`）
- Modify: Settings 快捷键表若有遗漏一并核对

- [ ] commit: `docs: align shortcut table with Ctrl+Shift+A select-all`

---

## 3. 验收清单（人工）

**P0**
- [ ] 添加两个不同入出点切片并批量导出 → 两段内容不同
- [ ] 仅拖拽标记添加的切片 → UI 显示近似，导出有警告
- [ ] 录制中点断开 / 按 `R` / 长按刷新 → 均出现确认，取消则不停录
- [ ] 对齐捕获失败 → 不出现「已精确对齐」；持续分析多房仍要求真正对齐组
- [ ] 连接中不 toast 成功；失败才提示；有流缓存时预览刷新失败不掉「已连接」

**P1**
- [ ] 开预览出现「拉流中」；抖音缺 Cookie 可跳转设置
- [ ] 一次开 4 路录制 → 后两路显示排队而非假死
- [ ] 未录制无法添加切片
- [ ] 设置页能看懂共享进样风险

**P2**
- [ ] ≥3 路预览出现降级提示
- [ ] 分析按钮灰态原因可读

---

## 4. 非目标（本计划不做）

- 不重做音频对齐算法 / 不更换互相关方案
- 不实现拖拽标记的完美墙钟反算（P0 仅诚实标注近似）
- 不提高录制启动 Semaphore 到无限（只做排队可见；若要改并发另开性能专项）
- 不自动下载 Cookie、不绕过平台风控
- 不做 NLE / 特效 / 自动更新安装

---

## 5. 建议执行顺序

```
Task1 切片快照导出 ─┐
Task2 拖拽近似提示 ─┼─► Task3 危险确认 ─► Task4 对齐诚实 ─► Task5 连接语义
                    │
                    └─►（可并行）测试文件骨架

然后 P1: Task9 → Task6 → Task7 → Task8
然后 P2: Task10 → Task11 → Task12
```

Task1 与 Task2 强相关，建议同一 agent 连续做；Task3 可并行于 Task4。

---

## 6. Spec 覆盖自检

| 痛点 | 对应 Task |
|------|-----------|
| #1 拖拽不准 | Task 2 |
| #2 列表导出串台 | Task 1 |
| #3 断开/`R` 误停 | Task 3 |
| #4 状态不可信 | Task 5 |
| #5 对齐假成功 | Task 4 |
| #6 Cookie/刷流黑屏 | Task 6 |
| #7 多路开录慢 | Task 7 |
| #8 共享进样绑死 | Task 8 |
| #9 未录制假完整 | Task 9 |
| #10 长按刷新核弹 | Task 3 |
| #11 画质静默降+分析门槛 | Task 10–11 |

无 TBD/占位步骤；导出优先级契约在 §2.1 已钉死。
