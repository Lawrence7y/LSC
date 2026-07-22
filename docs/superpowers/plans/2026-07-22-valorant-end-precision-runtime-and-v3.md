# Valorant 终点精度：运行时 + v3 Hard Retrain 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Do **not** create git commits unless the user explicitly asks.

**Goal:** 通过 `result_high_prob` + listed 相邻合并压误检与终点误差，再按剩余 hard 重训候选 v3，在确认真值与 blind/test 上门禁复评。

**Architecture:** 在 `round_detector` 增加可配置的 result 结束强阈值与纯函数 listed 合并；不改 `grade_round_confirmation` 导出语义。复用 hard 抽帧/合并/训练管线产出独立 v3 目录；未过门禁不覆盖生产模型。

**Tech Stack:** Python 3、现有 hybrid 检测、pytest、`eval_blind.py`、`train_export.py`。

**Spec:** `docs/superpowers/specs/2026-07-22-valorant-end-precision-runtime-and-v3-design.md`

---

## 文件与职责

- Modify: `lsc/analyzer/round_detector.py` — `result_high_prob` 判 `end_strong`；listed 合并后处理。
- Test: `tests/test_round_end_precision_runtime.py` — 阈值与合并单测。
- Reuse: `scripts/valorant_vision/build_hard_miss_queue.py`、`merge_round_boundary_labels.py`、`train_export.py`、`eval_blind.py`。
- Verify: 既有 `test_round_boundary_optimization.py` 弱终点回归 + FSM/hybrid。

### Task 1: `result_high_prob` 结束强置信

**Files:**
- Modify: `lsc/analyzer/round_detector.py`
- Test: `tests/test_round_end_precision_runtime.py`

- [ ] **Step 1: 写失败测试**

```python
from lsc.analyzer.round_detector import (
    grade_round_confirmation,
    _is_strong_confidence,
    _confidence_from_probs,
)


def test_result_end_uses_result_high_prob_threshold() -> None:
    thresholds = {"stable_prob": 0.55, "high_prob": 0.8, "result_high_prob": 0.88}
    # end confidence ~0.85 should be strong under high_prob but weak under result_high_prob
    probs = {"result": 0.85, "combat": 0.10, "buy": 0.02, "non_game": 0.02, "replay": 0.01}
    conf = _confidence_from_probs(probs, "result", thresholds=thresholds)
    assert conf >= 0.8
    assert _is_strong_confidence(conf, thresholds) is True  # global high_prob
    assert _is_strong_confidence(conf, thresholds, label="result") is False


def test_weak_result_end_stays_pending_with_score_hint() -> None:
    assert grade_round_confirmation(
        start_strong=True,
        end_strong=False,
        score_confirm=True,
    ) == "pending"
```

（实现 `_is_strong_confidence` 时增加可选 `label`；`label=="result"` 时读 `result_high_prob`。）

- [ ] **Step 2: 运行确认 FAIL**

Run: `python -m pytest tests/test_round_end_precision_runtime.py::test_result_end_uses_result_high_prob_threshold -q`

- [ ] **Step 3: 最小实现**

- 扩展 `_is_strong_confidence(confidence, thresholds, label=None)`。
- hybrid 闭合处：`end_by == "model_result"` 时 `_is_strong_confidence(..., label="result")`。
- 候选/生产模型 JSON 可暂不写 `result_high_prob`（回退 0.8）；评估 v2+runtime 时在加载后注入或写 sidecar meta 副本到候选目录。

- [ ] **Step 4: 测试 PASS**

### Task 2: listed 相邻合并

**Files:**
- Modify: `lsc/analyzer/round_detector.py`（或小模块 `lsc/analyzer/round_list_postprocess.py` 若文件过大）
- Test: `tests/test_round_end_precision_runtime.py`

- [ ] **Step 1: 写失败测试**

```python
from lsc.analyzer.round_detector import merge_listed_rounds


def test_merge_listed_rounds_merges_overlap_and_near_gap() -> None:
    rows = [
        {"start": 10.0, "end": 20.0, "confirm_status": "pending", "listed": True},
        {"start": 18.0, "end": 30.0, "confirm_status": "vision_confirmed", "listed": True},
        {"start": 100.0, "end": 110.0, "confirm_status": "pending", "listed": True},
    ]
    merged = merge_listed_rounds(rows, iou_threshold=0.5, max_gap_sec=3.0)
    assert len(merged) == 2
    assert merged[0]["start"] == 10.0 and merged[0]["end"] == 30.0
    assert merged[0]["confirm_status"] == "vision_confirmed"
```

- [ ] **Step 2–4: 红/绿实现 `merge_listed_rounds`，并在 hybrid 返回 listed 列表前调用。**

IoU 定义：intersection/union on [start,end]。间隙：`next.start - prev.end`；若 `< max_gap` 且区间相交或嵌套则合并。稳定按 start 排序后一次扫描合并。

### Task 3: 运行时补丁复评（确认真值）

**Files:** Verify scripts only

- [ ] **Step 1: 为候选 v2 目录写入带 `result_high_prob: 0.88` 的 JSON 副本（不改生产 models）。**

- [ ] **Step 2: 复评**

```powershell
$env:PYTHONPATH="$PWD"
python scripts/valorant_vision/eval_blind.py `
  --round-manifest "$env:USERPROFILE\LSC\datasets\valorant_phase_boundary_20260722_v2\round_manifest.json" `
  --model-dir "$env:USERPROFILE\LSC\models\valorant_phase_boundary_20260722_v2" `
  --output "$env:USERPROFILE\LSC\datasets\valorant_phase_boundary_20260722_v2\reports\candidate_v2_runtime_rounds.json"
```

Expected: 相对无补丁 v2，listed precision 上升或 listed_count 下降；记录召回是否仍 ≥0.90。保存漏检/终点误差 sidecar（若报告缺明细，用 detector 输出 + confirmed GT 推导，同既有 `extract_misses_from_report`）。

### Task 4: Hard 抽帧 + 注入 + 训练 v3

**Files:**
- Reuse: `build_hard_miss_queue.py`、`train_export.py`

- [ ] **Step 1: 用 Task 3 sidecar 生成 hard 队列**

```powershell
python scripts/valorant_vision/build_hard_miss_queue.py `
  --video "<recording.mp4>" `
  --confirmed-rounds "$env:USERPROFILE\LSC\datasets\valorant_phase\annotate\security_nick_20260722\rounds_confirmed.json" `
  --eval-report "<sidecar_or_report_with_misses>" `
  --out-dir "$env:USERPROFILE\LSC\datasets\valorant_phase\annotate\security_nick_20260722_hard_v3" `
  --radius-sec 8 --fps 2
```

- [ ] **Step 2: 时间对齐/高置信自动标 hard；人工只补缺口（若 unlabeled>0）。**

- [ ] **Step 3: 从 v2 数据复制为 v3 基座，注入 hard（result/buy 过采样），训练：**

```powershell
python scripts/valorant_vision/train_export.py `
  --data-dir "$env:USERPROFILE\LSC\datasets\valorant_phase_boundary_20260722_v3" `
  --out-dir "$env:USERPROFILE\LSC\models\valorant_phase_boundary_20260722_v3" `
  --epochs 10 --seed 20260722
```

在 v3 模型 JSON 写入 `result_high_prob: 0.88`。

### Task 5: v3 门禁与交付

- [ ] **Step 1: 评估 v3（含运行时）**

```powershell
python scripts/valorant_vision/eval_blind.py `
  --round-manifest "$env:USERPROFILE\LSC\datasets\valorant_phase_boundary_20260722_v2\round_manifest.json" `
  --model-dir "$env:USERPROFILE\LSC\models\valorant_phase_boundary_20260722_v3" `
  --output "$env:USERPROFILE\LSC\datasets\valorant_phase_boundary_20260722_v3\reports\candidate_v3_rounds.json"
python scripts/valorant_vision/eval_codex_broadcast.py `
  --annotation-dir "$env:USERPROFILE\LSC\datasets\valorant_phase\annotate\security_nick_20260722" `
  --model-dir "$env:USERPROFILE\LSC\models\valorant_phase_boundary_20260722_v3" `
  --split all `
  --output "$env:USERPROFILE\LSC\datasets\valorant_phase_boundary_20260722_v3\reports\candidate_v3_classification.json"
```

- [ ] **Step 2: 原 blind/test 同版本复测（仓库既有命令）。**

- [ ] **Step 3: 写 `delivery_summary.json`；`release_approved` 仅当召回≥0.90、起止 P95≤0.8、listed precision≥0.97。失败不替换生产。**

- [ ] **Step 4: 回归**

```powershell
python -m pytest tests/test_round_end_precision_runtime.py tests/test_round_boundary_optimization.py tests/test_round_gt_confirm_and_hard_retrain.py tests/test_valorant_round_fsm.py tests/test_continuous_analysis_guards.py tests/test_valorant_hybrid_detect.py -q
```

Expected: 全部通过。

## 计划自检

- Spec 阶段 A → Task 1–3；阶段 B → Task 4–5。
- 无占位步骤；`result_high_prob` / `merge_listed_rounds` 命名前后一致。
- 生产模型与导出门禁不被本计划直接修改覆盖。
