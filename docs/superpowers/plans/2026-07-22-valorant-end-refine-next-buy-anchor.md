# Valorant 终点密扫收口（next_buy 锚）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Do **not** commit unless the user asks.

**Goal:** 改 hybrid 终点 refine：向后锚到 next buy/non_game，取最后稳定 result 并校验过渡；不合格回退 coarse_end。不重训。

**Architecture:** 在 `round_detector.py` 增加纯函数序列收口助手，改写 `_refine_hybrid_end`；调用方在 refine 失败时保持 coarse 且不强确认。用合成序列单测 + 确认真值复评 v2。

**Tech Stack:** Python 3、现有 dense classify、pytest、`eval_blind.py`。

**Spec:** `docs/superpowers/specs/2026-07-22-valorant-end-refine-next-buy-anchor-design.md`

---

## 文件

- Modify: `lsc/analyzer/round_detector.py`
- Test: `tests/test_end_refine_next_buy_anchor.py`
- Verify: `tests/test_round_end_precision_runtime.py`、hybrid 相关测试、确认真值 `eval_blind`

### Task 1: 序列级收口纯函数

**Files:**
- Modify: `lsc/analyzer/round_detector.py`
- Test: `tests/test_end_refine_next_buy_anchor.py`

- [ ] **Step 1: 失败测试**

```python
from lsc.analyzer.round_detector import refine_end_with_next_buy_anchor


def test_picks_last_result_before_buy_not_early_flash() -> None:
    seq = [
        (10.0, "combat"),
        (11.0, "result"),
        (11.1, "result"),
        (11.2, "result"),  # early flash
        (12.0, "combat"),
        (20.0, "result"),
        (20.1, "result"),
        (20.2, "result"),
        (21.0, "buy"),
    ]
    end_ts, ok = refine_end_with_next_buy_anchor(
        seq, coarse_end=11.0, round_start=0.0, lookahead_sec=45.0, lookback_sec=8.0, min_stable=3,
    )
    assert ok is True
    assert 20.0 <= end_ts <= 22.5


def test_rejects_result_without_following_transition() -> None:
    seq = [(10.0, "combat"), (11.0, "result"), (11.1, "result"), (11.2, "result"), (12.0, "combat")]
    end_ts, ok = refine_end_with_next_buy_anchor(
        seq, coarse_end=11.0, round_start=0.0, lookahead_sec=45.0, lookback_sec=8.0, min_stable=3,
    )
    assert ok is False
```

- [ ] **Step 2: 运行 FAIL**

- [ ] **Step 3: 实现 `refine_end_with_next_buy_anchor`**

返回 `(end_timestamp, ok: bool)`。内部：找 anchor → 窗口内最后稳定 result → 过渡校验 → `compute_clip_end`。失败返回 `(coarse_end, False)`。

另实现 `find_last_stable_label_run(seq, target, min_stable) -> float | None`。

- [ ] **Step 4: 测试 PASS**

### Task 2: 接入 `_refine_hybrid_end`

**Files:**
- Modify: `lsc/analyzer/round_detector.py`
- Test: `tests/test_end_refine_next_buy_anchor.py`（可 mock sequence）

- [ ] **Step 1: 改 `_refine_hybrid_end`**

- 增加参数 `round_start: float`、可选 `lookahead_sec`/`lookback_sec`（默认常量）。
- 密扫范围改为 `[max(round_start, center_ts - lookback), center_ts + lookahead]`（center 仍为 result_center/coarse 侧）。
- 用 `refine_end_with_next_buy_anchor` 替代「第一段 result」。
- 返回：`ok=False` 时返回 `(None, {})` 或 `(coarse_center, {})` —— **约定**：返回 `end_ts=None` 表示失败，调用方用 coarse_end。

- [ ] **Step 2: 更新 `detect_valorant_rounds_hybrid` 调用**

传入 `round_start=coarse_start`；若 `refined_end is None`：`final_end = coarse_end`，且构造 `end_strong=False`（空 probs 已导致弱置信）。

- [ ] **Step 3: 跑**

```powershell
python -m pytest tests/test_end_refine_next_buy_anchor.py tests/test_round_end_precision_runtime.py tests/test_valorant_hybrid_detect.py -q
```

Expected: PASS。

### Task 3: 确认真值复评 v2

- [ ] **Step 1: 复评**

```powershell
$env:PYTHONPATH="$PWD"
python scripts/valorant_vision/eval_blind.py `
  --round-manifest "$env:USERPROFILE\LSC\datasets\valorant_phase_boundary_20260722_v2\round_manifest.json" `
  --model-dir "$env:USERPROFILE\LSC\models\valorant_phase_boundary_20260722_v2" `
  --output "$env:USERPROFILE\LSC\datasets\valorant_phase_boundary_20260722_v2\reports\candidate_v2_endrefine_rounds.json"
```

- [ ] **Step 2: 对照写 delivery 摘要**

对比 `candidate_v2_rounds.json` / `candidate_v2_runtime_rounds.json` / 新报告的 `end_err_p95`、`recall`、`listed_precision`。不替换生产模型。

- [ ] **Step 3: 回归**

```powershell
python -m pytest tests/test_end_refine_next_buy_anchor.py tests/test_round_end_precision_runtime.py tests/test_round_boundary_optimization.py tests/test_valorant_hybrid_detect.py tests/test_valorant_round_fsm.py -q
```

## 自检

- Spec 算法与 Task 1–2 一一对应；无重训步骤；失败回退路径明确。
