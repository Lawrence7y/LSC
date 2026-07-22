# Valorant 回合真值确认与 Hard Retrain 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Do **not** create git commits unless the user explicitly asks.

**Goal:** 提供 `/rounds` 人工确认回合真值，并基于漏检/弱终点 hard samples 重训候选 v2，在确认真值与原 blind/test 上门禁复评。

**Architecture:** 扩展现有 `serve_label_ui.py`（同 `--root`）增加回合确认页与 API，写出 `rounds_confirmed.json`；复用 `merge_round_boundary_labels` 的 confirmed 优先逻辑；新增 hard 抽帧脚本与对照复评流程；训练产物写入独立 `*_v2` 目录，门禁未过不覆盖生产 ONNX。

**Tech Stack:** Python 3、现有标注 HTTP UI、OpenCV、PyTorch/ONNX、pytest、`eval_blind.py` / `eval_gates.py`。

**Spec:** `docs/superpowers/specs/2026-07-22-valorant-round-gt-confirm-and-hard-retrain-design.md`

---

## 文件与职责

- Modify: `scripts/valorant_vision/serve_label_ui.py` — `/rounds` 页面、回合 CRUD API、最近帧预览。
- Create: `scripts/valorant_vision/round_gt.py` — 回合校验、最近帧查找、草稿/确认读写（供 UI 与测试复用）。
- Create: `scripts/valorant_vision/build_hard_miss_queue.py` — 根据复评漏检/终点误差窗口抽 hard 帧队列。
- Modify: `scripts/valorant_vision/merge_round_boundary_labels.py` — 仅在缺测时补强；已支持 `rounds_confirmed.json` 则只加回归测试。
- Test: `tests/test_round_gt_confirm_and_hard_retrain.py` — UI 辅助逻辑、校验、最近帧、hard 窗口抽样。
- Verify: `scripts/valorant_vision/eval_blind.py`、`train_export.py`、既有 FSM/hybrid 回归。

### Task 1: 回合校验与最近帧辅助模块

**Files:**
- Create: `scripts/valorant_vision/round_gt.py`
- Test: `tests/test_round_gt_confirm_and_hard_retrain.py`

- [ ] **Step 1: 写失败测试**

```python
from scripts.valorant_vision.round_gt import (
    find_nearest_frame,
    validate_confirmed_rounds,
)


def test_validate_confirmed_rounds_rejects_overlap_and_bad_bounds() -> None:
    rows = [
        {"start": 10.0, "end": 20.0, "end_reason": "result", "round_key": "R1"},
        {"start": 19.0, "end": 30.0, "end_reason": "next_buy", "round_key": "R2"},
    ]
    try:
        validate_confirmed_rounds(rows, duration=60.0)
    except ValueError as exc:
        assert "overlap" in str(exc).lower() or "重叠" in str(exc)
    else:
        raise AssertionError("overlapping rounds accepted")


def test_find_nearest_frame_picks_closest_timestamp() -> None:
    frames = [
        {"timestamp_sec": 1.0, "rel_path": "a.jpg"},
        {"timestamp_sec": 2.0, "rel_path": "b.jpg"},
        {"timestamp_sec": 5.0, "rel_path": "c.jpg"},
    ]
    hit = find_nearest_frame(frames, 2.2)
    assert hit["rel_path"] == "b.jpg"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_round_gt_confirm_and_hard_retrain.py::test_validate_confirmed_rounds_rejects_overlap_and_bad_bounds tests/test_round_gt_confirm_and_hard_retrain.py::test_find_nearest_frame_picks_closest_timestamp -q`

Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现最小模块**

```python
# scripts/valorant_vision/round_gt.py
from __future__ import annotations

import math
from typing import Any

VALID_END_REASONS = frozenset({"result", "next_buy", "score", "unknown"})


def validate_confirmed_rounds(rows: list[dict[str, Any]], *, duration: float) -> None:
    if not math.isfinite(duration) or duration < 0:
        raise ValueError("duration must be a finite non-negative number")
    ordered = sorted(rows, key=lambda r: float(r["start"]))
    previous_end = -1.0
    seen_keys: set[str] = set()
    for index, row in enumerate(ordered, start=1):
        start = float(row["start"])
        end = float(row["end"])
        reason = str(row.get("end_reason") or "")
        key = str(row.get("round_key") or f"R{index}")
        if reason not in VALID_END_REASONS:
            raise ValueError(f"invalid end_reason: {reason}")
        if key in seen_keys:
            raise ValueError(f"duplicate round_key: {key}")
        seen_keys.add(key)
        if not (0.0 <= start < end <= duration):
            raise ValueError(f"invalid bounds: {start}-{end}")
        if start < previous_end:
            raise ValueError(f"overlapping rounds near {start}")
        previous_end = end


def find_nearest_frame(frames: list[dict[str, Any]], timestamp_sec: float) -> dict[str, Any]:
    if not frames:
        raise ValueError("no frames available for preview")
    return min(frames, key=lambda f: abs(float(f["timestamp_sec"]) - float(timestamp_sec)))
```

另提供 `load_draft_rounds(root: Path)` / `save_confirmed_rounds(root: Path, payload: dict)`：读 `round_manifest.draft.json`，写 `rounds_confirmed.json`（调用 `validate_confirmed_rounds`）。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_round_gt_confirm_and_hard_retrain.py::test_validate_confirmed_rounds_rejects_overlap_and_bad_bounds tests/test_round_gt_confirm_and_hard_retrain.py::test_find_nearest_frame_picks_closest_timestamp -q`

Expected: PASS。

### Task 2: `/rounds` UI 与 API

**Files:**
- Modify: `scripts/valorant_vision/serve_label_ui.py`
- Test: `tests/test_round_gt_confirm_and_hard_retrain.py`

- [ ] **Step 1: 写 API 辅助失败测试**

```python
from scripts.valorant_vision.round_gt import build_preview_payload


def test_build_preview_payload_returns_rel_paths_for_start_and_end() -> None:
    frames = [
        {"timestamp_sec": 10.0, "rel_path": "s.jpg"},
        {"timestamp_sec": 20.0, "rel_path": "e.jpg"},
    ]
    payload = build_preview_payload(frames, start=10.1, end=19.8)
    assert payload["start"]["rel_path"] == "s.jpg"
    assert payload["end"]["rel_path"] == "e.jpg"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_round_gt_confirm_and_hard_retrain.py::test_build_preview_payload_returns_rel_paths_for_start_and_end -q`

Expected: FAIL。

- [ ] **Step 3: 实现预览辅助并接到 HTTP**

在 `round_gt.py` 增加 `build_preview_payload`。在 `serve_label_ui.py`：

- `GET /rounds` → 返回回合确认 HTML（列表 + start/end 输入 + end_reason + 双预览图 + 保存）。
- `GET /api/rounds` → 优先读 `rounds_confirmed.json`，否则 draft。
- `GET /api/rounds/preview?start=&end=` → JSON 含起止 `rel_path`。
- `POST /api/rounds` → body 为完整 confirmed payload；校验后写入 `rounds_confirmed.json`。
- 帧标注首页增加「确认回合」链接到 `/rounds`。

预览图继续走现有 `GET /frame/<rel_path>`。

- [ ] **Step 4: 运行测试通过**

Run: `python -m pytest tests/test_round_gt_confirm_and_hard_retrain.py -q`

Expected: PASS。

- [ ] **Step 5: 手工启动确认（人工步骤）**

```powershell
python scripts/valorant_vision/serve_label_ui.py --root "$env:USERPROFILE\LSC\datasets\valorant_phase\annotate\security_nick_20260722"
```

打开 `http://127.0.0.1:8765/rounds`，确认/修正全部回合后保存，确保生成 `rounds_confirmed.json`。

### Task 3: Merge 确认标记回归

**Files:**
- Test: `tests/test_round_gt_confirm_and_hard_retrain.py`
- Modify only if needed: `scripts/valorant_vision/merge_round_boundary_labels.py`

- [ ] **Step 1: 写回归测试（完整 fixture）**

```python
import json
from pathlib import Path

from scripts.valorant_vision.merge_round_boundary_labels import merge_labels


def test_merge_prefers_rounds_confirmed_and_sets_human_confirmed(tmp_path: Path) -> None:
    frame = tmp_path / "f.jpg"
    frame.write_bytes(b"jpeg")
    queue = [{
        "id": "a",
        "abs_path": str(frame),
        "video_id": "nick",
        "video_path": "nick.mp4",
        "timestamp_sec": 1.0,
        "session_id": "nick",
    }, {
        "id": "b",
        "abs_path": str(frame),
        "video_id": "nick",
        "video_path": "nick.mp4",
        "timestamp_sec": 2.0,
        "session_id": "nick",
    }, {
        "id": "c",
        "abs_path": str(frame),
        "video_id": "nick",
        "video_path": "nick.mp4",
        "timestamp_sec": 3.0,
        "session_id": "nick",
    }]
    labels = {"a": {"label": "buy"}, "b": {"label": "combat"}, "c": {"label": "result"}}
    queue_path = tmp_path / "queue.json"
    labels_path = tmp_path / "labels.json"
    queue_path.write_text(json.dumps(queue), encoding="utf-8")
    labels_path.write_text(json.dumps(labels), encoding="utf-8")
    (tmp_path / "round_manifest.draft.json").write_text(
        json.dumps({"videos": [{"video_id": "nick", "video_path": "nick.mp4",
                                "ground_truth": [{"start": 1.0, "end": 9.0}]}]}),
        encoding="utf-8",
    )
    (tmp_path / "rounds_confirmed.json").write_text(
        json.dumps({"videos": [{"video_id": "nick", "video_path": "nick.mp4",
                                "ground_truth": [{"start": 1.0, "end": 3.0,
                                                 "end_reason": "result",
                                                 "round_key": "R1"}]}]}),
        encoding="utf-8",
    )
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    out_dir = tmp_path / "out"
    result = merge_labels(
        queue_path=queue_path,
        labels_path=labels_path,
        data_dir=data_dir,
        out_dir=out_dir,
    )
    assert result["human_confirmed"] is True
    rounds = json.loads((out_dir / "round_manifest.json").read_text(encoding="utf-8"))
    assert rounds["human_confirmed"] is True
    assert rounds["videos"][0]["ground_truth"][0]["end"] == 3.0
```

- [ ] **Step 2: 运行测试**

Run: `python -m pytest tests/test_round_gt_confirm_and_hard_retrain.py::test_merge_prefers_rounds_confirmed_and_sets_human_confirmed -q`

Expected: PASS（现有 `_load_confirmed_rounds` 已支持）；若 FAIL，仅修 merge 的 confirmed 优先路径。

- [ ] **Step 3: 若已 PASS，无需改生产代码；记录回归覆盖即可。**

### Task 4: Hard 漏检抽帧队列

**Files:**
- Create: `scripts/valorant_vision/build_hard_miss_queue.py`
- Test: `tests/test_round_gt_confirm_and_hard_retrain.py`

- [ ] **Step 1: 写失败测试**

```python
from scripts.valorant_vision.build_hard_miss_queue import build_hard_windows


def test_build_hard_windows_covers_missed_round_and_end_error() -> None:
    gt = [{"start": 100.0, "end": 140.0, "round_key": "R1"}]
    missed = [{"round_key": "R1", "start": 100.0, "end": 140.0}]
    end_errors = [{"round_key": "R2", "gt_end": 200.0, "pred_end": 230.0}]
    windows = build_hard_windows(
        missed_rounds=missed,
        end_errors=end_errors,
        radius=8.0,
    )
    assert any(w["start"] <= 100.0 and w["end"] >= 140.0 for w in windows)
    assert any(w["start"] <= 192.0 and w["end"] >= 208.0 for w in windows)
```

- [ ] **Step 2: 运行确认失败**

- [ ] **Step 3: 实现 `build_hard_windows` + CLI**

CLI：`--video`、`--confirmed-rounds`、`--eval-report`（候选或基线 rounds JSON）、`--out-dir`、`--radius-sec`（默认 8）、`--fps`（默认 2）。

行为：

1. 从 eval report / 或本地重跑匹配逻辑，得到漏回合与 |end_err| > 0.8 的配对；
2. 生成 hard 窗口并抽帧（可复用 `build_round_boundary_queue._extract_frames` / `build_sample_times` 思路）；
3. 写出 `queue.json` 到独立目录，例如 `annotate/security_nick_20260722_hard`。

解码失败必须报错。

- [ ] **Step 4: 测试通过**

- [ ] **Step 5: 在确认真值存在后生成 hard 队列（人工可随后补标）**

```powershell
python scripts/valorant_vision/build_hard_miss_queue.py `
  --video "<recording.mp4>" `
  --confirmed-rounds "$env:USERPROFILE\LSC\datasets\valorant_phase\annotate\security_nick_20260722\rounds_confirmed.json" `
  --eval-report "$env:USERPROFILE\LSC\datasets\valorant_phase_boundary_20260722\reports\candidate_rounds.json" `
  --out-dir "$env:USERPROFILE\LSC\datasets\valorant_phase\annotate\security_nick_20260722_hard" `
  --radius-sec 8 --fps 2
```

### Task 5: 用确认真值复评 → 合并 hard → 训练 v2 → 门禁

**Files:**
- Verify: `scripts/valorant_vision/eval_blind.py`、`eval_codex_broadcast.py`、`train_export.py`、`merge_round_boundary_labels.py`

- [ ] **Step 1: 将 confirmed rounds 写入评估用 manifest**

```powershell
python scripts/valorant_vision/merge_round_boundary_labels.py `
  --queue "$env:USERPROFILE\LSC\datasets\valorant_phase\annotate\security_nick_20260722\queue.json" `
  --labels "$env:USERPROFILE\LSC\datasets\valorant_phase\annotate\security_nick_20260722\labels.json" `
  --data-dir "$env:USERPROFILE\LSC\datasets\valorant_phase" `
  --out-dir "$env:USERPROFILE\LSC\datasets\valorant_phase_boundary_20260722_v2"
```

Expected: `boundary_dataset_manifest.json` 中 `human_confirmed: true`。若 hard 标签已完成，先把 hard 标签合并进同一 out-dir（或第二次 merge 到 v2 数据目录）；保持 blind/test 只读。

- [ ] **Step 2: 确认真值上复评生产与候选 v1**

```powershell
$env:PYTHONPATH="$PWD"
python scripts/valorant_vision/eval_blind.py `
  --round-manifest "$env:USERPROFILE\LSC\datasets\valorant_phase_boundary_20260722_v2\round_manifest.json" `
  --model-dir "$PWD\lsc\analyzer\models" `
  --output "$env:USERPROFILE\LSC\datasets\valorant_phase_boundary_20260722_v2\reports\baseline_rounds.json"
python scripts/valorant_vision/eval_blind.py `
  --round-manifest "$env:USERPROFILE\LSC\datasets\valorant_phase_boundary_20260722_v2\round_manifest.json" `
  --model-dir "$env:USERPROFILE\LSC\models\valorant_phase_boundary_20260722" `
  --output "$env:USERPROFILE\LSC\datasets\valorant_phase_boundary_20260722_v2\reports\candidate_v1_rounds.json"
```

- [ ] **Step 3: 训练候选 v2**

```powershell
python scripts/valorant_vision/train_export.py `
  --data-dir "$env:USERPROFILE\LSC\datasets\valorant_phase_boundary_20260722_v2" `
  --out-dir "$env:USERPROFILE\LSC\models\valorant_phase_boundary_20260722_v2" `
  --epochs 10 --seed 20260722
```

Expected: 写出独立 ONNX/JSON，不覆盖 `lsc/analyzer/models/`。

- [ ] **Step 4: 评估候选 v2 + 分类对照**

```powershell
python scripts/valorant_vision/eval_blind.py `
  --round-manifest "$env:USERPROFILE\LSC\datasets\valorant_phase_boundary_20260722_v2\round_manifest.json" `
  --model-dir "$env:USERPROFILE\LSC\models\valorant_phase_boundary_20260722_v2" `
  --output "$env:USERPROFILE\LSC\datasets\valorant_phase_boundary_20260722_v2\reports\candidate_v2_rounds.json"
python scripts/valorant_vision/eval_codex_broadcast.py `
  --annotation-dir "$env:USERPROFILE\LSC\datasets\valorant_phase\annotate\security_nick_20260722" `
  --model-dir "$env:USERPROFILE\LSC\models\valorant_phase_boundary_20260722_v2" `
  --split all `
  --output "$env:USERPROFILE\LSC\datasets\valorant_phase_boundary_20260722_v2\reports\candidate_v2_classification.json"
```

- [ ] **Step 5: 门禁结论**

候选 v2 必须：回合召回 ≥ 0.90，起止 P95 ≤ 0.8s，listed precision 等不退化。失败则保留 `*_v2` 目录并列出下一轮标注清单；**禁止**替换生产模型。原 blind/test 继续用仓库既有评估命令同版本复测。

### Task 6: 全量回归与交付

**Files:**
- Verify: `tests/test_round_gt_confirm_and_hard_retrain.py`、`tests/test_round_boundary_optimization.py`、`tests/test_valorant_round_fsm.py`、`tests/test_continuous_analysis_guards.py`、`tests/test_valorant_hybrid_detect.py`

- [ ] **Step 1: 跑专项 + 既有回归**

Run:

```powershell
python -m pytest tests/test_round_gt_confirm_and_hard_retrain.py tests/test_round_boundary_optimization.py tests/test_valorant_round_fsm.py tests/test_continuous_analysis_guards.py tests/test_valorant_hybrid_detect.py -q
```

Expected: 全部通过。

- [ ] **Step 2: 保存交付证据**

交付：`rounds_confirmed.json`、hard 队列摘要、v2 模型 SHA-256、对照报告、`delivery_summary.json`（含 `release_approved`）。未过门禁不得声称已部署。

- [ ] **Step 3: 仅提交本计划相关脚本/测试/文档**（用户明确要求 commit 时才执行）

## 计划自检

- Spec 阶段 A（UI/确认 JSON）→ Task 1–3；阶段 B（hard/重训/门禁）→ Task 4–5；验证 → Task 6。
- 无 TBD/占位步骤；接口名与 `round_gt` / hard CLI 前后一致。
- 盲测只读；候选写入独立 `*_v2` 目录；弱 `model_result` 语义不改。
