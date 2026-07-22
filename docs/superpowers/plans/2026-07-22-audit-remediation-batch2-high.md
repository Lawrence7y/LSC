# Audit Remediation Batch2 High Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地审查 High 项：配置/分析任务/持久化锁、稳定目录 hash、诚实处理超长回合、OCR 分辨率失败不静默错窗、广播终态不丢、断线写操作可感知、ClipList 稳定 id、CPU 回退导出与 OCR 可取消。

**Architecture:** 小步加锁与纯函数超时/丢弃策略；前端以 `clip_id` 为唯一操作键；导出 CPU 回退改为与主导出同构的 Popen+cancel。

**Tech Stack:** Python threading/asyncio、React/TS、pytest

**Spec:** `docs/superpowers/specs/2026-07-22-audit-remediation-batch2-high-design.md`  
**前置:** Batch1 Critical 已合入

**执行约束：** 工作目录 `D:\Project\直播切片多人`；**不要 git commit**（除非用户要求）；不拆 `room_handler.py`。

---

## 文件结构

| 文件 | Task |
|------|------|
| `lsc/config.py` | H1a |
| `python-backend/handlers/room_handler.py` | H1b |
| `python-backend/persistence.py` | H1c |
| `lsc/core/services/recording_service.py` | H2a |
| `lsc/analyzer/round_detector.py` | H2b |
| `lsc/analyzer/ocr_detector.py` | H2c, H4b |
| `python-backend/message_bridge.py` | H3a |
| `lsc-electron/src/services/websocket.ts` | H3b |
| `lsc-electron/src/pages/Workbench/components/ClipList.tsx` + `Workbench/index.tsx` | H3c |
| `lsc/exporter/clip.py` | H4a |
| `tests/test_audit_batch2_*.py` | 各 Task |

---

### Task 1: H1a — `load_config` 使用 `_config_lock`

**Files:** `lsc/config.py`；`tests/test_config_lock.py`

- [ ] **Step 1: 测试** — 两线程并发 `load_config(force_reload=True)` 不抛、最终单例一致。

```python
import threading
from lsc.config import load_config, reset_config

def test_load_config_concurrent():
    reset_config()
    errs = []
    def worker():
        try:
            for _ in range(20):
                load_config(force_reload=True)
        except Exception as e:
            errs.append(e)
    ts = [threading.Thread(target=worker) for _ in range(8)]
    for t in ts: t.start()
    for t in ts: t.join()
    assert not errs
    assert load_config() is load_config()
```

- [ ] **Step 2: 实现** — `load_config` / `reload_config` / `reset_config` 全程持 `_config_lock`：

```python
def load_config(force_reload: bool = False) -> LscConfig:
    global _config_instance
    with _config_lock:
        if force_reload or _config_instance is None:
            # existing body that assigns _config_instance
            ...
        return _config_instance
```

- [ ] **Step 3:** `pytest tests/test_config_lock.py -v` → PASS

---

### Task 2: H1b — `_analysis_jobs` / `_continuous_tasks` 加锁

**Files:** `python-backend/handlers/room_handler.py`；`tests/test_analysis_jobs_lock_guards.py`

- [ ] **Step 1: 守卫** — 源码断言存在 `_analysis_jobs_lock = threading.RLock()`（或等价名），且对 `_analysis_jobs` / `_continuous_tasks` 的批量 `items()`/`list(...)` 迭代在 `with _analysis_jobs_lock` 内或先 `list(...items())` 快照。

- [ ] **Step 2: 实现**
  - 模块级：`_analysis_jobs_lock = threading.RLock()`
  - 所有写：`with _analysis_jobs_lock: _analysis_jobs[rid] = ...`
  - 广播前：`with _analysis_jobs_lock: snapshot = dict(_analysis_jobs.get(rid) or {})`
  - 禁止在锁内 `await` / 跑 FFmpeg

- [ ] **Step 3:** 守卫测试 PASS

---

### Task 3: H1c — persistence 进程内锁

**Files:** `python-backend/persistence.py`

- [ ] **Step 1:** 模块级 `_persist_lock = threading.Lock()`
- [ ] **Step 2:** `save_rooms` / `save_settings` / `save_analysis_results` 的 read-modify-write 包在 `with _persist_lock:`
- [ ] **Step 3:** 可选线程压测：并发 `save_settings` 最终文件为合法 JSON

---

### Task 4: H2a — 稳定目录 hash

**Files:** `lsc/core/services/recording_service.py:653`；`tests/test_recording_dir_hash.py`

- [ ] **Step 1: 测试**

```python
import hashlib
url = "https://live.example/room/1"
expected = hashlib.sha1(url.encode("utf-8")).hexdigest()[:6]
# 调用提取函数或直接断言实现行使用 sha1
```

- [ ] **Step 2: 替换**

```python
import hashlib
short_id = hashlib.sha1(room.room_url.encode("utf-8")).hexdigest()[:6]
```

不迁移旧目录。

- [ ] **Step 3:** pytest PASS

---

### Task 5: H2b — 超长回合诚实降级（禁止假能量谷）

**Files:** `lsc/analyzer/round_detector.py:897-904`；相关单测

- [ ] **Step 1:** 改逻辑：超长段 **不** 中点对切；打 WARNING；保留整段或按项目已有上限策略丢弃/截断（与周围过滤一致，优先「保留并告警」）：

```python
if combat_len > cfg.max_combat_duration:
    _log.warning(
        "超长回合未分割（取消假中点切）: [%d-%d] len=%d > max=%d",
        seg_start, seg_end, combat_len, cfg.max_combat_duration,
    )
    validated.append((seg_start, seg_end))
    continue
```

- [ ] **Step 2:** 更新/删除依赖「必对切成两段」的旧测试；新增断言不产生 mid 硬切。

---

### Task 6: H2c — OCR 分辨率探测失败不回退 1080p

**Files:** `lsc/analyzer/ocr_detector.py:_get_video_resolution`；调用方

- [ ] **Step 1:** 返回类型改为 `tuple[int, int] | None`；失败返回 `None`。
- [ ] **Step 2:** 调用处若 `None`：跳过 OCR / 返回空事件并 WARNING `ocr_unavailable`，禁止用 `(1920, 1080)`。
- [ ] **Step 3:** 单测 mock 探测失败 → 不调用基于错窗的裁剪。

---

### Task 7: H3a — 广播终态不丢

**Files:** `python-backend/message_bridge.py`；`tests/test_message_bridge_terminal.py`

- [ ] **Step 1: 白名单**

```python
_TERMINAL_TYPES = frozenset({
    "clip_completed", "clip_failed",
    "recording_stopped", "recording_started",
    "reconnect_failed", "continuous_highlights",
})
_DROPPABLE_TYPES = frozenset({
    "rooms_updated", "mse_segment", "export_progress", "analysis_progress",
    "continuous_analysis_status", "system_stats",
})
```

- [ ] **Step 2: `queue_broadcast`**  
  队列满时：循环 `get_nowait` 丢弃 `_DROPPABLE_TYPES`；若队头是终态则跳过并找下一个可丢（可用临时 list 重排：先取出全部，丢掉 droppable，再 put 回，最后 put 新终态）。若仍满：`_log.error` + 临时 `maxsize` 扩容一档（或 `put` 带短 timeout），**禁止丢终态**。

- [ ] **Step 3: 单测** — 填满队列全是 `mse_segment`，再 `queue_broadcast(clip_completed)` → 队列中仍含该终态。

---

### Task 8: H3b — 断线写操作可感知

**Files:** `lsc-electron/src/services/websocket.ts`；调用方可选 toast

**现状：** `shouldQueueWhenDisconnected` 仅允许少数读类型；写类型已 `console.warn` 后 return，调用方无 Promise 失败。

- [ ] **Step 1:** `send` 改为返回 `boolean`（或新增 `sendOrThrow`）：断线且不可入队时返回 `false`。
- [ ] **Step 2:** 对写类型集合（`start_recording`/`stop_recording`/`export_clip`/`save_settings`/`start_analysis`/`start_analysis_export`/`start_continuous_analysis`/`confirm_highlight_clip` 等）在 Workbench/`useWebSocket` 封装层：若 `false` → `message.warning('未连接后端，操作未发送')`。
- [ ] **Step 3:** 单测/守卫：`shouldQueueWhenDisconnected('start_recording') === false` 且源码含用户可见提示字符串。

---

### Task 9: H3c — ClipList 稳定 `clip_id`

**Files:** `ClipList.tsx`；`Workbench/index.tsx`；`tests/test_frontend_stability_guards.py`

- [ ] **Step 1:** Props 改为按 id：

```typescript
onDelete: (clipId: string) => void
onExport: (clip: ClipSegment) => void
// selection: Set<string> of clip_id
```

- [ ] **Step 2:** `renderItem` 的 Ant List `key` 使用 `clip.clip_id`（或 `clip_id ?? round_key`），禁止 `index`。
- [ ] **Step 3:** Workbench 删除/导出用 id 查找：`clips.find(c => c.clip_id === id)`。
- [ ] **Step 4:** 跑 `pytest tests/test_frontend_stability_guards.py -v`，**修红至全绿**（按失败断言更新实现或过时断言——优先改实现满足「稳定 id」意图）。

---

### Task 10: H4a — CPU 回退可取消

**Files:** `lsc/exporter/clip.py:_export_cpu_fallback`

- [ ] **Step 1:** 去掉 `subprocess.run(..., timeout=300)`。
- [ ] **Step 2:** 使用 `Popen` + `proc.wait` 与 `on_process(proc)`；可选复用 `compute_export_watchdog_timeout`；支持调用方 cancel（杀 proc）。
- [ ] **Step 3:** 单测 mock Popen：断言调用了 `on_process`，且不用 `subprocess.run`。

---

### Task 11: H4b — OCR 循环 `cancel_check`

**Files:** `lsc/analyzer/ocr_detector.py` 主循环（~375+ 及已有 203 行附近）

- [ ] **Step 1:** 确认所有逐帧/逐窗循环调用 `if cancel_check and cancel_check(): return ...`。
- [ ] **Step 2:** 帧差预筛为**可选**：默认关或极保守；本 Task 最低交付是 cancel 响应。
- [ ] **Step 3:** 单测：`cancel_check` 第二次调用返回 True → 函数提前结束。

---

### Task 12: Batch2 回归

Run:

```text
pytest tests/test_config_lock.py tests/test_analysis_jobs_lock_guards.py tests/test_recording_dir_hash.py tests/test_message_bridge_terminal.py tests/test_frontend_stability_guards.py -v
pytest tests/test_valorant_round_fsm.py tests/test_round_end_precision_runtime.py -q
```

Expected: 相关全 PASS；frontend guards 0 failed。

---

## Spec 覆盖

| 项 | Task |
|----|------|
| H1a/b/c | 1–3 |
| H2a/b/c | 4–6 |
| H3a/b/c | 7–9 |
| H4a/b | 10–11 |
| 回归 | 12 |
