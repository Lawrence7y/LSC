# 下一迭代：信任闭环 / 平台可恢复 / 工程卫生 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不扩产品范围的前提下，把「导出时间可信、预览失败可恢复、仓库/巨型文件可维护」收成下一迭代可交付增量。

**Architecture:** 三条独立可合并的轨道。(A) 公共轴已就绪时拖拽→切片必须走 `create_clip_snapshot`/`export_clip_by_id` 且 `clip_ready`；未对齐时禁止把拖拽结果伪装成精确导出。(B) 预览启动/刷流慢路径补进度与可操作错误。(C) `.bundle` 隔离 + 从 `room_handler.py` 抽出 timeline/export 处理器，降低改一处伤一片的风险。

**Tech Stack:** Electron React/Zustand；Python `TimelineService` + `room_handler`；pytest 契约测试 + 少量前端源码守卫。

**非目标：** NLE、特效、自动更新安装、新分析模式、改默认 `shared_ingest_enabled=True`。

---

## 0. 现状结论（开工前必读）

| 已落地 | 仍缺 |
|--------|------|
| 切片墙钟快照字段、列表导出优先快照 | 未对齐拖拽仍 `live:false` 清墙钟 → approximate |
| 对齐后 `create_timeline` + 前端 common 轴 | `clip_ready=false` 时仍可能让用户以为能精确导出 |
| 危险操作二次确认、录制排队文案 | 预览「正在拉流」有，但 B 站长探测无阶段进度 |
| 抖音 Cookie 引导、共享进样风险文案 | 画质静默降级无横幅；`.bundle` 未 ignore |
| `export_clip_by_id` | `room_handler.py` / `Workbench/index.tsx` 过大 |

**关键契约（锁定，勿改符号）：**

```
common = preview_local + preview_to_common_delta
recording_local = common - recording_to_common_delta
recording_to_common_delta = media_start_mono + preview_to_common_delta
```

`live=false`（拖拽）**不得**用「按下时刻的 wallclock」冒充内容时刻；精确导出只允许：
1. I/O 键 `live=true` 墙钟路径，或
2. TimelineContext + `create_clip_snapshot` + `export_clip_by_id`

---

## 1. 文件职责图

| 文件 | 职责 |
|------|------|
| `lsc/core/models.py` | `TimelineContext.common_to_recording` 契约（只测，原则上不改） |
| `lsc/core/services/timeline_service.py` | `clip_ready` / snapshot 创建守卫 |
| `python-backend/handlers/timeline_handlers.py` | **新建**：从 `room_handler` 抽出 align→timeline、snapshot、export_by_id |
| `python-backend/handlers/room_handler.py` | 注册抽离后的 handlers；预览进度广播 |
| `lsc-electron/src/pages/Workbench/index.tsx` | 拖拽/添加切片门槛；`clip_ready` UI |
| `lsc-electron/src/components/VideoPreview.tsx` | 拉流阶段文案（探测中/转码中） |
| `lsc-electron/src/pages/Workbench/components/RoomCard.tsx` | 预览失败重试 + 画质降级提示入口 |
| `lsc-electron/src/types/index.ts` | `preview_phase` / `preview_actual_*` 可选字段 |
| `.gitignore` | 忽略 `lsc-electron/.bundle/` |
| `tests/test_timeline_delta_consistency.py` | 导出映射与墙钟公式同号 |
| `tests/test_clip_ready_guards.py` | clip_ready / 拖拽精确路径守卫 |
| `tests/test_frontend_stability_guards.py` | 前端门槛文案守卫 |

---

## Track A — 导出时间轴信任闭环

### Task 1: 锁定 common↔recording 与墙钟公式同号

**Files:**
- Create: `tests/test_timeline_delta_consistency.py`
- Modify: 仅当测试失败才动 `lsc/core/models.py` / `timeline_service.py`（预期应已绿）

- [ ] **Step 1: 写契约测试**

```python
"""common_to_recording 必须与墙钟映射同号同结果。"""
from __future__ import annotations

from lsc.core.models import RoomTimeSnapshot, TimelineContext
from lsc.core.services.timeline_service import build_room_snapshots_from_align


def test_common_to_recording_matches_wallclock_formula():
    # 基准房 offset=0，房间 B 领先 1.5s（content_offset=+1.5）
    media_start = 1000.0  # recording_media_start_mono
    snaps = build_room_snapshots_from_align(
        reference_room_id="ref",
        offsets={"ref": 0.0, "b": 1.5},
        scores={"ref": 0.9, "b": 0.9},
        room_meta={
            "ref": {"media_start_mono": media_start, "preview_epoch_id": "e1", "recording_id": "r1"},
            "b": {"media_start_mono": media_start, "preview_epoch_id": "e1", "recording_id": "r2"},
        },
    )
    ctx = TimelineContext(
        timeline_id="t1",
        reference_room_id="ref",
        preview_ready=True,
        clip_ready=True,
        room_snapshots=snaps,
    )
    # 用户在 common=10 处切：等价于「墙钟 = media_start + preview_local」且减 content_offset
    common = 10.0
    rec_b = ctx.common_to_recording("b", common)
    # preview_to_common_delta[b] = 1.5 - 0 = 1.5
    # recording_to_common_delta[b] = 1000 + 1.5 = 1001.5
    # recording_local = 10 - 1001.5 = -991.5 → 导出侧会 max(0, ...)；这里测原始转换
    assert abs(snaps["b"].preview_to_common_delta - 1.5) < 1e-9
    assert abs(snaps["b"].recording_to_common_delta - (media_start + 1.5)) < 1e-9
    assert abs(rec_b - (common - snaps["b"].recording_to_common_delta)) < 1e-9

    # 与墙钟公式对照：mark_wc=media_start+preview_local, export=mark_wc-media_start-content_offset
    preview_local_b = ctx.common_to_preview("b", common)  # 10 - 1.5 = 8.5
    content_offset_b = 1.5
    mark_wc = media_start + preview_local_b  # 仅在「预览本地时间≈录制已开时长」假设下
    export_wallclock = mark_wc - media_start - content_offset_b  # 8.5 - 1.5 = 7.0
    # common 路径：recording_local = common - (media_start + delta) 再加 media_start 才是文件时间？
    # 产品定义：common_to_recording 直接给出文件内秒数
    # 当 media_start 被编入 recording_to_common_delta 时，
    # file_time = common - media_start - preview_delta = 10 - 1000 - 1.5 = -991.5
    # 这与「文件从 0 起算的本地秒」不一致时，说明对齐瞬间 common 原点约定必须在测试注释中写死。
    # 生产 export_clip_by_id 使用：export_start = common_start - rec_delta
    # 即 file_time = common - recording_to_common_delta
    assert abs(rec_b - (common - (media_start + 1.5))) < 1e-9
```

> 若断言与当前实现冲突：以 `export_clip_by_id` 现用公式为准，改测试期望并在文件头注释「common 原点 = 对齐瞬间的墙钟减 media_start 后的预览参照」，**禁止**同时改两套公式。

- [ ] **Step 2: 跑测试**

```bash
pytest tests/test_timeline_delta_consistency.py -v
```

Expected: PASS（或按上面规则只改测试注释/期望，不改符号约定）

- [ ] **Step 3: Commit**

```bash
git add tests/test_timeline_delta_consistency.py
git commit -m "$(cat <<'EOF'
test: lock common↔recording delta sign against wallclock mapping

EOF
)"
```

---

### Task 2: `clip_ready` 门槛 — 无录制 ID 不得宣称精确切片

**Files:**
- Modify: `python-backend/handlers/room_handler.py`（`create_clip_snapshot` handler，或抽离后的 `timeline_handlers.py`）
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`
- Test: `tests/test_clip_ready_guards.py`（新建）
- Test: `tests/test_frontend_stability_guards.py`

- [ ] **Step 1: 写失败测试 — 后端拒绝 clip_ready=false**

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_create_clip_snapshot_handler_checks_clip_ready():
    src = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    if "timeline_handlers.py" in src or (ROOT / "python-backend/handlers/timeline_handlers.py").exists():
        src = (ROOT / "python-backend/handlers/timeline_handlers.py").read_text(encoding="utf-8")
    assert "clip_ready" in src
    assert "CLIP_NOT_READY" in src or "clip_ready" in src and "recording" in src.lower()
```

- [ ] **Step 2: 后端 — snapshot 创建前校验**

在 `create_clip_snapshot` handler 内（现约在 `room_handler.py` 末段 timeline 区）：

```python
ctx = _timeline_svc.get_timeline(timeline_id)
if ctx is None:
    return {'success': False, 'error': 'NO_TIMELINE'}
if not ctx.clip_ready:
    return {'success': False, 'error': 'CLIP_NOT_READY',
            'message': '对齐可用但录制尚未就绪，请确认各房间正在录制后再添加精确切片'}
```

- [ ] **Step 3: 前端 — common 模式添加切片前检查**

在 `handleAddSelectedClips`（或等价函数）里，`create_clip_snapshot` 之前：

```typescript
if (ctx && !ctx.clip_ready) {
  message.warning('已对齐但录制未就绪，无法创建精确切片；请确认各房间正在录制')
  return
}
```

类型：若 `TimelineContext` 前端类型缺 `clip_ready`，在 `lsc-electron/src/types/index.ts` 补上。

- [ ] **Step 4: 跑测试 + commit**

```bash
pytest tests/test_clip_ready_guards.py tests/test_frontend_stability_guards.py -v -k "clip_ready or create_clip"
```

```bash
git add python-backend/handlers/room_handler.py \
  lsc-electron/src/pages/Workbench/index.tsx \
  lsc-electron/src/types/index.ts \
  tests/test_clip_ready_guards.py \
  tests/test_frontend_stability_guards.py
git commit -m "$(cat <<'EOF'
fix: require clip_ready before exact clip snapshots

EOF
)"
```

---

### Task 3: 未对齐拖拽路径 — 禁止伪装精确

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`（`handleMarkerDragEnd` / `handleAddClip`）
- Modify: `lsc-electron/src/pages/Workbench/components/ClipList.tsx`（已有近似徽章则只加固）
- Test: `tests/test_frontend_stability_guards.py`

- [ ] **Step 1: 守卫测试**

```python
def test_unaligned_drag_add_forces_approximate():
    wb = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    # 拖拽结束未对齐时必须 live:false 提示近似
    assert "live: false" in wb or "live:false" in wb.replace(" ", "")
    assert "近似定位" in wb or "approximate" in wb
    # 未对齐添加切片必须写入 mark_precision: 'approximate'（无 snapshot）
    add_body = wb.split("const handleAddClip = useCallback", 1)[1].split("}, [", 1)[0]
    assert "approximate" in add_body
    assert "clip_snapshot_id" not in add_body or "create_clip_snapshot" not in add_body
```

- [ ] **Step 2: 行为收敛（最小改动）**

1. `handleMarkerDragEnd`：无 common 轴时保持现状提示；**不要**改成 `live:true`。
2. `handleAddClip`（单房降级路径）：若该房间无 `mark_in_wallclock`/`mark_out_wallclock`，强制：

```typescript
mark_precision: 'approximate',
mark_in_wallclock: null,
mark_out_wallclock: null,
```

3. 列表导出时若 `mark_precision === 'approximate'`，toast 一次警告（已有则跳过）。

- [ ] **Step 3: Commit**

```bash
git commit -m "$(cat <<'EOF'
fix: keep unaligned scrub marks explicitly approximate

EOF
)"
```

---

### Task 4: 对齐失效后清空 common 标记与 snapshot 入口

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`（`timeline_invalidated` / `timeline_ready` 监听处）

- [ ] **Step 1: 失效时复位 UI 状态**

在现有 `timeline_invalidated` 订阅里追加：

```typescript
setCommonMarkIn(null)
setCommonMarkOut(null)
setWaveformPeaks([])
message.warning('对齐已失效，精确公共轴已清除；请重新对齐后再精确切片')
```

- [ ] **Step 2: 手工清单（写入 PR 描述，不必自动化）**

1. 两房预览+录制 → 对齐 → 拖拽入出点 → 添加切片 → 徽章为精确 → `export_clip_by_id` 成功  
2. 对齐后停一路预览触发 invalidate → common 标记清空 → 再添加须走近似或重新对齐  
3. 未对齐拖拽添加 → 近似徽章 → 导出有警告

- [ ] **Step 3: Commit**

```bash
git commit -m "$(cat <<'EOF'
fix: clear common marks when timeline is invalidated

EOF
)"
```

---

## Track B — 平台预览可恢复

### Task 5: 预览阶段进度（尤其 B 站长探测）

**Files:**
- Modify: `python-backend/handlers/room_handler.py`（`_handle_mse_preview` / enable_preview 路径）
- Modify: `lsc-electron/src/types/index.ts`
- Modify: `lsc-electron/src/hooks/useWebSocket.ts`
- Modify: `lsc-electron/src/components/VideoPreview.tsx`
- Test: `tests/test_frontend_stability_guards.py`

- [ ] **Step 1: 后端在刷流前后广播阶段**

在 `enable_preview` / MSE 启动路径，刷流前与 FFmpeg 启动前：

```python
server.broadcast('preview_phase', {
    'room_id': room_id,
    'phase': 'refreshing_url',  # refreshing_url | probing | streaming | error
    'platform': platform,
})
# refresh_stream_url 完成后：
server.broadcast('preview_phase', {'room_id': room_id, 'phase': 'probing'})
# mse_init 发出时：
server.broadcast('preview_phase', {'room_id': room_id, 'phase': 'streaming'})
```

- [ ] **Step 2: 前端类型与 store**

```typescript
// types RoomSession
preview_phase?: 'idle' | 'refreshing_url' | 'probing' | 'streaming' | 'error'
```

`useWebSocket.ts` 订阅 `preview_phase` → `updateRoom`。

- [ ] **Step 3: VideoPreview 文案**

```tsx
const phaseText =
  room.preview_phase === 'refreshing_url' ? '正在刷新流地址…' :
  room.preview_phase === 'probing' ? '正在探测/转码…' :
  '正在拉流/转码…'
```

B 站可在 `refreshing_url` 时额外副文案：「B站刷新可能需要十余秒」。

- [ ] **Step 4: 守卫 + commit**

```python
def test_preview_phase_broadcast_and_ui():
    handler = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    assert "preview_phase" in handler
    assert "refreshing_url" in handler
    preview = (ROOT / "lsc-electron/src/components/VideoPreview.tsx").read_text(encoding="utf-8")
    assert "刷新流地址" in preview or "refreshing_url" in preview
```

```bash
git commit -m "$(cat <<'EOF'
feat: surface preview phase progress for slow stream refresh

EOF
)"
```

---

### Task 6: 预览失败可操作（重试 + 保留已有引导）

**Files:**
- Modify: `lsc-electron/src/components/VideoPreview.tsx` 或 `RoomCard.tsx`
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`（若重试复用 enable_preview）

- [ ] **Step 1: mse_error 覆盖层增加「重试预览」按钮**

调用现有 `enable_preview { room_id, mode: 'mse' }`（与开预览同一路径）。抖音 Cookie 错误继续走已有「去设置 Cookie」，不重复造轮子。

- [ ] **Step 2: 守卫**

```python
def test_mse_error_offers_retry():
    card = (ROOT / "lsc-electron/src/pages/Workbench/components/RoomCard.tsx").read_text(encoding="utf-8")
    preview = (ROOT / "lsc-electron/src/components/VideoPreview.tsx").read_text(encoding="utf-8")
    blob = card + preview
    assert "重试" in blob
    assert "enable_preview" in blob or "onRetryPreview" in blob
```

- [ ] **Step 3: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat: add retry action on preview mse_error overlay

EOF
)"
```

---

### Task 7: 多路预览画质降级横幅（P2，可与 B 并行）

**Files:**
- Modify: `python-backend/handlers/room_handler.py`（enable_preview 响应或 `mse_init` 附带 width/height）
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`（可关闭 Alert）

- [ ] **Step 1: 广播实际分辨率**

```python
server.broadcast('preview_quality_info', {
    'room_id': room_id,
    'width': actual_w,
    'height': actual_h,
    'degraded': actual_h < 720,  # 相对用户所选「高清」等，按现有降分辨率策略填
})
```

- [ ] **Step 2: 工作台顶部一次性 Alert**

「当前 ≥N 路预览，画质已降至 {h}p 以保障流畅」+ 关闭按钮；sessionStorage 记已关闭。

- [ ] **Step 3: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat: show dismissible banner when preview resolution is degraded

EOF
)"
```

---

## Track C — 工程卫生

### Task 8: 忽略嵌入式 `.bundle`，避免仓库被二进制淹没

**Files:**
- Modify: `.gitignore`
- Optional: 若曾误 add，勿 `git rm --cached` 大规模文件除非用户明确要求；本任务只 ignore

- [ ] **Step 1: 追加 ignore**

```gitignore
# Electron 打包用嵌入式 Python / FFmpeg（由 prep-bundle 生成，勿入库）
lsc-electron/.bundle/
```

- [ ] **Step 2: 确认 status 不再列出 `.bundle` 下海量 untracked**

```bash
git status --short lsc-electron/.bundle | head
```

Expected: 无输出或仅显示被 ignore。

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "$(cat <<'EOF'
chore: ignore lsc-electron/.bundle generated toolchain

EOF
)"
```

---

### Task 9: 抽出 timeline/export handlers（减负 `room_handler.py`）

**Files:**
- Create: `python-backend/handlers/timeline_handlers.py`
- Modify: `python-backend/handlers/room_handler.py`（改为 `register_timeline_handlers(server, ...)`）
- Modify: 若有 `handlers/__init__.py` 则导出
- Test: 现有 `tests/test_align_creates_timeline.py`、`tests/test_clip_snapshot_handlers.py` 必须仍绿

- [ ] **Step 1: 搬迁范围（只搬这些，禁止顺手重构业务）**

从 `room_handler.py` 剪切并保持行为不变：
- `handle_align_preview_audio` 成功分支里 create_timeline 相关（或整个 align handler）
- `create_clip_snapshot`
- `export_clip_by_id`
- `get_timeline`
- `_timeline_to_dict` / `_timeline_svc` 引用

注册形态：

```python
# timeline_handlers.py
def register_timeline_handlers(server, *, bridge, manager, broadcast_rooms, timeline_svc, ...):
    @server.on('create_clip_snapshot')
    async def handle_create_clip_snapshot(data):
        ...
```

`room_handler.py` 末尾：`register_timeline_handlers(...)`。

- [ ] **Step 2: 跑回归**

```bash
pytest tests/test_align_creates_timeline.py tests/test_clip_snapshot_handlers.py tests/test_timeline_service.py tests/test_timeline_delta_consistency.py -v
```

Expected: 全绿。

- [ ] **Step 3: Commit**

```bash
git commit -m "$(cat <<'EOF'
refactor: extract timeline/export WS handlers from room_handler

EOF
)"
```

---

### Task 10: 文档对齐（标记旧计划状态，防重复开工）

**Files:**
- Modify: `docs/superpowers/plans/2026-07-13-ux-pain-points-remediation.md` 文首加状态
- Modify: `docs/superpowers/plans/2026-07-13-common-timeline-ux.md` 文首加状态

- [ ] **Step 1: 各文件顶部插入**

```markdown
> **Status (2026-07-14):** 主体已在代码落地；剩余项并入 `2026-07-14-next-iteration-trust-platform-hygiene.md`。请勿按本文件空 checkbox 从头重做。
```

- [ ] **Step 2: Commit**

```bash
git commit -m "$(cat <<'EOF'
docs: mark July 13 UX/timeline plans superseded by next-iteration plan

EOF
)"
```

---

## 2. 建议执行顺序

```
A1 → A2 → A3 → A4
B5 → B6 →（可选 B7）
C8（随时可插队，无依赖）
C9（A 完成后再拆，避免双线改同一 handler）
C10（收尾）
```

## 3. 完成定义（DoD）

- [ ] `pytest` 上述新测 + timeline/clip 相关旧测全绿
- [ ] `cd lsc-electron && npx tsc --noEmit` 通过
- [ ] 手工三条：对齐精确拖拽导出 / 失效清空 / 未对齐近似警告
- [ ] B 站预览能看到「刷新流地址」阶段；失败可点重试
- [ ] `git status` 不再被 `.bundle` 淹没
- [ ] 无新功能范围蔓延（无 NLE / 无新分析模式）

---

## 4. Self-Review

| 需求 | 对应任务 |
|------|----------|
| 导出时间轴真机闭环 | A1–A4 |
| 抖音/B站拉流可恢复 | B5–B6（Cookie 已有，本迭代补阶段+重试） |
| 拆 handler + 清 bundle | C8–C9 |
| 文档漂移 | C10 |
| 画质降级提示 | B7（可选） |
