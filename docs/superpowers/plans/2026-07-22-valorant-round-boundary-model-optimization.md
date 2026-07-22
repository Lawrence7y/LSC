# Valorant 回合边界模型优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用「保安头子尼克」完整录制建立可审计的边界样本，训练并评估一个提高回合召回、降低弱 `model_result` 结束误差的候选模型。

**Architecture:** 保留现有五分类 MobileNetV3-Small 与 ONNX 推理接口。新增独立的边界抽帧/标注队列和数据合并脚本，把人工确认的相位帧与回合真值加入训练数据；训练前后都在同一盲测集上评估，运行时继续将弱结果保留为 `pending`，只有门禁通过的候选模型才可替换生产模型。

**Tech Stack:** Python 3、OpenCV、PyTorch/TorchVision、ONNX、pytest、现有 `eval_gates.py`。

---

## 文件与职责

- Create: `scripts/valorant_vision/build_round_boundary_queue.py` — 从完整录制和分析快照抽取候选边界、间隔及上下文帧，生成标注队列和回合草稿。
- Create: `scripts/valorant_vision/merge_round_boundary_labels.py` — 校验人工标签并把新录制按连续时间块合并到训练/验证数据，不触碰 blind/test。
- Modify: `scripts/valorant_vision/serve_label_ui.py` — 增加可选 `--root`，允许标注独立队列目录，默认行为保持不变。
- Modify: `scripts/valorant_vision/train_export.py` — 增加可复现实验种子和数据集摘要元数据；不改变模型结构或默认增强策略。
- Modify: `scripts/valorant_vision/eval_codex_broadcast.py` — 增加 `--annotation-dir` 和 `--output`，让新录制标注集可复用同一分类评估器。
- Test: `tests/test_round_boundary_optimization.py` — 覆盖抽样、标签校验、时间块拆分、blind 隔离和弱结果门禁。
- Verify: `scripts/valorant_vision/eval_blind.py`、`scripts/valorant_vision/eval_gates.py` — 生成基线/候选模型对照报告。
- Runtime check: `lsc/analyzer/round_detector.py`、`lsc/analyzer/valorant_round_fsm.py` — 仅复用并回归验证现有弱 `model_result` 不会升级为 `vision_confirmed`；只有测试失败时才修改。

### Task 1: 为本次录制建立边界抽帧队列

**Files:**
- Create: `scripts/valorant_vision/build_round_boundary_queue.py`
- Test: `tests/test_round_boundary_optimization.py`

- [ ] **Step 1: 写抽样与时间校验的失败测试**

```python
from scripts.valorant_vision.build_round_boundary_queue import (
    build_sample_times,
    validate_round_rows,
)


def test_build_sample_times_covers_boundaries_and_gap_context() -> None:
    rows = [{"start": 100.0, "end": 140.0}, {"start": 220.0, "end": 250.0}]
    times = build_sample_times(rows, duration=300.0, radius=12.0, fps=2.0)
    assert 88.0 in times
    assert 140.0 in times
    assert 160.0 in times
    assert 220.0 in times


def test_validate_round_rows_rejects_invalid_or_overlapping_rows() -> None:
    rows = [{"start": 20.0, "end": 10.0}]
    try:
        validate_round_rows(rows, duration=60.0)
    except ValueError as exc:
        assert "end" in str(exc)
    else:
        raise AssertionError("invalid round row was accepted")
```

- [ ] **Step 2: 运行失败测试**

Run: `python -m pytest tests/test_round_boundary_optimization.py::test_build_sample_times_covers_boundaries_and_gap_context tests/test_round_boundary_optimization.py::test_validate_round_rows_rejects_invalid_or_overlapping_rows -q`

Expected: FAIL because `build_round_boundary_queue.py` and its helpers do not exist.

- [ ] **Step 3: 实现最小抽样模块**

实现以下接口：

```python
def validate_round_rows(rows: list[dict], *, duration: float) -> None:
    ordered = sorted(rows, key=lambda row: float(row["start"]))
    previous_end = -1.0
    for row in ordered:
        start = float(row["start"])
        end = float(row["end"])
        if not (0.0 <= start < end <= duration):
            raise ValueError(f"invalid round bounds: {start}-{end}")
        if start < previous_end:
            raise ValueError(f"overlapping round bounds: {start}-{end}")
        previous_end = end


def build_sample_times(
    rows: list[dict], *, duration: float, radius: float, fps: float
) -> list[float]:
    if duration <= 0 or radius <= 0 or fps <= 0:
        raise ValueError("duration, radius and fps must be positive")
    validate_round_rows(rows, duration=duration)
    step = 1.0 / fps
    points = {round(max(0.0, min(duration, t)), 3) for t in (0.0, duration)}
    for row in rows:
        for center in (float(row["start"]), float(row["end"])):
            t = center - radius
            while t <= center + radius:
                points.add(round(max(0.0, min(duration, t)), 3))
                t += step
    for left, right in zip(rows, rows[1:]):
        gap_start = float(left["end"])
        gap_end = float(right["start"])
        t = gap_start
        while t <= gap_end:
            points.add(round(t, 3))
            t += step
    return sorted(points)
```

命令行参数必须是 `--video`、`--analysis-json`、`--out-dir`、`--radius-sec`、`--fps`。脚本用 `cv2.VideoCapture` 读取视频，写出 `frame_*.jpg`、兼容现有标注 UI 的 `queue.json`，并写出 `round_manifest.draft.json`；解码失败、重复时间戳和越界必须报错，不得静默跳过。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_round_boundary_optimization.py::test_build_sample_times_covers_boundaries_and_gap_context tests/test_round_boundary_optimization.py::test_validate_round_rows_rejects_invalid_or_overlapping_rows -q`

Expected: PASS。

- [ ] **Step 5: 对本次录制生成队列**

Run:

```powershell
python scripts/valorant_vision/build_round_boundary_queue.py `
  --video 'D:\desktop\新建文件夹 (2)\douyin_保安头子尼克_无畏契约_d62ab9\recording_20260722_102229_f57bf6.mp4' `
  --analysis-json 'D:\desktop\新建文件夹 (2)\douyin_保安头子尼克_无畏契约_d62ab9\recording_20260722_102229_f57bf6.analysis.json' `
  --out-dir "$env:USERPROFILE\LSC\datasets\valorant_phase\annotate\security_nick_20260722" `
  --radius-sec 12 --fps 2
```

Expected: 输出 8 个候选回合、候选间隔帧和可读的 `queue.json`；所有帧路径存在，时间戳在完整录制时长内。

### Task 2: 完成人工边界标注并隔离数据泄漏

**Files:**
- Modify: `scripts/valorant_vision/serve_label_ui.py`
- Modify: `scripts/valorant_vision/merge_round_boundary_labels.py`
- Test: `tests/test_round_boundary_optimization.py`

- [ ] **Step 1: 写 root 参数和标签校验失败测试**

```python
from scripts.valorant_vision.merge_round_boundary_labels import (
    split_by_time_block,
    validate_labels,
)


def test_split_by_time_block_keeps_one_session_out_of_both_splits() -> None:
    rows = [{"timestamp_sec": t, "video_id": "nick"} for t in (1, 2, 3, 4, 5, 6)]
    train, val = split_by_time_block(rows, validation_fraction=1 / 3)
    assert {r["video_id"] for r in train} == {"nick"}
    assert {r["video_id"] for r in val} == {"nick"}
    assert max(r["timestamp_sec"] for r in train) < min(r["timestamp_sec"] for r in val)


def test_validate_labels_rejects_unknown_label_and_missing_frame() -> None:
    rows = [{"id": "x", "label": "unknown", "abs_path": "missing.jpg"}]
    try:
        validate_labels(rows)
    except ValueError as exc:
        assert "label" in str(exc) or "frame" in str(exc)
    else:
        raise AssertionError("invalid label row was accepted")
```

- [ ] **Step 2: 运行失败测试**

Run: `python -m pytest tests/test_round_boundary_optimization.py::test_split_by_time_block_keeps_one_session_out_of_both_splits tests/test_round_boundary_optimization.py::test_validate_labels_rejects_unknown_label_and_missing_frame -q`

Expected: FAIL because the merge module and the UI root option do not exist.

- [ ] **Step 3: 实现标注目录和合并校验**

`serve_label_ui.py` 增加 `--root` 参数；未指定时继续使用现有 `Path.home() / "LSC" / "datasets" / "valorant_phase" / "annotate"`。`merge_round_boundary_labels.py` 只接受五个合法相位标签，检查 `abs_path`、唯一 ID、唯一 `(video_id, timestamp_sec)`，并把相同录制按时间排序后切成前 2/3 train、后 1/3 validation；blind/test 路径只能读取并拒绝写入。

合并数据时必须输出 `boundary_dataset_manifest.json`，记录来源视频、会话 ID、train/val 数量、标签计数、最小/最大时间戳和输入文件 SHA-256；同时输出供 `eval_blind.py --round-manifest` 使用的 `round_manifest.json`，其中每个视频条目包含 `video_path` 和人工确认的 `ground_truth` 回合数组。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_round_boundary_optimization.py::test_split_by_time_block_keeps_one_session_out_of_both_splits tests/test_round_boundary_optimization.py::test_validate_labels_rejects_unknown_label_and_missing_frame -q`

Expected: PASS。

- [ ] **Step 5: 人工标注并导出数据**

Run: `python scripts/valorant_vision/serve_label_ui.py --root "$env:USERPROFILE\LSC\datasets\valorant_phase\annotate\security_nick_20260722"`

在 UI 中标注所有边界上下文和候选间隔；随后运行：

```powershell
python scripts/valorant_vision/merge_round_boundary_labels.py `
  --queue "$env:USERPROFILE\LSC\datasets\valorant_phase\annotate\security_nick_20260722\queue.json" `
  --labels "$env:USERPROFILE\LSC\datasets\valorant_phase\annotate\security_nick_20260722\labels.json" `
  --data-dir "$env:USERPROFILE\LSC\datasets\valorant_phase" `
  --out-dir "$env:USERPROFILE\LSC\datasets\valorant_phase_boundary_20260722"
```

Expected: 五个类别均有计数；输出目录只有 train/val 和 manifest，不生成或修改 blind/test。

### Task 3: 生成当前模型基线并训练候选模型

**Files:**
- Modify: `scripts/valorant_vision/train_export.py`
- Test: `tests/test_round_boundary_optimization.py`

- [ ] **Step 1: 写可复现元数据失败测试**

```python
from scripts.valorant_vision.train_export import experiment_metadata


def test_experiment_metadata_contains_seed_and_dataset_digest() -> None:
    meta = experiment_metadata(seed=20260722, train_count=10, val_count=4, digest="abc")
    assert meta == {
        "seed": 20260722,
        "train_count": 10,
        "val_count": 4,
        "dataset_digest": "abc",
    }
```

- [ ] **Step 2: 运行失败测试**

Run: `python -m pytest tests/test_round_boundary_optimization.py::test_experiment_metadata_contains_seed_and_dataset_digest -q`

Expected: FAIL because `experiment_metadata` does not exist.

- [ ] **Step 3: 增加最小可复现实验元数据**

实现 `experiment_metadata`，并增加 `--seed` 参数；在训练入口设置 Python、NumPy、Torch 和 DataLoader 的随机种子，把种子与数据集 SHA-256 写入模型 JSON。默认 seed 使用 `20260722`，不改变现有命令的模型结构、类别和增强。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_round_boundary_optimization.py::test_experiment_metadata_contains_seed_and_dataset_digest -q`

Expected: PASS。

- [ ] **Step 5: 生成旧模型基线**

先让 `eval_codex_broadcast.py` 接受 `--annotation-dir`、`--model-dir` 和 `--output`，再运行：

```powershell
python scripts/valorant_vision/eval_codex_broadcast.py `
  --annotation-dir "$env:USERPROFILE\LSC\datasets\valorant_phase\annotate\security_nick_20260722" `
  --model-dir "$PWD\lsc\analyzer\models" `
  --split all `
  --output "$env:USERPROFILE\LSC\datasets\valorant_phase_boundary_20260722\reports\baseline_classification.json"
```

使用当前运行模型对新录制标注集生成回合报告，输出到 `$env:USERPROFILE\LSC\datasets\valorant_phase_boundary_20260722\reports\baseline_rounds.json`；记录新录制分类指标、回合召回、起止误差和漏回合列表。基线输出不得覆盖现有 blind 报告。

- [ ] **Step 6: 训练候选模型到独立目录**

Run:

```powershell
python scripts/valorant_vision/train_export.py `
  --data-dir "$env:USERPROFILE\LSC\datasets\valorant_phase_boundary_20260722" `
  --out-dir "$env:USERPROFILE\LSC\models\valorant_phase_boundary_20260722" `
  --epochs 10 --seed 20260722
```

Expected: 输出候选 ONNX、JSON 元数据、训练/验证日志；不覆盖 `lsc/analyzer/models/valorant_phase_v1.*`。

### Task 4: 对照评估并回归弱终点门禁

**Files:**
- Test: `tests/test_round_boundary_optimization.py`
- Verify: `scripts/valorant_vision/eval_blind.py`, `scripts/valorant_vision/eval_gates.py`
- Runtime check: `lsc/analyzer/round_detector.py`, `lsc/analyzer/valorant_round_fsm.py`

- [ ] **Step 1: 写弱 `model_result` 回归测试**

```python
from lsc.analyzer.round_detector import grade_round_confirmation


def test_weak_model_result_stays_pending_even_with_score_hint() -> None:
    assert grade_round_confirmation(
        start_strong=False,
        end_strong=False,
        score_confirm=True,
    ) == "pending"
```

- [ ] **Step 2: 运行测试确认当前行为**

Run: `python -m pytest tests/test_round_boundary_optimization.py::test_weak_model_result_stays_pending_even_with_score_hint -q`

Expected: PASS；若失败，只修正确认状态逻辑并先单独验证，不调整导出门禁。

- [ ] **Step 3: 在新录制上分别评估旧模型与候选模型**

Run:

```powershell
python scripts/valorant_vision/eval_blind.py `
  --round-manifest "$env:USERPROFILE\LSC\datasets\valorant_phase_boundary_20260722\round_manifest.json" `
  --model-dir "$PWD\lsc\analyzer\models" `
  --output "$env:USERPROFILE\LSC\datasets\valorant_phase_boundary_20260722\reports\baseline_rounds.json" `
  --enforce-gates
python scripts/valorant_vision/eval_blind.py `
  --round-manifest "$env:USERPROFILE\LSC\datasets\valorant_phase_boundary_20260722\round_manifest.json" `
  --model-dir "$env:USERPROFILE\LSC\models\valorant_phase_boundary_20260722" `
  --output "$env:USERPROFILE\LSC\datasets\valorant_phase_boundary_20260722\reports\candidate_rounds.json" `
  --enforce-gates
python scripts/valorant_vision/eval_codex_broadcast.py `
  --annotation-dir "$env:USERPROFILE\LSC\datasets\valorant_phase\annotate\security_nick_20260722" `
  --model-dir "$env:USERPROFILE\LSC\models\valorant_phase_boundary_20260722" `
  --split all `
  --output "$env:USERPROFILE\LSC\datasets\valorant_phase_boundary_20260722\reports\candidate_classification.json"
```

Expected: 两份回合报告和两份分类报告均生成；候选模型必须提升回合召回和结束点 P95，且不能让原 blind/test 门禁退化。原 blind/test 继续使用仓库现有独立评估命令和同一模型版本，不把新录制样本混入其中。

- [ ] **Step 4: 生成逐回合差异报告**

保存每个回合的 `round_key`、真值起止、旧模型起止、候选模型起止、start/end error、confirm_status、end_reason；单独列出漏回合和 `model_result` 弱终点。

- [ ] **Step 5: 只在全部门禁通过后准备替换**

复制候选模型到临时运行目录并做一次完整回放；核对 ONNX SHA-256、模型 JSON 数据集版本和评估报告后，才允许提出替换 `lsc/analyzer/models/valorant_phase_v1.*` 的变更。任一门禁失败则保留候选目录并返回下一轮标注清单。

### Task 5: 全量验证与交付

**Files:**
- Verify: `tests/test_valorant_round_fsm.py`
- Verify: `tests/test_continuous_analysis_guards.py`
- Verify: `tests/test_valorant_hybrid_detect.py`

- [ ] **Step 1: 运行模型优化专项测试**

Run: `python -m pytest tests/test_round_boundary_optimization.py -q`

Expected: 全部通过。

- [ ] **Step 2: 运行现有回合与连续分析回归**

Run: `python -m pytest tests/test_valorant_round_fsm.py tests/test_continuous_analysis_guards.py tests/test_valorant_hybrid_detect.py -q`

Expected: 全部通过，且没有新增失败或未处理异常。

- [ ] **Step 3: 保存交付证据**

交付 `boundary_dataset_manifest.json`、旧/候选模型评估报告、逐回合差异报告、候选模型 SHA-256 和测试输出；明确记录是否达到发布门禁。未达到门禁时，不声称模型已部署。

- [ ] **Step 4: 提交可审阅变更**

只提交本计划涉及的脚本、测试和文档；不得暂存或覆盖工作区中原有的识别器、配置、模型和数据脚本改动。

## 计划自检

- 数据标注、训练、运行时确认和评估均有独立任务，覆盖设计文档全部目标。
- 盲测集只读，候选模型使用独立输出目录，未通过门禁不会替换生产模型。
- 每个实现步骤都有明确文件、失败测试、命令和预期结果；没有占位步骤或未定义接口。
- 现有弱 `model_result` 逻辑先回归验证，只有测试失败才修改，避免无证据扩大运行时改动。
