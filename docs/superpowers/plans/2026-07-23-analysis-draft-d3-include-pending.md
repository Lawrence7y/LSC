# D3 — `include_pending` 后端策略

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 自动草稿可携带 pending/refining 切片；默认路径仍拒绝 pending，不破坏既有测试。

**Architecture:** `clip_allowed_for_draft` / `clip_source_usable` 增加 `include_pending: bool = False`；`generate_jianying_draft` WS 透传该字段；builder 组装切片轨时使用同一参数。

**Tech Stack:** Python 3.12、pytest、现有 `jianying_handlers` / `jianying_draft`

**Spec:** `docs/superpowers/specs/2026-07-23-analysis-auto-jianying-draft-design.md` §6  
**前置:** D1（建议）；可与 D1 并行  
**后继:** D2

**执行约束：** 工作目录 `D:\Project\直播切片多人`；**不要 git commit**（除非用户要求）。

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `lsc/exporter/jianying_draft.py` | `include_pending` 参数 |
| `python-backend/handlers/jianying_handlers.py` | 读 `data.include_pending`，过滤时传入 |
| `tests/test_jianying_draft.py` | 单测扩展 |
| `tests/test_jianying_ws_guards.py` | WS/过滤守卫 |

---

### Task 1: 扩展 `clip_source_usable` / `clip_allowed_for_draft`

**Files:**
- Modify: `lsc/exporter/jianying_draft.py`
- Modify: `tests/test_jianying_draft.py`

- [ ] **Step 1: 写失败测试**

```python
def test_clip_allowed_for_draft_include_pending():
    pending = {"confirm_status": "pending"}
    refining = {"confirm_status": "refining"}
    assert clip_allowed_for_draft(pending) is False
    assert clip_allowed_for_draft(pending, include_pending=True) is True
    assert clip_allowed_for_draft(refining, include_pending=True) is True
    assert clip_allowed_for_draft(
        {"confirm_status": "pending", "mark_precision": "approximate"},
        include_pending=True,
    ) is False


def test_clip_source_usable_include_pending():
    assert clip_source_usable(precision="exact", confirm_status="pending") is False
    assert clip_source_usable(
        precision="exact", confirm_status="pending", include_pending=True,
    ) is True
```

- [ ] **Step 2: 跑测确认失败**

```bash
python -m pytest tests/test_jianying_draft.py::test_clip_allowed_for_draft_include_pending tests/test_jianying_draft.py::test_clip_source_usable_include_pending -v --tb=short
```

Expected: FAIL（参数不存在）

- [ ] **Step 3: 最小实现**

```python
def clip_source_usable(
    *,
    precision: str,
    confirm_status: str | None,
    include_pending: bool = False,
) -> bool:
    if precision == "approximate":
        return False
    if confirm_status in ("pending", "refining") and not include_pending:
        return False
    return True


def clip_allowed_for_draft(clip: dict, *, include_pending: bool = False) -> bool:
    status = clip.get("confirm_status")
    if status in ("pending", "refining") and not include_pending:
        return False
    if clip.get("mark_precision") == "approximate":
        return False
    return True
```

更新 `build_session_draft`（或组装循环）里调用 `clip_source_usable(..., include_pending=options.include_pending)` —— 若 options 上无字段，给 `JianyingDraftOptions` 增加 `include_pending: bool = False`（`lsc/core/models.py`）。

- [ ] **Step 4: 跑旧测 + 新测**

```bash
python -m pytest tests/test_jianying_draft.py -v --tb=short
```

Expected: PASS

---

### Task 2: WS 透传 `include_pending`

**Files:**
- Modify: `python-backend/handlers/jianying_handlers.py`
- Modify: `tests/test_jianying_ws_guards.py`

- [ ] **Step 5: 守卫测试**

在 `tests/test_jianying_ws_guards.py` 增加：

```python
def test_handler_reads_include_pending_flag() -> None:
    src = Path("python-backend/handlers/jianying_handlers.py").read_text(encoding="utf-8")
    assert "include_pending" in src
```

并保留：默认 `clip_allowed_for_draft({"confirm_status": "pending"}) is False`。

- [ ] **Step 6: 实现**

在 `_build_*` / `handle_generate_jianying_draft` 路径：

```python
include_pending = bool(data.get("include_pending", False))
...
if not clip_allowed_for_draft(c, include_pending=include_pending):
    ...
```

Options 构造时写入 `include_pending=include_pending`。

- [ ] **Step 7: 跑测**

```bash
python -m pytest tests/test_jianying_ws_guards.py tests/test_jianying_draft.py -v --tb=short
```

Expected: PASS

---

## Done 标准

- [ ] 默认行为与旧测一致（pending 拒绝）
- [ ] `include_pending=True` 允许 pending/refining，仍拒绝 approximate
- [ ] WS 可接收该字段
