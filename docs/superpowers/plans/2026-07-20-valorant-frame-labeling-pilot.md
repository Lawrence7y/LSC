# Valorant Frame Labeling Pilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用一段 POV 和一段赛事转播录像完成可复现的五分类帧标注、INT8 ONNX 训练、内部时间块评估与安全 shadow 验证。

**Architecture:** 复用现有 FFmpeg 抽帧、MobileNetV3-Small 训练、分类器契约和 `eval_blind.py`。把当前未跟踪的本地标注草稿收敛成“会话配置 → 粗帧队列 → 区间候选标签 → 边界密帧 → 人工复核 → manifest”的最小工具链；数据与模型只写入已忽略的 `datasets/valorant_phase/`，生产代码只补 Electron 环境透传和必要评估入口。

**Tech Stack:** Python 3.12、FFmpeg/FFprobe、OpenCV、PyTorch/torchvision、ONNX Runtime、Electron/TypeScript、pytest。

**Spec:** `docs/superpowers/specs/2026-07-20-valorant-frame-labeling-pilot-design.md`

---

## File map

- `scripts/valorant_vision/build_label_queue.py`：改造当前未跟踪草稿；从会话 JSON 和粗抽帧目录生成带时间块 split 的队列。
- `scripts/valorant_vision/apply_interval_labels.py`：改造当前未跟踪草稿；读取通用区间 JSON，只生成可复核候选标签，不保留硬编码录像路径。
- `scripts/valorant_vision/serve_label_ui.py`：改造当前未跟踪草稿；安全加载队列、保存人工标签并导出 manifest。
- `scripts/valorant_vision/densify_boundaries.py`：新增；从粗标签转换点生成 ±3 秒密集时间戳并抽帧，合并回标注队列。
- `scripts/valorant_vision/predict_manifest.py`：新增；对 manifest 对应图片批量推理并输出 `eval_blind.py` 可消费的 predictions JSONL。
- `scripts/valorant_vision/train_export.py`：增加每类训练样本和非空验证集门禁。
- `scripts/valorant_vision/eval_blind.py`：完整录像评估支持 manifest 中的 `time_range`，只评估保留时间块。
- `lsc-electron/electron/main.ts`：把两个 Valorant 环境变量加入后端白名单。
- `tests/test_valorant_labeling_tools.py`：标注队列、区间标签、密集边界与 manifest 导出的纯函数测试。
- `tests/test_valorant_predict_manifest.py`：批量预测输出测试。
- `tests/test_valorant_frame_classifier.py`：训练数据门禁测试。
- `tests/test_valorant_eval_gates.py`：按时间块运行检测器的测试。
- `tests/test_electron_backend_env.py`：Electron 环境白名单回归测试。

执行期间不得暂存或覆盖现有用户改动：旧 HUD spec、`rooms.json`、`rooms.json.bak`、`.worktrees/`。三个未跟踪标注草稿只能按本计划先测试再改造。

### Task 1: Make the coarse labeling queue configuration-driven

**Files:**
- Create: `tests/test_valorant_labeling_tools.py`
- Create from current untracked draft: `scripts/valorant_vision/build_label_queue.py`

- [ ] **Step 1: Write failing split and queue tests**

```python
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "valorant_vision"
sys.path.insert(0, str(SCRIPTS))

from build_label_queue import VideoSession, build_queue, choose_split


def test_choose_split_uses_temporal_blocks_and_gap() -> None:
    assert choose_split(100.0, 1000.0, gap_sec=30.0) == "train"
    assert choose_split(650.0, 1000.0, gap_sec=30.0) is None
    assert choose_split(700.0, 1000.0, gap_sec=30.0) == "val"
    assert choose_split(800.0, 1000.0, gap_sec=30.0) is None
    assert choose_split(900.0, 1000.0, gap_sec=30.0) == "test"


def test_build_queue_keeps_both_sources_in_each_split(tmp_path: Path) -> None:
    sessions = []
    for source in ("pov", "broadcast"):
        frame_dir = tmp_path / source
        frame_dir.mkdir()
        for index in (1, 176, 226):
            (frame_dir / f"frame_{index:06d}.jpg").write_bytes(b"jpg")
        sessions.append(VideoSession(source, f"{source}.mp4", frame_dir, source, source))

    rows = build_queue(
        sessions,
        {"pov": 1000.0, "broadcast": 1000.0},
        frame_root=tmp_path,
        interval_sec=4.0,
    )

    assert {(row["source_type"], row["split"]) for row in rows} == {
        ("pov", "train"), ("pov", "val"), ("pov", "test"),
        ("broadcast", "train"), ("broadcast", "val"), ("broadcast", "test"),
    }
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
python -m pytest tests/test_valorant_labeling_tools.py -q
```

Expected: collection fails because the current draft has no `VideoSession`, `choose_split`, or parameterized `build_queue`.

- [ ] **Step 3: Replace hard-coded sessions with the minimal pure API and CLI**

Implement these definitions in `build_label_queue.py`; retain JSON writing in `main`, but read `--sessions`, `--output`, `--interval` and `--gap-sec` arguments instead of constants:

```python
from dataclasses import dataclass

VALID_SOURCES = {"pov", "broadcast"}


@dataclass(frozen=True)
class VideoSession:
    video_id: str
    video_path: str
    frame_dir: Path
    source_type: str
    session_id: str

    @classmethod
    def from_dict(cls, row: dict) -> "VideoSession":
        source = str(row["source_type"])
        if source not in VALID_SOURCES:
            raise ValueError(f"invalid source_type: {source}")
        return cls(
            video_id=str(row["video_id"]),
            video_path=str(row["video_path"]),
            frame_dir=Path(row["frame_dir"]),
            source_type=source,
            session_id=str(row["session_id"]),
        )


def choose_split(timestamp_sec: float, duration_sec: float, *, gap_sec: float = 30.0) -> str | None:
    if duration_sec <= 0 or not 0 <= timestamp_sec <= duration_sec:
        raise ValueError("timestamp/duration out of range")
    train_end = duration_sec * 0.65
    val_end = duration_sec * 0.80
    half_gap = gap_sec / 2.0
    if abs(timestamp_sec - train_end) <= half_gap or abs(timestamp_sec - val_end) <= half_gap:
        return None
    if timestamp_sec < train_end:
        return "train"
    if timestamp_sec < val_end:
        return "val"
    return "test"


def build_queue(
    sessions: list[VideoSession],
    durations: dict[str, float],
    *,
    frame_root: Path,
    interval_sec: float,
    gap_sec: float = 30.0,
) -> list[dict]:
    rows: list[dict] = []
    for session in sessions:
        duration = durations[session.video_id]
        for frame in sorted(session.frame_dir.glob("frame_*.jpg")):
            index = frame_index(frame)
            timestamp = (index - 1) * interval_sec
            split = choose_split(timestamp, duration, gap_sec=gap_sec)
            if split is None:
                continue
            rows.append({
                "id": f"{session.video_id}_{int(round(timestamp * 1000)):010d}",
                "rel_path": frame.resolve().relative_to(frame_root.resolve()).as_posix(),
                "abs_path": str(frame.resolve()),
                "video_id": session.video_id,
                "video_path": session.video_path,
                "timestamp_sec": timestamp,
                "source_type": session.source_type,
                "session_id": session.session_id,
                "split": split,
            })
    return rows
```

Use `ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 <video>` in `main` to fill `durations`; fail with the video path if duration is non-positive. CLI also requires `--frame-root`; reject any frame directory outside that root.

- [ ] **Step 4: Run the tests and CLI help**

```powershell
python -m pytest tests/test_valorant_labeling_tools.py -q
python scripts/valorant_vision/build_label_queue.py --help
```

Expected: tests pass; help lists `--sessions`, `--frame-root`, `--output`, `--interval`, and `--gap-sec`.

- [ ] **Step 5: Commit Task 1**

```powershell
git add scripts/valorant_vision/build_label_queue.py tests/test_valorant_labeling_tools.py
git commit -m "feat: build source-balanced valorant label queue"
```

### Task 2: Make interval pre-labeling generic and non-destructive

**Files:**
- Modify: `tests/test_valorant_labeling_tools.py`
- Create from current untracked draft: `scripts/valorant_vision/apply_interval_labels.py`

- [ ] **Step 1: Add failing tests for interval boundaries and manual-label preservation**

```python
from apply_interval_labels import apply_interval_candidates, label_at


def test_label_at_uses_half_open_intervals() -> None:
    intervals = [
        {"start_sec": 0.0, "end_sec": 10.0, "label": "buy", "notes": "barrier"},
        {"start_sec": 10.0, "end_sec": 20.0, "label": "combat", "notes": "live"},
    ]
    assert label_at(intervals, 9.999)["label"] == "buy"
    assert label_at(intervals, 10.0)["label"] == "combat"
    assert label_at(intervals, 20.0) is None


def test_interval_candidates_never_overwrite_human_label() -> None:
    queue = [{"id": "v_1", "video_id": "v", "timestamp_sec": 4.0}]
    labels = {"v_1": {"label": "result", "annotator": "human"}}
    intervals = {"v": [{"start_sec": 0.0, "end_sec": 8.0, "label": "combat"}]}

    apply_interval_candidates(queue, labels, intervals)

    assert labels["v_1"]["label"] == "result"
    assert labels["v_1"]["annotator"] == "human"
```

- [ ] **Step 2: Run the new tests and verify RED**

```powershell
python -m pytest tests/test_valorant_labeling_tools.py -q
```

Expected: import or attribute failure because the current draft only has hard-coded interval tables.

- [ ] **Step 3: Implement generic half-open interval candidates**

```python
VALID_LABELS = {"non_game", "buy", "combat", "result", "replay"}


def label_at(intervals: list[dict], timestamp_sec: float) -> dict | None:
    for interval in intervals:
        label = str(interval["label"])
        if label not in VALID_LABELS:
            raise ValueError(f"invalid label: {label}")
        if float(interval["start_sec"]) <= timestamp_sec < float(interval["end_sec"]):
            return interval
    return None


def apply_interval_candidates(queue: list[dict], labels: dict, intervals_by_video: dict) -> int:
    written = 0
    for item in queue:
        current = labels.get(item["id"])
        if current and current.get("annotator") == "human":
            continue
        interval = label_at(intervals_by_video.get(item["video_id"], []), item["timestamp_sec"])
        if interval is None:
            continue
        labels[item["id"]] = {
            "label": interval["label"],
            "notes": interval.get("notes", ""),
            "annotator": "interval_candidate_v1",
        }
        written += 1
    return written
```

CLI takes `--queue`, `--intervals`, `--labels`, and `--manifest`; remove all recording-specific constants and paths.

- [ ] **Step 4: Run tests and a temporary CLI round trip**

```powershell
python -m pytest tests/test_valorant_labeling_tools.py -q
python scripts/valorant_vision/apply_interval_labels.py --help
```

Expected: tests pass; help contains no hard-coded username, recording name, or fixed data root.

- [ ] **Step 5: Commit Task 2**

```powershell
git add scripts/valorant_vision/apply_interval_labels.py tests/test_valorant_labeling_tools.py
git commit -m "feat: apply reviewable valorant interval labels"
```

### Task 3: Harden the local labeling UI and manifest export

**Files:**
- Modify: `tests/test_valorant_labeling_tools.py`
- Create from current untracked draft: `scripts/valorant_vision/serve_label_ui.py`

- [ ] **Step 1: Add failing trust-boundary and export tests**

```python
import pytest

from serve_label_ui import build_manifest_rows, resolve_frame_path, validate_labels


def test_resolve_frame_path_rejects_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(ValueError, match="outside frame root"):
        resolve_frame_path(root, "../secret.jpg")


def test_validate_labels_rejects_unknown_class() -> None:
    with pytest.raises(ValueError, match="invalid label"):
        validate_labels({"v_1": {"label": "loading"}})


def test_manifest_export_contains_only_human_reviewed_rows() -> None:
    queue = [{
        "id": "v_1", "video_id": "v", "video_path": "v.mp4",
        "timestamp_sec": 4.0, "split": "train", "source_type": "pov",
        "session_id": "s",
    }]
    candidate = {"v_1": {"label": "combat", "annotator": "interval_candidate_v1"}}
    human = {"v_1": {"label": "combat", "annotator": "human", "notes": "checked"}}
    assert build_manifest_rows(queue, candidate) == []
    assert build_manifest_rows(queue, human)[0]["notes"] == "checked"
```

- [ ] **Step 2: Run tests and verify RED**

```powershell
python -m pytest tests/test_valorant_labeling_tools.py -q
```

Expected: missing pure helper functions.

- [ ] **Step 3: Extract validation helpers and make saves atomic**

```python
VALID_LABELS = {"non_game", "buy", "combat", "result", "replay"}


def resolve_frame_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise ValueError("outside frame root")
    return candidate


def validate_labels(labels: dict) -> dict:
    if not isinstance(labels, dict):
        raise ValueError("labels must be an object")
    for key, value in labels.items():
        if not isinstance(value, dict) or value.get("label") not in VALID_LABELS:
            raise ValueError(f"invalid label for {key}")
    return labels


def build_manifest_rows(queue: list[dict], labels: dict) -> list[dict]:
    rows: list[dict] = []
    for item in queue:
        label = labels.get(item["id"])
        if not label or label.get("annotator") != "human":
            continue
        rows.append({
            "video_id": item["video_id"],
            "video_path": item["video_path"],
            "timestamp_sec": item["timestamp_sec"],
            "label": label["label"],
            "split": item["split"],
            "source_type": item["source_type"],
            "session_id": item["session_id"],
            "notes": label.get("notes", ""),
        })
    return rows


def write_json_atomic(path: Path, value: object) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
```

Make CLI arguments `--root`, `--queue`, `--labels`, `--manifest`, and `--port`; POST `/api/labels` must call `validate_labels` and stamp newly changed UI entries with `annotator: "human"`.

候选标签仍属于“未复核”。把前端的未标判断统一为：

```javascript
function isReviewed(id) {
  return labels[id]?.annotator === "human";
}
function firstUnlabeled() {
  for (let i = 0; i < queue.length; i++) if (!isReviewed(queue[i].id)) return i;
  return 0;
}
function visibleIndices() {
  if (!onlyUnlabeled) return queue.map((_, i) => i);
  return queue.map((q, i) => [q, i]).filter(([q]) => !isReviewed(q.id)).map(([, i]) => i);
}
```

`saveLabel` 必须写入 `{label, notes, annotator: "human", timestamp_sec, video_id}`，确保用户按键确认后才进入 manifest。

- [ ] **Step 4: Run tests and smoke-start the UI against a temporary queue**

```powershell
python -m pytest tests/test_valorant_labeling_tools.py -q
python scripts/valorant_vision/serve_label_ui.py --help
```

Expected: tests pass and help displays all local-path arguments.

- [ ] **Step 5: Commit Task 3**

```powershell
git add scripts/valorant_vision/serve_label_ui.py tests/test_valorant_labeling_tools.py
git commit -m "feat: harden local valorant labeling UI"
```

### Task 4: Add boundary densification without a second FFmpeg implementation

**Files:**
- Create: `scripts/valorant_vision/densify_boundaries.py`
- Modify: `tests/test_valorant_labeling_tools.py`

- [ ] **Step 1: Add failing transition and dense timestamp tests**

```python
from densify_boundaries import dense_timestamps, transition_centers


def test_transition_centers_never_cross_video_or_split() -> None:
    queue = [
        {"id": "a1", "video_id": "a", "split": "train", "timestamp_sec": 4.0},
        {"id": "a2", "video_id": "a", "split": "train", "timestamp_sec": 8.0},
        {"id": "a3", "video_id": "a", "split": "val", "timestamp_sec": 700.0},
    ]
    labels = {
        "a1": {"label": "buy", "annotator": "human"},
        "a2": {"label": "combat", "annotator": "human"},
        "a3": {"label": "result", "annotator": "human"},
    }
    assert transition_centers(queue, labels) == [("a", "train", 6.0)]


def test_dense_timestamps_are_clamped_and_deduplicated() -> None:
    assert dense_timestamps([1.0, 2.0], duration=4.0, radius=1.0, step=0.5) == [
        0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0,
    ]
```

- [ ] **Step 2: Run the tests and verify RED**

```powershell
python -m pytest tests/test_valorant_labeling_tools.py -q
```

Expected: `ModuleNotFoundError: densify_boundaries`.

- [ ] **Step 3: Implement transition discovery and reuse `extract_single_frame`**

```python
from collections import defaultdict

from extract_frames import ManifestRow, extract_single_frame


def transition_centers(queue: list[dict], labels: dict) -> list[tuple[str, str, float]]:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for item in queue:
        label = labels.get(item["id"])
        if label and label.get("annotator") == "human":
            groups[(item["video_id"], item["split"])].append(item)
    out: list[tuple[str, str, float]] = []
    for (video_id, split), items in groups.items():
        ordered = sorted(items, key=lambda row: row["timestamp_sec"])
        for left, right in zip(ordered, ordered[1:]):
            if labels[left["id"]]["label"] != labels[right["id"]]["label"]:
                out.append((video_id, split, (left["timestamp_sec"] + right["timestamp_sec"]) / 2.0))
    return out


def dense_timestamps(centers: list[float], *, duration: float, radius: float = 3.0, step: float = 0.5) -> list[float]:
    values: set[float] = set()
    for center in centers:
        current = max(0.0, center - radius)
        end = min(duration, center + radius)
        while current <= end + 1e-9:
            values.add(round(current, 3))
            current += step
    return sorted(values)
```

For each timestamp, construct a `ManifestRow` only to reuse `extract_single_frame`; write the JPEG under `annotations/frames_dense/<video_id>/` and append a queue item whose ID uses timestamp milliseconds. Skip an ID already present in the coarse queue.

- [ ] **Step 4: Run tests and CLI help**

```powershell
python -m pytest tests/test_valorant_labeling_tools.py -q
python scripts/valorant_vision/densify_boundaries.py --help
```

Expected: tests pass; CLI requires queue, labels, sessions and output paths.

- [ ] **Step 5: Commit Task 4**

```powershell
git add scripts/valorant_vision/densify_boundaries.py tests/test_valorant_labeling_tools.py
git commit -m "feat: densify valorant phase boundaries"
```

### Task 5: Enforce train/validation class coverage before export

**Files:**
- Modify: `scripts/valorant_vision/train_export.py`
- Modify: `tests/test_valorant_frame_classifier.py`

- [ ] **Step 1: Add failing data coverage tests**

```python
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "valorant_vision"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import train_export


def test_training_coverage_requires_fifty_per_class() -> None:
    counts = {label: 50 for label in train_export.LABELS}
    counts["replay"] = 49
    with pytest.raises(ValueError, match="replay=49"):
        train_export.validate_class_counts(counts, minimum=50, split="train")


def test_validation_coverage_requires_each_class() -> None:
    counts = {label: 1 for label in train_export.LABELS}
    counts["result"] = 0
    with pytest.raises(ValueError, match="result=0"):
        train_export.validate_class_counts(counts, minimum=1, split="val")


def test_int8_export_preserves_fp32_comparison_artifact() -> None:
    source = (SCRIPTS / "train_export.py").read_text(encoding="utf-8")
    assert "valorant_phase_v1.fp32.onnx" in source
    assert "shutil.copy2" in source
```

- [ ] **Step 2: Run the tests and verify RED**

```powershell
python -m pytest tests/test_valorant_frame_classifier.py -q
```

Expected: `validate_class_counts` is missing.

- [ ] **Step 3: Add the minimal count guard and call it in `main`**

```python
from collections import Counter


def validate_class_counts(counts: dict[str, int], *, minimum: int, split: str) -> None:
    missing = {label: int(counts.get(label, 0)) for label in LABELS if int(counts.get(label, 0)) < minimum}
    if missing:
        detail = ", ".join(f"{label}={count}" for label, count in missing.items())
        raise ValueError(f"{split} class coverage below {minimum}: {detail}")
```

After collecting samples:

```python
train_counts = Counter(LABELS[label_idx] for _, label_idx in train_samples)
val_counts = Counter(LABELS[label_idx] for _, label_idx in val_samples)
validate_class_counts(train_counts, minimum=50, split="train")
validate_class_counts(val_counts, minimum=1, split="val")
```

Print the `ValueError` to stderr and exit 1 before importing torch.

Before quantizing the runtime artifact, preserve the comparison graph:

```python
import shutil

fp32_path = out_dir / "valorant_phase_v1.fp32.onnx"
if export_int8:
    shutil.copy2(onnx_path, fp32_path)
```

The runtime contract still points to the quantized `valorant_phase_v1.onnx`; the `.fp32.onnx` file exists only for the fixed agreement check.

- [ ] **Step 4: Run classifier/export tests**

```powershell
python -m pytest tests/test_valorant_frame_classifier.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 5**

```powershell
git add scripts/valorant_vision/train_export.py tests/test_valorant_frame_classifier.py
git commit -m "fix: gate valorant training on class coverage"
```

### Task 6: Add reproducible manifest inference and held-out time ranges

**Files:**
- Create: `scripts/valorant_vision/predict_manifest.py`
- Create: `tests/test_valorant_predict_manifest.py`
- Modify: `scripts/valorant_vision/eval_blind.py`
- Modify: `tests/test_valorant_eval_gates.py`

- [ ] **Step 1: Write failing batch prediction test**

```python
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "valorant_vision"
sys.path.insert(0, str(SCRIPTS))

from predict_manifest import build_parser, predict_rows


class FakeClassifier:
    def predict_batch(self, frames):
        rows = np.zeros((len(frames), 5), dtype=np.float32)
        rows[:, 2] = 1.0
        return rows


def test_predict_rows_preserves_manifest_keys() -> None:
    rows = [{
        "video_id": "pov", "timestamp_sec": 12.5,
        "source_type": "pov", "label": "combat",
    }]
    predicted = predict_rows(rows, FakeClassifier(), load_frame=lambda _row: np.zeros((8, 8, 3), np.uint8))
    assert predicted == [{
        "video_id": "pov", "timestamp_sec": 12.5,
        "source_type": "pov", "predicted_label": "combat",
    }]


def test_predict_parser_accepts_test_split() -> None:
    args = build_parser().parse_args([
        "--manifest", "manifest.jsonl", "--data-dir", "dataset",
        "--model-dir", "model", "--split", "test", "--output", "predictions.jsonl",
    ])
    assert args.split == "test"
```

- [ ] **Step 2: Add failing held-out range test**

Append to `tests/test_valorant_eval_gates.py`:

```python
def test_full_video_manifest_passes_time_range_to_detector(tmp_path) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    calls = []

    def detector(path, **kwargs):
        calls.append((path, kwargs))
        return []

    eval_blind.build_rounds_from_videos([{
        "video_id": "v", "video_path": str(video),
        "time_range": [800.0, 1000.0], "ground_truth": [],
    }], detector=detector, model_dir=tmp_path)

    assert calls[0][1]["time_range"] == (800.0, 1000.0)
```

- [ ] **Step 3: Run both tests and verify RED**

```powershell
python -m pytest tests/test_valorant_predict_manifest.py tests/test_valorant_eval_gates.py -q
```

Expected: missing `predict_manifest` and missing detector `time_range` argument.

- [ ] **Step 4: Implement batch prediction**

```python
CLASS_NAMES = ("non_game", "buy", "combat", "result", "replay")


def predict_rows(rows: list[dict], classifier, *, load_frame, batch_size: int = 64) -> list[dict]:
    output: list[dict] = []
    for offset in range(0, len(rows), batch_size):
        batch_rows = rows[offset:offset + batch_size]
        frames = [load_frame(row) for row in batch_rows]
        probabilities = classifier.predict_batch(frames)
        for row, probs in zip(batch_rows, probabilities, strict=True):
            output.append({
                "video_id": row["video_id"],
                "timestamp_sec": float(row["timestamp_sec"]),
                "source_type": row["source_type"],
                "predicted_label": CLASS_NAMES[int(np.argmax(probs))],
            })
    return output
```

CLI arguments: `--manifest`, `--data-dir`, `--model-dir`, `--split` and `--output`. Filter manifest rows to the requested split before calling `predict_rows`. Load the JPEG using `extract_frames.output_path_for(ManifestRow.from_dict(row), data_dir)` and `cv2.imread`; fail with the exact path when decoding returns `None`.

- [ ] **Step 5: Pass optional held-out ranges to the full-video detector**

In `build_rounds_from_videos`:

```python
detector_kwargs = {
    "model_dir": model_dir,
    "session_id": session_id,
}
if record.get("time_range") is not None:
    start, end = record["time_range"]
    detector_kwargs["time_range"] = (float(start), float(end))
for row in detector(video_path, **detector_kwargs):
    start_sec = float(row.get("start", row.get("start_sec", 0.0)))
    end_sec = float(row.get("end", row.get("end_sec", 0.0)))
    listed = (
        row.get("boundary_source") == "valorant_hybrid_v1"
        and row.get("confirm_status") in ("vision_confirmed", "pending")
        and end_sec > start_sec
    )
    predictions.append({**row, "video_id": video_id, "listed": listed})
```

Reject a range unless `0 <= start < end`.

- [ ] **Step 6: Run prediction and gate tests**

```powershell
python -m pytest tests/test_valorant_predict_manifest.py tests/test_valorant_eval_gates.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit Task 6**

```powershell
git add scripts/valorant_vision/predict_manifest.py scripts/valorant_vision/eval_blind.py tests/test_valorant_predict_manifest.py tests/test_valorant_eval_gates.py
git commit -m "feat: evaluate valorant model on held-out ranges"
```

### Task 7: Pass Valorant model and shadow settings through Electron

**Files:**
- Modify: `lsc-electron/electron/main.ts:282-303`
- Create: `tests/test_electron_backend_env.py`

- [ ] **Step 1: Write failing source-level whitelist test**

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_electron_passes_valorant_runtime_env_to_backend() -> None:
    source = (ROOT / "lsc-electron/electron/main.ts").read_text(encoding="utf-8")
    safe_env = source.split("const safeEnv", 1)[1].split("backendProcess = spawn", 1)[0]
    assert "LSC_VALORANT_MODEL_DIR" in safe_env
    assert "LSC_VALORANT_VISION_SHADOW" in safe_env
```

- [ ] **Step 2: Run the test and verify RED**

```powershell
python -m pytest tests/test_electron_backend_env.py -q
```

Expected: assertion failure for both missing names.

- [ ] **Step 3: Add only the two required variables to the whitelist**

```typescript
  for (const key of ['LSC_VALORANT_MODEL_DIR', 'LSC_VALORANT_VISION_SHADOW'] as const) {
    if (process.env[key]) {
      safeEnv[key] = process.env[key]
    }
  }
```

Place this after `LSC_CONFIG_PATH` handling and before spawning the backend.

- [ ] **Step 4: Run focused and TypeScript checks**

```powershell
python -m pytest tests/test_electron_backend_env.py -q
cd lsc-electron
npm exec tsc -- --noEmit
```

Expected: the focused test passes. TypeScript may still report only the five documented pre-existing errors; no new error may mention `electron/main.ts` or `safeEnv`.

- [ ] **Step 5: Commit Task 7**

```powershell
git add lsc-electron/electron/main.ts tests/test_electron_backend_env.py
git commit -m "fix: pass valorant vision settings to backend"
```

### Task 8: Prepare and label the two local videos

**Files:**
- Create locally, ignored: `datasets/valorant_phase/raw/pov.<extension>`
- Create locally, ignored: `datasets/valorant_phase/raw/broadcast.<extension>`
- Create locally, ignored: `datasets/valorant_phase/annotations/sessions.json`
- Create locally, ignored: `datasets/valorant_phase/annotations/intervals.json`
- Create locally, ignored: `datasets/valorant_phase/annotations/queue.json`
- Create locally, ignored: `datasets/valorant_phase/annotations/labels.json`
- Create locally, ignored: `datasets/valorant_phase/annotations/manifest.jsonl`

- [ ] **Step 1: Put the videos in the agreed ignored directory and verify decoding**

```powershell
ffprobe -v error -show_entries format=duration:stream=width,height,r_frame_rate -of json datasets/valorant_phase/raw/pov.mp4
ffprobe -v error -show_entries format=duration:stream=width,height,r_frame_rate -of json datasets/valorant_phase/raw/broadcast.mp4
```

Expected: both commands exit 0, report positive duration, and contain one video stream. If the actual extensions differ, preserve the files and use those exact names in `sessions.json`.

- [ ] **Step 2: Extract coarse frames at one frame per four seconds**

```powershell
ffmpeg -hide_banner -loglevel error -i datasets/valorant_phase/raw/pov.mp4 -vf fps=1/4 -q:v 2 datasets/valorant_phase/annotations/frames/pov/frame_%06d.jpg
ffmpeg -hide_banner -loglevel error -i datasets/valorant_phase/raw/broadcast.mp4 -vf fps=1/4 -q:v 2 datasets/valorant_phase/annotations/frames/broadcast/frame_%06d.jpg
```

Expected: each output directory contains roughly `duration / 4` non-empty JPEGs.

- [ ] **Step 3: Create the exact two-session configuration**

```json
[
  {
    "video_id": "pov_pilot_20260720",
    "video_path": "datasets/valorant_phase/raw/pov.mp4",
    "frame_dir": "datasets/valorant_phase/annotations/frames/pov",
    "source_type": "pov",
    "session_id": "pov_pilot_20260720"
  },
  {
    "video_id": "broadcast_pilot_20260720",
    "video_path": "datasets/valorant_phase/raw/broadcast.mp4",
    "frame_dir": "datasets/valorant_phase/annotations/frames/broadcast",
    "source_type": "broadcast",
    "session_id": "broadcast_pilot_20260720"
  }
]
```

- [ ] **Step 4: Build the coarse queue**

```powershell
python scripts/valorant_vision/build_label_queue.py --sessions datasets/valorant_phase/annotations/sessions.json --frame-root datasets/valorant_phase/annotations --output datasets/valorant_phase/annotations/queue.json --interval 4 --gap-sec 30
```

Expected: queue is non-empty and both `source_type` values occur in train, val, and test.
The generated split must match the approved 65% train / 15% val / 20% test time blocks with a 30-second exclusion gap around both boundaries.

- [ ] **Step 5: Produce contact sheets and write reviewed coarse intervals**

```powershell
ffmpeg -hide_banner -loglevel error -framerate 1 -i datasets/valorant_phase/annotations/frames/pov/frame_%06d.jpg -vf "scale=384:-1,tile=5x4:padding=4:margin=4" -vsync 0 datasets/valorant_phase/annotations/pov_sheet_%03d.jpg
ffmpeg -hide_banner -loglevel error -framerate 1 -i datasets/valorant_phase/annotations/frames/broadcast/frame_%06d.jpg -vf "scale=384:-1,tile=5x4:padding=4:margin=4" -vsync 0 datasets/valorant_phase/annotations/broadcast_sheet_%03d.jpg
```

Inspect sheets in chronological order. Write half-open intervals covering every unambiguous coarse frame with only the five approved labels. Leave ambiguous spans absent rather than guessing. Use the visual companion for adjacent frames whose state cannot be determined from one sheet.

- [ ] **Step 6: Apply candidates, review every frame in the UI, and export coarse manifest**

```powershell
python scripts/valorant_vision/apply_interval_labels.py --queue datasets/valorant_phase/annotations/queue.json --intervals datasets/valorant_phase/annotations/intervals.json --labels datasets/valorant_phase/annotations/labels.json --manifest datasets/valorant_phase/annotations/manifest.jsonl
python scripts/valorant_vision/serve_label_ui.py --root datasets/valorant_phase/annotations --queue datasets/valorant_phase/annotations/queue.json --labels datasets/valorant_phase/annotations/labels.json --manifest datasets/valorant_phase/annotations/manifest.jsonl --port 8765
```

Expected: UI reports zero entries whose `annotator` is not `human` before export; manifest contains only human-reviewed decisions transformed into the existing manifest schema. Reviewing every coarse frame exceeds the spec's minimum 10% stable-frame audit.

- [ ] **Step 7: Densify transitions and complete a second review pass**

```powershell
python scripts/valorant_vision/densify_boundaries.py --queue datasets/valorant_phase/annotations/queue.json --labels datasets/valorant_phase/annotations/labels.json --sessions datasets/valorant_phase/annotations/sessions.json --output-root datasets/valorant_phase/annotations/frames_dense --radius 3 --step 0.5
python scripts/valorant_vision/serve_label_ui.py --root datasets/valorant_phase/annotations --queue datasets/valorant_phase/annotations/queue.json --labels datasets/valorant_phase/annotations/labels.json --manifest datasets/valorant_phase/annotations/manifest.jsonl --port 8765
```

Expected: all new dense frames are reviewed; every observed transition is represented by dense samples on both sides where the video contains them.

- [ ] **Step 8: Audit label counts and materialize the image dataset**

```powershell
python scripts/valorant_vision/extract_frames.py datasets/valorant_phase/annotations/manifest.jsonl --output-dir datasets/valorant_phase --skip-existing
python -c "import json,collections,pathlib; rows=[json.loads(x) for x in pathlib.Path(r'datasets/valorant_phase/annotations/manifest.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()]; print(collections.Counter((r['split'],r['source_type'],r['label']) for r in rows))"
```

Expected: train has at least 50 reviewed frames for every class; val and test each contain both sources and every class present in the corresponding held-out time block. If a class is absent or below 50 in train, stop and request the missing footage instead of duplicating samples.

No Git commit: all Task 8 outputs are intentionally ignored local data.

### Task 9: Train, export and evaluate the pilot model

**Files:**
- Create locally, ignored: `datasets/valorant_phase/model/valorant_phase_v1.onnx`
- Create locally, ignored: `datasets/valorant_phase/model/valorant_phase_v1.json`
- Create locally, ignored: `datasets/valorant_phase/reports/predictions_test.jsonl`
- Create locally, ignored: `datasets/valorant_phase/reports/round_manifest.json`
- Create locally, ignored: `datasets/valorant_phase/reports/pilot_eval.json`

- [ ] **Step 1: Verify training dependencies before consuming GPU/CPU time**

```powershell
python -c "import torch,torchvision,onnx,onnxruntime; print(torch.__version__, torchvision.__version__, onnx.__version__, onnxruntime.__version__)"
```

Expected: exit 0. If a module is absent, report it and request installation approval; do not silently change the environment.

- [ ] **Step 2: Train and export the runtime INT8 artifact**

```powershell
python scripts/valorant_vision/train_export.py --data-dir datasets/valorant_phase --out-dir datasets/valorant_phase/model --epochs 10 --export-int8
```

Expected: runtime ONNX, comparison FP32 ONNX and JSON exist; JSON SHA-256 matches the runtime ONNX file; training printed a non-empty validation result.

- [ ] **Step 3: Run the runtime classifier over held-out frame rows**

```powershell
python scripts/valorant_vision/predict_manifest.py --manifest datasets/valorant_phase/annotations/manifest.jsonl --data-dir datasets/valorant_phase --model-dir datasets/valorant_phase/model --split test --output datasets/valorant_phase/reports/predictions_test.jsonl
```

Expected: one prediction for every test manifest row and no train/val row in the output.

- [ ] **Step 4: Freeze round ground truth for each held-out tail**

Create `round_manifest.json` with exactly two records. Each record contains its `video_id`, `video_path`, `session_id`, the test block `[start_sec, end_sec]` as `time_range`, and every complete round inside that range as `{start_sec, end_sec}`. Exclude a round crossing the start or end of the held-out range.

- [ ] **Step 5: Run frame and full-video internal evaluation**

```powershell
python scripts/valorant_vision/eval_blind.py --predictions datasets/valorant_phase/reports/predictions_test.jsonl --labels datasets/valorant_phase/annotations/manifest.jsonl --round-manifest datasets/valorant_phase/reports/round_manifest.json --model-dir datasets/valorant_phase/model --output datasets/valorant_phase/reports/pilot_eval.json
```

Expected pilot gates in the saved report: Macro F1 ≥ 0.90; buy/result precision ≥ 0.93; non_game/replay recall ≥ 0.90; round recall ≥ 0.80; listed precision ≥ 0.90; confirmed boundary error P95 ≤ 2 seconds. Do not use `--enforce-gates`, because that switch applies stricter production thresholds.

- [ ] **Step 6: Check FP32-to-INT8 agreement using the preserved FP32 export**

Build a temporary classifier directory with the preserved graph and a matching SHA-256 contract:

```powershell
New-Item -ItemType Directory -Force datasets/valorant_phase/model-fp32
Copy-Item datasets/valorant_phase/model/valorant_phase_v1.fp32.onnx datasets/valorant_phase/model-fp32/valorant_phase_v1.onnx
$meta = Get-Content datasets/valorant_phase/model/valorant_phase_v1.json -Raw | ConvertFrom-Json
$meta.sha256 = (Get-FileHash datasets/valorant_phase/model-fp32/valorant_phase_v1.onnx -Algorithm SHA256).Hash.ToLowerInvariant()
$json = $meta | ConvertTo-Json -Depth 10
[System.IO.File]::WriteAllText((Resolve-Path 'datasets/valorant_phase/model-fp32').Path + '\valorant_phase_v1.json', $json, [System.Text.UTF8Encoding]::new($false))
python scripts/valorant_vision/predict_manifest.py --manifest datasets/valorant_phase/annotations/manifest.jsonl --data-dir datasets/valorant_phase --model-dir datasets/valorant_phase/model-fp32 --split test --output datasets/valorant_phase/reports/predictions_test_fp32.jsonl
python -c "import json,pathlib; a=[json.loads(x)['predicted_label'] for x in pathlib.Path(r'datasets/valorant_phase/reports/predictions_test.jsonl').read_text(encoding='utf-8').splitlines()]; b=[json.loads(x)['predicted_label'] for x in pathlib.Path(r'datasets/valorant_phase/reports/predictions_test_fp32.jsonl').read_text(encoding='utf-8').splitlines()]; print(sum(x==y for x,y in zip(a,b,strict=True))/len(a))"
```

Expected: printed agreement is at least `0.98`. Keep both prediction files in the ignored report directory.

If any pilot metric fails, return to train/val errors only. Once test results have been viewed, do not tune on them and reuse the same test as blind evidence.

No Git commit: model and reports remain ignored local artifacts.

### Task 10: Run regression and shadow acceptance

**Files:**
- Modify: `docs/superpowers/plans/baselines/2026-07-20-hybrid-vision-notes.md`
- Create locally, ignored: `datasets/valorant_phase/reports/shadow_run.md`

- [ ] **Step 1: Run focused tests**

```powershell
python -m pytest tests/test_valorant_labeling_tools.py tests/test_valorant_predict_manifest.py tests/test_valorant_frame_classifier.py tests/test_valorant_eval_gates.py tests/test_electron_backend_env.py tests/test_valorant_hybrid_detect.py tests/test_hybrid_vision_lifecycle.py -q
```

Expected: all focused tests pass.

- [ ] **Step 2: Run the full Python suite and diff checks**

```powershell
python -m pytest -q
git diff --check
```

Expected: no hybrid-related failure and no whitespace error. The only allowed pytest failures are the four individually documented pre-existing frontend guards; record the new pass count.

- [ ] **Step 3: Start current source in shadow mode**

```powershell
$env:LSC_VALORANT_MODEL_DIR = (Resolve-Path 'datasets/valorant_phase/model').Path
$env:LSC_VALORANT_VISION_SHADOW = '1'
Set-Location lsc-electron
npm run dev
```

Expected: Electron starts the current source backend, status reports `shadow_mode=true`, the runtime model version and provider are non-empty, and no model-contract error appears.

- [ ] **Step 4: Observe one POV and one broadcast analysis session**

For each source, record detected rounds, listable rounds, vision-confirmed rounds, analysis lag, provider, latest error and boundary examples in `shadow_run.md`. Verify that the UI/backend emits no `clip_queued` while shadow is enabled.

- [ ] **Step 5: Verify stop behavior and orphan cleanup**

Stop continuous analysis during an active scan. Measure time from stop request to idle, then run:

```powershell
Get-Process ffmpeg -ErrorAction SilentlyContinue | Select-Object Id,ProcessName,StartTime
```

Expected: idle within 3 seconds for the current short batch and no FFmpeg process owned by this validation session remains. Do not terminate unrelated FFmpeg processes; correlate by start time and backend log first.

- [ ] **Step 6: Update the regression baseline and commit**

Record exact focused/full test counts, the five pre-existing TypeScript errors if unchanged, pilot metric results, shadow observations, stop latency and remaining formal-release blockers.

```powershell
git add docs/superpowers/plans/baselines/2026-07-20-hybrid-vision-notes.md
git commit -m "docs: record valorant pilot model validation"
```

### Task 11: Final review and handoff

**Files:**
- No new files unless a verified defect requires a separate TDD fix.

- [ ] **Step 1: Verify intended commits and preserve unrelated dirty state**

```powershell
git status --short
git log --oneline 19d2cf2..HEAD
```

Expected: only the planned source/test/docs files are committed. User-owned HUD spec, room configs and `.worktrees/` remain untouched; no raw video, frame, label, model or report artifact is tracked.

- [ ] **Step 2: Report the evidence boundary**

Final handoff must separate:

- confirmed: local two-source pilot metrics and shadow behavior;
- not confirmed: cross-session generalization and production thresholds;
- next data request: at least two additional independent POV sessions and two additional independent broadcast sessions before formal blind testing.
