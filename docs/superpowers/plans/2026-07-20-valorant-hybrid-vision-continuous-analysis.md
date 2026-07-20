# 无畏契约混合视觉持续分析 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用轻量 ONNX 五分类 + 顶部数字锚点 + 确定性时间状态机，替换 Valorant 持续分析中的音频/文字 OCR/固定 HUD 阈值边界路径，使赛事转播与第一视角都能稳定切出完整回合并真实可取消。

**Architecture:** `valorant_frame_classifier` 只做模型契约与批量推理；`valorant_round_fsm` 只融合时间证据；`round_detector` 负责可取消抽帧、按需数字 OCR、粗扫/密扫；`room_handler` 只保留游标、`stopping` 生命周期、入列门禁与多房映射。训练脚本离线产出 INT8 ONNX；运行时仅依赖已有 onnxruntime。

**Tech Stack:** Python 3.12, onnxruntime, OpenCV/numpy, FFmpeg, RapidOCR（仅顶部数字）, pytest, React/TypeScript（`vision_confirmed`）

**Spec:** `docs/superpowers/specs/2026-07-20-valorant-hybrid-vision-continuous-analysis-design.md`

**Supersedes:** `docs/superpowers/plans/2026-07-19-valorant-hud-round-boundary.md` 与对应 HUD 固定 ROI 设计。不得把 `.worktrees/valorant-hud-boundary` 整树覆盖到主分支；仅可选择性复用已验证的通用链路片段。

---

## 审阅锁定（实现不得回退）

| 决策 | 选择 |
|------|------|
| 边界权威 | 仅混合视觉：模型五分类 + 数字锚点 + 时间状态机 |
| 禁止回退 | Valorant 边界不得回退音频 RMS、旧文字状态 OCR、固定 HUD 阈值 |
| 起点 | 买枪结束后第一帧稳定 `combat`；不含买枪 |
| 终点 | 回合结束事件后默认 +1.5s；遇 Replay/non_game/下一买枪则提前截断 |
| 强制闭合 | **禁止**；开放 >150s 丢弃并重锚；取消/停分析丢弃开放尾 |
| 入列 | `list_only=True`；`vision_confirmed` 可导但不自动 FFmpeg；`pending` 待确认 |
| 失败 | 识别失败只计诊断，不 `clip_queued` |
| FFmpeg | 持续视觉扫描不得用最终阻塞 `run_hidden(timeout=...)`；必须可终止进程树 |
| 信号量 | `_analysis_semaphore` 仅在 worker/FFmpeg/推理批次全部退出后释放 |
| 停止 | `stopping` → 真实退出 → `idle`；禁止 `task.cancel()` 后立刻报已停止 |
| 模型缺失 | 禁用本次 Valorant 自动切片并报错，不猜默认、不回退旧算法 |
| 工作树 | 从当前分支建基线；HUD worktree 不整树合并 |
| Shadow | 发布前可 shadow；切换主路径后删除兼容分支，不长期双开关 |

---

## 文件结构

| 文件 | 职责 |
|------|------|
| **Create** `lsc/analyzer/valorant_frame_classifier.py` | 模型元数据校验、懒加载 session、批量预处理与五分类推理 |
| **Create** `lsc/analyzer/valorant_round_fsm.py` | 纯时间证据状态机（无图像/FFmpeg/OCR） |
| **Create** `lsc/analyzer/models/valorant_phase_v1.json` | 契约元数据（含阈值版本）；真实 ONNX 由训练任务产出 |
| **Create** `lsc/analyzer/models/.gitkeep` | 模型目录占位；真实 `valorant_phase_v1.onnx` 由训练导出（大文件可用 Git LFS 或发布产物） |
| **Create** `lsc/utils/cancellable_ffmpeg.py` | 可观察、可终止的抽帧子进程封装 |
| **Modify** `lsc/utils/process_launcher.py` | 导出/复用 `prepare_launch`、`hidden_run_kwargs` 给 cancellable 路径 |
| **Modify** `lsc/analyzer/ocr_accel.py` | 复用 provider 顺序；classifier 通过同一候选列表选 EP |
| **Modify** `lsc/analyzer/round_detector.py` | 混合视觉粗扫/密扫入口；Valorant 主路径切断音频/旧 OCR 边界 |
| **Modify** `python-backend/handlers/room_handler.py` | 游标、stopping、vision 门禁、入列、禁 trim、禁旧升格 |
| **Modify** `python-backend/handlers/room_utils.py` | `vision_confirmed` 纳入可导判断 |
| **Modify** `lsc-electron/src/types/index.ts` | `ClipConfirmStatus` + 证据可选字段 |
| **Modify** `lsc-electron/src/pages/Workbench/components/ClipList.tsx` | 视觉确认标签与可导 |
| **Modify** `lsc-electron/src/pages/Workbench/index.tsx` | 快捷键可导与 upsert 合并 |
| **Create** `scripts/valorant_vision/manifest_schema.md` | 训练清单字段说明 |
| **Create** `scripts/valorant_vision/extract_frames.py` | 按清单/边界密度抽帧 |
| **Create** `scripts/valorant_vision/train_export.py` | 训练 + INT8 ONNX 导出 |
| **Create** `scripts/valorant_vision/eval_blind.py` | 混淆矩阵与完整录像盲测门 |
| **Create** `tests/test_valorant_frame_classifier.py` | 契约/加载/回退 |
| **Create** `tests/test_valorant_round_fsm.py` | 纯状态机 |
| **Create** `tests/test_valorant_dense_refine.py` | 密扫边界语义 |
| **Create** `tests/test_cancellable_ffmpeg.py` | 可取消抽帧行为 |
| **Create** `tests/test_hybrid_vision_lifecycle.py` | stopping/信号量/无孤儿进程 |
| **Create** `tests/fixtures/valorant_vision/` | 最小 stub ONNX + 元数据 + 合成证据序列 |
| **Modify** `tests/test_continuous_analysis_guards.py` | 入列门禁与禁旧路径 |
| **Modify** `tests/test_ux_habit_guards.py` / `test_frontend_stability_guards.py` | `vision_confirmed` 守卫 |

**并行说明：** Task 1–9 可用 stub ONNX 推进运行时；Task 10–12 为离线数据/训练/评估，可与运行时并行；Task 13–15 为切换与清理，依赖真实模型通过盲测门。

---

### Task 1: 模型元数据契约与 stub fixture

**Files:**
- Create: `lsc/analyzer/models/.gitkeep`
- Create: `tests/fixtures/valorant_vision/valorant_phase_v1.json`
- Create: `scripts/valorant_vision/make_stub_onnx.py`
- Create: `tests/fixtures/valorant_vision/valorant_phase_v1.onnx`（由脚本生成）
- Create: `tests/test_valorant_frame_classifier.py`（先写契约测试，实现在 Task 2）

- [ ] **Step 1: 写失败测试（元数据 schema）**

```python
# tests/test_valorant_frame_classifier.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "valorant_vision"
META_PATH = FIXTURE_DIR / "valorant_phase_v1.json"

REQUIRED_META_KEYS = {
    "model_version",
    "class_names",
    "input_size",
    "color_order",
    "normalize_mean",
    "normalize_std",
    "threshold_version",
    "sha256",
    "dataset_version",
    "thresholds",
}


def test_fixture_metadata_has_required_keys() -> None:
    meta = json.loads(META_PATH.read_text(encoding="utf-8"))
    missing = REQUIRED_META_KEYS - set(meta)
    assert not missing, missing
    assert meta["class_names"] == [
        "non_game", "buy", "combat", "result", "replay",
    ]
    assert meta["input_size"] == [224, 224]
    assert meta["color_order"] == "RGB"
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_valorant_frame_classifier.py::test_fixture_metadata_has_required_keys -v`

Expected: FAIL（fixture 不存在）

- [ ] **Step 3: 写 stub 元数据与生成脚本**

```json
{
  "model_version": "valorant_phase_v1_stub",
  "class_names": ["non_game", "buy", "combat", "result", "replay"],
  "input_size": [224, 224],
  "color_order": "RGB",
  "normalize_mean": [0.485, 0.456, 0.406],
  "normalize_std": [0.229, 0.224, 0.225],
  "threshold_version": "v1",
  "sha256": "REPLACE_AFTER_EXPORT",
  "dataset_version": "stub-0",
  "thresholds": {
    "stable_prob": 0.55,
    "high_prob": 0.80
  }
}
```

```python
# scripts/valorant_vision/make_stub_onnx.py
"""生成测试用最小五分类 ONNX，并回填 sha256 到同目录 json。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np


def main() -> None:
    try:
        import onnx
        from onnx import TensorProto, helper, numpy_helper
    except ImportError as exc:
        raise SystemExit("需要 onnx 包以生成 stub") from exc

    out_dir = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "valorant_vision"
    out_dir.mkdir(parents=True, exist_ok=True)
    onnx_path = out_dir / "valorant_phase_v1.onnx"
    meta_path = out_dir / "valorant_phase_v1.json"

    # input [N,3,224,224] -> GlobalAveragePool -> Gemm -> Softmax
    x = helper.make_tensor_value_info("input", TensorProto.FLOAT, ["N", 3, 224, 224])
    y = helper.make_tensor_value_info("probs", TensorProto.FLOAT, ["N", 5])
    gap = helper.make_node("GlobalAveragePool", ["input"], ["pooled"])
    # pooled [N,3,1,1] -> flatten via reshape handled by Gemm on squeezed; use ReduceMean trick:
    # simpler: Flatten + Gemm
    flat = helper.make_node("Flatten", ["input"], ["flat"], axis=1)
    w = numpy_helper.from_array(np.zeros((5, 3 * 224 * 224), dtype=np.float32), name="W")
    b = numpy_helper.from_array(
        np.array([0.0, 0.0, 10.0, 0.0, 0.0], dtype=np.float32), name="B"
    )  # bias toward combat
    gemm = helper.make_node("Gemm", ["flat", "W", "B"], ["logits"], alpha=1.0, beta=1.0, transB=1)
    sm = helper.make_node("Softmax", ["logits"], ["probs"], axis=1)
    graph = helper.make_graph([flat, gemm, sm], "valorant_stub", [x], [y], [w, b])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    onnx.checker.check_model(model)
    onnx.save(model, str(onnx_path))

    digest = hashlib.sha256(onnx_path.read_bytes()).hexdigest()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["sha256"] = digest
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {onnx_path} sha256={digest}")


if __name__ == "__main__":
    main()
```

先写 json（`sha256` 暂占位），再运行：

```bash
python scripts/valorant_vision/make_stub_onnx.py
```

- [ ] **Step 4: 再跑契约测试**

Run: `pytest tests/test_valorant_frame_classifier.py::test_fixture_metadata_has_required_keys -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add lsc/analyzer/models/.gitkeep tests/fixtures/valorant_vision scripts/valorant_vision/make_stub_onnx.py tests/test_valorant_frame_classifier.py
git commit -m "test: add valorant vision model metadata fixture and stub ONNX generator"
```

---

### Task 2: `ValorantFrameClassifier` 加载与批量推理

**Files:**
- Create: `lsc/analyzer/valorant_frame_classifier.py`
- Modify: `tests/test_valorant_frame_classifier.py`
- Modify: `lsc/analyzer/ocr_accel.py`（仅复用 `list_accel_candidates` / provider 名映射，不改 OCR 行为）

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/test_valorant_frame_classifier.py
import numpy as np

from lsc.analyzer.valorant_frame_classifier import (
    ModelContractError,
    ValorantFrameClassifier,
)


def test_load_rejects_sha_mismatch(tmp_path: Path) -> None:
    meta = json.loads(META_PATH.read_text(encoding="utf-8"))
    bad = tmp_path / "valorant_phase_v1.json"
    onnx = FIXTURE_DIR / "valorant_phase_v1.onnx"
    meta["sha256"] = "0" * 64
    bad.write_text(json.dumps(meta), encoding="utf-8")
    clf = ValorantFrameClassifier(model_dir=tmp_path)
    # copy onnx next to bad meta
    (tmp_path / "valorant_phase_v1.onnx").write_bytes(onnx.read_bytes())
    with pytest.raises(ModelContractError, match="sha256"):
        clf.load()


def test_predict_batch_returns_five_probs() -> None:
    clf = ValorantFrameClassifier(model_dir=FIXTURE_DIR)
    clf.load()
    frames = [np.zeros((240, 320, 3), dtype=np.uint8) for _ in range(2)]
    out = clf.predict_batch(frames)
    assert out.shape == (2, 5)
    assert np.allclose(out.sum(axis=1), 1.0, atol=1e-3)


def test_missing_model_raises_diagnostic_error(tmp_path: Path) -> None:
    clf = ValorantFrameClassifier(model_dir=tmp_path)
    with pytest.raises(ModelContractError, match="missing"):
        clf.load()
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_valorant_frame_classifier.py -v`

Expected: FAIL（`ValorantFrameClassifier` 未定义）

- [ ] **Step 3: 最小实现**

```python
# lsc/analyzer/valorant_frame_classifier.py
from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any

import numpy as np

from lsc.analyzer.ocr_accel import list_accel_candidates

_log = logging.getLogger(__name__)

_CLASS_NAMES = ("non_game", "buy", "combat", "result", "replay")
_DEFAULT_DIR = Path(__file__).resolve().parent / "models"


class ModelContractError(RuntimeError):
    """模型文件或元数据契约不匹配。"""


def _provider_for_accel(accel: str) -> str:
    return {
        "dml": "DmlExecutionProvider",
        "cuda": "CUDAExecutionProvider",
        "cpu": "CPUExecutionProvider",
    }[accel]


class ValorantFrameClassifier:
    """无畏契约五分类包装器：只负责校验、加载与推理。"""

    def __init__(self, model_dir: Path | None = None) -> None:
        self._dir = Path(model_dir) if model_dir else _DEFAULT_DIR
        self._session: Any = None
        self._meta: dict[str, Any] | None = None
        self._provider: str | None = None
        self._lock = threading.Lock()

    @property
    def model_version(self) -> str:
        if not self._meta:
            raise ModelContractError("model not loaded")
        return str(self._meta["model_version"])

    @property
    def provider(self) -> str | None:
        return self._provider

    @property
    def thresholds(self) -> dict[str, float]:
        if not self._meta:
            raise ModelContractError("model not loaded")
        return dict(self._meta["thresholds"])

    def load(self) -> None:
        with self._lock:
            onnx_path = self._dir / "valorant_phase_v1.onnx"
            meta_path = self._dir / "valorant_phase_v1.json"
            if not onnx_path.is_file() or not meta_path.is_file():
                raise ModelContractError(f"missing model or metadata under {self._dir}")
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            self._validate_meta(meta)
            digest = hashlib.sha256(onnx_path.read_bytes()).hexdigest()
            if digest.lower() != str(meta["sha256"]).lower():
                raise ModelContractError("sha256 mismatch")
            self._session = self._create_session(onnx_path)
            self._meta = meta

    def _validate_meta(self, meta: dict[str, Any]) -> None:
        required = {
            "model_version", "class_names", "input_size", "color_order",
            "normalize_mean", "normalize_std", "threshold_version",
            "sha256", "dataset_version", "thresholds",
        }
        missing = required - set(meta)
        if missing:
            raise ModelContractError(f"metadata missing keys: {sorted(missing)}")
        if list(meta["class_names"]) != list(_CLASS_NAMES):
            raise ModelContractError("class_names mismatch")
        if list(meta["input_size"]) != [224, 224]:
            raise ModelContractError("input_size mismatch")
        if meta["color_order"] != "RGB":
            raise ModelContractError("color_order mismatch")

    def _create_session(self, onnx_path: Path) -> Any:
        import onnxruntime as ort

        last_err: Exception | None = None
        for accel in list_accel_candidates():  # dml/cuda (if any) then cpu
            provider = _provider_for_accel(accel)
            try:
                sess = ort.InferenceSession(
                    str(onnx_path), providers=[provider, "CPUExecutionProvider"]
                )
                self._provider = sess.get_providers()[0]
                _log.info("valorant classifier provider=%s", self._provider)
                return sess
            except Exception as exc:  # noqa: BLE001 — 尝试下一 provider
                last_err = exc
                _log.warning("provider %s failed: %s", provider, exc)
        raise ModelContractError(f"failed to init onnx session: {last_err}")

    def predict_batch(self, frames_bgr: list[np.ndarray]) -> np.ndarray:
        if self._session is None or self._meta is None:
            self.load()
        assert self._session is not None and self._meta is not None
        if not frames_bgr:
            return np.zeros((0, 5), dtype=np.float32)
        batch = np.stack([self._preprocess(f) for f in frames_bgr], axis=0)
        input_name = self._session.get_inputs()[0].name
        probs = self._session.run(None, {input_name: batch})[0]
        return np.asarray(probs, dtype=np.float32)

    def _preprocess(self, frame_bgr: np.ndarray) -> np.ndarray:
        assert self._meta is not None
        import cv2

        h, w = frame_bgr.shape[:2]
        size = int(self._meta["input_size"][0])
        scale = size / max(h, w)
        nh, nw = max(1, int(round(h * scale))), max(1, int(round(w * scale)))
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        resized = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_AREA)
        canvas = np.zeros((size, size, 3), dtype=np.uint8)
        y0 = (size - nh) // 2
        x0 = (size - nw) // 2
        canvas[y0 : y0 + nh, x0 : x0 + nw] = resized
        x = canvas.astype(np.float32) / 255.0
        mean = np.asarray(self._meta["normalize_mean"], dtype=np.float32)
        std = np.asarray(self._meta["normalize_std"], dtype=np.float32)
        x = (x - mean) / std
        return np.transpose(x, (2, 0, 1))
```

- [ ] **Step 4: 运行测试通过**

Run: `pytest tests/test_valorant_frame_classifier.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add lsc/analyzer/valorant_frame_classifier.py tests/test_valorant_frame_classifier.py
git commit -m "feat: add valorant ONNX frame classifier with strict metadata contract"
```

---

### Task 3: 纯时间状态机 `valorant_round_fsm`

**Files:**
- Create: `lsc/analyzer/valorant_round_fsm.py`
- Create: `tests/test_valorant_round_fsm.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_valorant_round_fsm.py
from __future__ import annotations

from lsc.analyzer.valorant_round_fsm import (
    FrameEvidence,
    RoundFSM,
    RoundFSMConfig,
)


def _ev(ts: float, cls: str, *, p: float = 0.9, left=None, right=None, timer=None) -> FrameEvidence:
    probs = {c: 0.02 for c in ("non_game", "buy", "combat", "result", "replay")}
    probs[cls] = p
    return FrameEvidence(
        timestamp=ts,
        class_probabilities=probs,
        predicted_class=cls,
        timer_seconds=timer,
        left_score=left,
        right_score=right,
        model_version="stub",
    )


def test_full_buy_combat_result_closes_one_round() -> None:
    fsm = RoundFSM(RoundFSMConfig(coarse_stable_frames=2, max_open_sec=150.0))
    out = []
    # buy
    for t in (1.0, 2.0):
        out.extend(fsm.feed(_ev(t, "buy")))
    # combat
    for t in (3.0, 4.0):
        out.extend(fsm.feed(_ev(t, "combat")))
    # result + score +1
    for t in (40.0, 41.0):
        out.extend(fsm.feed(_ev(t, "result", left=1, right=0)))
    closed = [e for e in out if e.kind == "closed"]
    assert len(closed) == 1
    assert closed[0].start == 3.0
    assert closed[0].confirm_status in {"vision_confirmed", "pending"}


def test_single_frame_jitter_does_not_transition() -> None:
    fsm = RoundFSM(RoundFSMConfig(coarse_stable_frames=2))
    events = []
    events.extend(fsm.feed(_ev(1.0, "buy")))
    events.extend(fsm.feed(_ev(2.0, "combat")))  # single combat — no open
    assert not any(e.kind == "opened" for e in events)


def test_replay_and_non_game_never_open_or_close() -> None:
    fsm = RoundFSM(RoundFSMConfig(coarse_stable_frames=2))
    events = []
    for t, c in [(1.0, "buy"), (2.0, "buy"), (3.0, "combat"), (4.0, "combat")]:
        events.extend(fsm.feed(_ev(t, c)))
    events.extend(fsm.feed(_ev(5.0, "replay")))
    events.extend(fsm.feed(_ev(6.0, "replay")))
    events.extend(fsm.feed(_ev(7.0, "non_game")))
    assert not any(e.kind == "closed" for e in events)


def test_open_over_150s_discarded_not_forced() -> None:
    fsm = RoundFSM(RoundFSMConfig(coarse_stable_frames=2, max_open_sec=150.0))
    events = []
    for t in (1.0, 2.0):
        events.extend(fsm.feed(_ev(t, "buy")))
    for t in (3.0, 4.0):
        events.extend(fsm.feed(_ev(t, "combat")))
    events.extend(fsm.feed(_ev(160.0, "combat")))
    assert any(e.kind == "discarded" for e in events)
    assert not any(e.kind == "closed" for e in events)


def test_mid_combat_start_waits_for_buy() -> None:
    fsm = RoundFSM(RoundFSMConfig(coarse_stable_frames=2))
    events = []
    for t in (1.0, 2.0, 3.0):
        events.extend(fsm.feed(_ev(t, "combat")))
    assert not any(e.kind == "opened" for e in events)


def test_refine_does_not_change_round_key() -> None:
    fsm = RoundFSM(RoundFSMConfig(coarse_stable_frames=2))
    events = []
    for t in (1.0, 2.0):
        events.extend(fsm.feed(_ev(t, "buy")))
    for t in (3.0, 4.0):
        events.extend(fsm.feed(_ev(t, "combat")))
    opened = next(e for e in events if e.kind == "opened")
    key = opened.round_key
    fsm.apply_refine(round_key=key, start=3.1, end=None)
    assert opened.round_key == key
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_valorant_round_fsm.py -v`

Expected: FAIL（模块不存在）

- [ ] **Step 3: 最小实现**

实现要点（完整代码写入 `lsc/analyzer/valorant_round_fsm.py`）：

```python
@dataclass(frozen=True)
class FrameEvidence:
    timestamp: float
    class_probabilities: dict[str, float]
    predicted_class: str
    timer_seconds: float | None
    left_score: int | None
    right_score: int | None
    model_version: str

@dataclass
class RoundEvent:
    kind: str  # opened|closed|discarded|resync
    round_key: str
    start: float | None = None
    end: float | None = None
    confirm_status: str | None = None
    start_by: str | None = None
    end_by: str | None = None
    boundary_evidence: tuple[str, ...] = ()
    # ...

class RoundFSM:
    """WAIT_BUY -> WAIT_COMBAT -> ROUND_OPEN -> WAIT_BUY"""
    # feed(): 连续 coarse_stable_frames 才转换
    # non_game/replay 永不 open/close
    # score +1 或稳定 result 可 close
    # open 时长 > max_open_sec -> discarded
    # apply_refine 只改 start/end，不改 round_key
```

规则必须与 spec 一致：`RESULT_TAIL_SEC=1.5`、`NON_GAME_ABORT_SEC=5.0`、`max_open_sec=150`。

- [ ] **Step 4: 测试通过**

Run: `pytest tests/test_valorant_round_fsm.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add lsc/analyzer/valorant_round_fsm.py tests/test_valorant_round_fsm.py
git commit -m "feat: add pure-time valorant round FSM for hybrid vision evidence"
```

---

### Task 4: 可取消 FFmpeg 抽帧

**Files:**
- Create: `lsc/utils/cancellable_ffmpeg.py`
- Create: `tests/test_cancellable_ffmpeg.py`
- Modify: `lsc/utils/process_launcher.py`（如需导出 helper）

- [ ] **Step 1: 写失败测试（行为测，不用源码字符串）**

```python
# tests/test_cancellable_ffmpeg.py
from __future__ import annotations

import sys
import time

import pytest

from lsc.utils.cancellable_ffmpeg import CancellableFFmpeg, FFmpegCancelled


def test_cancel_terminates_child_process() -> None:
    # 用 python -c 模拟长驻“ffmpeg”
    cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
    proc = CancellableFFmpeg(cmd)
    proc.start()
    assert proc.poll() is None
    proc.cancel(timeout_sec=5.0)
    assert proc.poll() is not None
    assert proc.returncode is not None


def test_wait_raises_on_cancel_flag() -> None:
    cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
    cancelled = {"v": False}

    def check() -> bool:
        return cancelled["v"]

    proc = CancellableFFmpeg(cmd, cancel_check=check)
    proc.start()
    cancelled["v"] = True
    with pytest.raises(FFmpegCancelled):
        proc.wait(timeout_sec=5.0)
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_cancellable_ffmpeg.py -v`

Expected: FAIL

- [ ] **Step 3: 最小实现**

```python
# lsc/utils/cancellable_ffmpeg.py
from __future__ import annotations

import logging
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any

from lsc.utils.process_launcher import get_creation_flags

_log = logging.getLogger(__name__)


class FFmpegCancelled(RuntimeError):
    pass


class CancellableFFmpeg:
    def __init__(
        self,
        cmd: list[str],
        *,
        cancel_check: Callable[[], bool] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> None:
        self._cmd = list(cmd)
        self._cancel_check = cancel_check
        self._env = env
        self._cwd = cwd
        self._proc: subprocess.Popen[Any] | None = None

    def start(self) -> None:
        kwargs: dict[str, Any] = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "env": self._env,
            "cwd": self._cwd,
        }
        flags = get_creation_flags()
        if flags:
            kwargs["creationflags"] = flags
        if sys.platform == "win32":
            # 便于 taskkill /T 杀进程树
            kwargs.setdefault("creationflags", 0)
            kwargs["creationflags"] |= get_creation_flags()
        self._proc = subprocess.Popen(self._cmd, **kwargs)  # noqa: S603

    def poll(self) -> int | None:
        if self._proc is None:
            return None
        return self._proc.poll()

    @property
    def returncode(self) -> int | None:
        return None if self._proc is None else self._proc.returncode

    def cancel(self, timeout_sec: float = 5.0) -> None:
        if self._proc is None or self._proc.poll() is not None:
            return
        self._terminate_tree()
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                return
            time.sleep(0.05)
        self._kill_tree()

    def wait(self, timeout_sec: float = 120.0) -> subprocess.CompletedProcess[bytes]:
        if self._proc is None:
            raise RuntimeError("not started")
        deadline = time.monotonic() + timeout_sec
        while True:
            if self._cancel_check and self._cancel_check():
                self.cancel()
                raise FFmpegCancelled("ffmpeg cancelled")
            rc = self._proc.poll()
            if rc is not None:
                out, err = self._proc.communicate()
                return subprocess.CompletedProcess(self._cmd, rc, out, err)
            if time.monotonic() >= deadline:
                self.cancel()
                raise TimeoutError("ffmpeg timeout")
            time.sleep(0.05)

    def _terminate_tree(self) -> None:
        assert self._proc is not None
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/T", "/PID", str(self._proc.pid)],
                capture_output=True,
                **({"creationflags": get_creation_flags()} if get_creation_flags() else {}),
            )
        else:
            self._proc.terminate()

    def _kill_tree(self) -> None:
        assert self._proc is not None
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(self._proc.pid)],
                capture_output=True,
                **({"creationflags": get_creation_flags()} if get_creation_flags() else {}),
            )
        else:
            self._proc.kill()
```

- [ ] **Step 4: 测试通过**

Run: `pytest tests/test_cancellable_ffmpeg.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add lsc/utils/cancellable_ffmpeg.py tests/test_cancellable_ffmpeg.py
git commit -m "feat: add cancellable FFmpeg process wrapper for vision scans"
```

---

### Task 5: 粗扫抽帧 + 统一帧证据 + 按需数字锚点

**Files:**
- Modify: `lsc/analyzer/round_detector.py`
- Create: `tests/test_valorant_dense_refine.py`（本任务先写粗扫证据测试；密扫在 Task 6）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_valorant_dense_refine.py
from __future__ import annotations

from lsc.analyzer.round_detector import FrameEvidenceBuilder, build_frame_evidence


def test_ocr_failure_leaves_scores_none_not_zero() -> None:
    ev = build_frame_evidence(
        timestamp=12.0,
        probs={"non_game": 0.01, "buy": 0.02, "combat": 0.9, "result": 0.05, "replay": 0.02},
        predicted_class="combat",
        timer_seconds=None,
        left_score=None,
        right_score=None,
        model_version="stub",
    )
    assert ev.left_score is None
    assert ev.right_score is None
    assert ev.timer_seconds is None
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_valorant_dense_refine.py::test_ocr_failure_leaves_scores_none_not_zero -v`

Expected: FAIL

- [ ] **Step 3: 在 `round_detector.py` 增加证据构建与可取消整帧抽帧 API**

```python
def build_frame_evidence(...) -> FrameEvidence:
    return FrameEvidence(...)


def extract_frames_cancellable(
    video_path: str,
    *,
    start_sec: float,
    end_sec: float,
    fps: float,
    ffmpeg_path: str,
    cancel_check: Callable[[], bool] | None = None,
    overlap_sec: float = 2.0,
) -> list[tuple[float, np.ndarray]]:
    """用 CancellableFFmpeg 抽全画面 JPEG；按 showinfo pts 去重。"""
    ...


def read_top_digit_anchors(frame_bgr: np.ndarray) -> tuple[float | None, int | None, int | None]:
    """仅顶部小区域 RapidOCR；失败返回 (None, None, None)，禁止填 0。"""
    ...
```

数字 OCR **只**在模型预测为 `buy`/`result` 或状态转换候选时调用。

- [ ] **Step 4: 测试通过并 commit**

```bash
git add lsc/analyzer/round_detector.py tests/test_valorant_dense_refine.py
git commit -m "feat: add cancellable frame extract and optional digit anchors"
```

---

### Task 6: 局部密扫精修边界

**Files:**
- Modify: `lsc/analyzer/round_detector.py`
- Modify: `tests/test_valorant_dense_refine.py`

- [ ] **Step 1: 写失败测试**

```python
def test_dense_start_is_first_stable_combat_frame() -> None:
    # 合成 10FPS 概率序列：... buy buy combat combat combat
    from lsc.analyzer.round_detector import refine_boundary_from_sequence

    seq = [
        (0.0, "buy"), (0.1, "buy"), (0.2, "buy"),
        (0.3, "combat"), (0.4, "combat"), (0.5, "combat"),
    ]
    start = refine_boundary_from_sequence(seq, target="combat", min_stable=3, fps=10)
    assert start == 0.3


def test_end_keeps_1_5s_result_tail_unless_replay() -> None:
    from lsc.analyzer.round_detector import compute_clip_end

    assert compute_clip_end(result_event_ts=10.0, next_states=[]) == 11.5
    assert compute_clip_end(
        result_event_ts=10.0,
        next_states=[(10.8, "replay")],
    ) == 10.8


def test_no_strong_end_does_not_emit_vision_confirmed() -> None:
    from lsc.analyzer.round_detector import grade_round_confirmation

    status = grade_round_confirmation(
        start_strong=True,
        end_strong=False,
        score_confirm=False,
    )
    assert status == "pending"
```

- [ ] **Step 2–4: 实现 `refine_boundary_from_sequence` / `compute_clip_end` / `grade_round_confirmation`，测通后 commit**

```bash
git commit -m "feat: add dense refine boundary helpers for hybrid vision"
```

---

### Task 7: `detect_valorant_rounds_hybrid` 主检测入口

**Files:**
- Modify: `lsc/analyzer/round_detector.py`
- Modify: `tests/test_round_detector.py`（增加 hybrid 路径守卫，旧音频路径标记 deprecated）

- [ ] **Step 1: 写失败测试**

```python
def test_hybrid_detect_sets_boundary_source_and_round_key(monkeypatch, tmp_path):
    from lsc.analyzer import round_detector as rd

    # monkeypatch extract + classifier + fsm to synthetic closed round
    rounds = rd.detect_valorant_rounds_hybrid(
        str(tmp_path / "x.mp4"),
        time_range=(0.0, 60.0),
        model_dir=FIXTURE_DIR,
        cancel_check=lambda: False,
    )
    assert rounds
    assert rounds[0]["boundary_source"] == "valorant_hybrid_v1"
    assert rounds[0]["round_key"].startswith("hybrid-")
    assert "phase" in rounds[0]
```

（测试内用 monkeypatch 避免真 FFmpeg；行为测优先。）

- [ ] **Step 2–4: 实现入口**

```python
def detect_valorant_rounds_hybrid(
    video_path: str,
    *,
    ffmpeg_path: str = "ffmpeg",
    time_range: tuple[float, float] | None = None,
    model_dir: Path | None = None,
    cancel_check: Callable[[], bool] | None = None,
    progress_callback: ... = None,
    session_id: str = "",
) -> list[dict[str, Any]]:
    """1FPS 粗扫 -> FSM -> 候选前后 5s @8-10FPS 密扫 -> 证据分级。"""
```

`round_key`：`hybrid-{session_id}-{int(coarse_start)}`，密扫不改 key。

输出字段对齐 spec：`confirm_status`, `boundary_source`, `start_by`, `end_by`, `boundary_evidence`, `model_version`, `start_confidence`, `end_confidence`。

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: add detect_valorant_rounds_hybrid coarse+dense pipeline"
```

---

### Task 8: Handler 游标、stopping、vision 入列门禁

**Files:**
- Modify: `python-backend/handlers/room_handler.py`
- Modify: `python-backend/handlers/room_utils.py`
- Modify: `tests/test_continuous_analysis_guards.py`
- Create: `tests/test_hybrid_vision_lifecycle.py`

- [ ] **Step 1: 写失败测试（门禁）**

```python
# tests/test_continuous_analysis_guards.py 追加

def test_vision_confirmed_is_listable_and_exportable_gate():
    from handlers import room_handler
    rd = {
        "start": 10.0, "end": 55.0, "phase": "combat",
        "boundary_source": "valorant_hybrid_v1",
        "start_by": "model_buy_exit",
        "end_by": "model_result",
        "confirm_status": "vision_confirmed",
    }
    assert room_handler._is_listable_hybrid_round(rd) is True
    assert room_handler._is_auto_exportable_valorant_round(rd) is True


def test_failed_recognition_not_listable():
    rd = {"start": 10.0, "end": 12.0, "boundary_source": "valorant_hybrid_v1", "confirm_status": None}
    assert room_handler._is_listable_hybrid_round(rd) is False


def test_audio_only_round_not_listable_on_valorant_path():
    rd = {"start": 10.0, "end": 80.0, "start_by": "full_round", "end_by": "energy_collapse"}
    assert room_handler._is_listable_hybrid_round(rd) is False
```

- [ ] **Step 2: 写生命周期失败测试**

```python
# tests/test_hybrid_vision_lifecycle.py
import asyncio
import time

def test_stop_sets_stopping_until_resources_exit(monkeypatch):
    """伪 FFmpeg + 慢推理：stop 后状态为 stopping，退出后才 idle。"""
    # 用 asyncio.run 驱动精简版 stop 流程；断言顺序：
    # cancelled True -> status stopping -> 子进程 exit -> status idle
    ...


def test_semaphore_not_released_while_ffmpeg_alive(monkeypatch):
    ...
```

- [ ] **Step 3: 实现 handler 变更**

关键改动清单：

1. `_continuous_valorant_worker` 的 `_do_scan` 改为调用 `detect_valorant_rounds_hybrid`（`valorant_round` 模式）。
2. 入列：`confirm_status` 取自回合对象（`vision_confirmed` 或 `pending`）；`list_only=True`；`defer_export=True`。
3. `_is_auto_exportable_valorant_round`：接受 `vision_confirmed` + `boundary_source==valorant_hybrid_v1` + `start_by/end_by` 合法集合；**不再**要求旧 OCR `ocr_buy_exit`。
4. Hybrid 回合入列前**禁止** `_trim_valorant_combat_bounds`。
5. `handle_stop_continuous_analysis`：设 `cancelled` → `status='stopping'` → 等 worker 退出（含 FFmpeg cancel）→ 再 `pop` / 广播 `idle`；响应不得在仅 `task.cancel()` 后声称已停止。
6. 停录收尾：从 `last_processed_ts` 继续，不默认全文件重扫。
7. 状态广播增加 `analysis_lag_sec`、`model_version`、`provider`、`scan_phase`。
8. `room_utils.is_auto_exportable_valorant_round` 同步接受 `vision_confirmed`。

- [ ] **Step 4: 测试通过**

Run:

```bash
pytest tests/test_continuous_analysis_guards.py tests/test_hybrid_vision_lifecycle.py -v
```

Expected: PASS（更新旧 OCR 门禁断言以匹配新权威）

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: wire hybrid vision into continuous analysis with stopping lifecycle"
```

---

### Task 9: 前端 `vision_confirmed`

**Files:**
- Modify: `lsc-electron/src/types/index.ts`
- Modify: `lsc-electron/src/pages/Workbench/components/ClipList.tsx`
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`
- Modify: `tests/test_ux_habit_guards.py`
- Modify: `tests/test_frontend_stability_guards.py`（若有 confirm 枚举守卫）

- [ ] **Step 1: 写/更新守卫测试**

```python
# tests/test_ux_habit_guards.py — 确保 canExportClip 含 vision_confirmed
def test_can_export_includes_vision_confirmed():
    text = Path("lsc-electron/src/pages/Workbench/components/ClipList.tsx").read_text(encoding="utf-8")
    assert "vision_confirmed" in text
```

- [ ] **Step 2: 改类型**

```typescript
export type ClipConfirmStatus =
  | 'pending'
  | 'refining'
  | 'user_confirmed'
  | 'ocr_confirmed'
  | 'vision_confirmed'
```

可选字段：`boundary_evidence?: string[]`、`boundary_source?: string`。

- [ ] **Step 3: 改 `canExportClip` 与标签**

```typescript
const confirmed = !clip.confirm_status ||
  clip.confirm_status === 'user_confirmed' ||
  clip.confirm_status === 'ocr_confirmed' ||
  clip.confirm_status === 'vision_confirmed'

// tag:
case 'vision_confirmed': return { text: '视觉确认', color: 'geekblue' }
```

Workbench 快捷键 `canExportForShortcut` 同步加入 `vision_confirmed`。证据可显示 `boundary_evidence` 短标签（如「买枪退出 + 比分变化」）。

- [ ] **Step 4: 守卫测试通过 + `npx tsc --noEmit`**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: treat vision_confirmed clips as exportable in workbench"
```

---

### Task 10: 训练清单与抽帧脚本

**Files:**
- Create: `scripts/valorant_vision/manifest_schema.md`
- Create: `scripts/valorant_vision/extract_frames.py`
- Create: `scripts/valorant_vision/example_manifest.jsonl`

- [ ] **Step 1: 文档化清单字段**

```text
video_id, video_path, timestamp_sec, label, split, source_type(broadcast|pov), session_id, notes
```

约束：按完整录像分组划分 train/val/test；边界前后 3 秒提高密度。

- [ ] **Step 2: 实现 `extract_frames.py`**

读取 jsonl → 可取消 FFmpeg 抽帧 → 写出 `datasets/valorant_phase/<split>/<label>/...jpg`（目录默认在仓库外或 `.gitignore`）。

- [ ] **Step 3: Commit 脚本与 schema（不含原始帧）**

```bash
git commit -m "chore: add valorant vision dataset manifest schema and frame extractor"
```

---

### Task 11: 训练与 INT8 ONNX 导出

**Files:**
- Create: `scripts/valorant_vision/train_export.py`
- Create: `lsc/analyzer/models/valorant_phase_v1.json`（真实元数据）
- Produce: `lsc/analyzer/models/valorant_phase_v1.onnx`（或发布产物路径，按仓库策略）

- [ ] **Step 1: 实现训练脚本**

MobileNetV3-Small、输入 224、五类、按录像分组划分、亮度/JPEG/轻微缩放增强（禁止大幅随机裁剪）。

导出 INT8 ONNX；计算 SHA-256；写入 json（类别顺序、归一化、阈值版本、数据集版本）。

- [ ] **Step 2: 在验证集上标定 `thresholds.stable_prob` / `high_prob`**

不得偷看最终盲测录像标签。

- [ ] **Step 3: 用 Task 2 契约测试加载真实模型**

```bash
pytest tests/test_valorant_frame_classifier.py -v
```

- [ ] **Step 4: Commit 元数据与（若策略允许）ONNX**

```bash
git commit -m "feat: export valorant_phase_v1 INT8 ONNX with sealed metadata"
```

---

### Task 12: 分类评估与完整录像盲测门

**Files:**
- Create: `scripts/valorant_vision/eval_blind.py`
- Create: `tests/test_valorant_eval_gates.py`（对夹具报告解析/门槛断言；真盲测手工跑脚本）

- [ ] **Step 1: 评估脚本输出**

- 五分类混淆矩阵；每类 P/R/F1；Macro F1
- 按 `broadcast` / `pov` 分组
- 回合级：recall、入列 precision、起止误差 P95、超长切片计数、重复 key、非游戏入列计数

- [ ] **Step 2: 门槛（发布门）**

```text
Macro F1 >= 0.94
buy/result precision >= 0.97
replay/non_game recall >= 0.95
round recall >= 0.90
listed precision >= 0.97
vision_confirmed start/end |err| P95 <= 0.8s；单条 <= 2s
无 150/180 强制闭合切片
```

- [ ] **Step 3: Commit 脚本**

```bash
git commit -m "test: add valorant hybrid vision evaluation and blind-test gates"
```

---

### Task 13: Shadow 运行（只报告不入列）

**Files:**
- Modify: `python-backend/handlers/room_handler.py`（临时 `shadow_mode` 或环境变量 `LSC_VALORANT_VISION_SHADOW=1`）

- [ ] **Step 1: Shadow 下跑 hybrid 检测，写本地报告，跳过 `clip_queued`**

- [ ] **Step 2: 各选一场赛事转播 + 第一视角验证报告**

- [ ] **Step 3: 验收通过后删除 shadow 分支（不要长期双开关）**

```bash
git commit -m "chore: add temporary hybrid vision shadow mode for pre-cutover validation"
# 验证后另提交删除 shadow
git commit -m "chore: remove hybrid vision shadow mode after cutover validation"
```

---

### Task 14: 切换主路径并删除 Valorant 旧边界权威

**Files:**
- Modify: `lsc/analyzer/round_detector.py` — `detect_valorant_rounds` 委托 hybrid 或直接替换
- Modify: `python-backend/handlers/room_handler.py` — 移除 OCR 升格 / 音频 pending 入列
- Modify: 相关测试，删除过时 OCR-only 入列断言
- 保留：`sound_detector` / 通用音频能力供其他模式；`ocr_detector` 杀敌等非边界用途可留

- [ ] **Step 1: 测试锁定「旧路径不能入列」**

```python
def test_legacy_audio_ocr_boundary_cannot_enter_valorant_list_path():
    ...
```

- [ ] **Step 2: 切换并删除 Valorant 边界用的音频/旧文字 OCR/固定 HUD 主路径代码**

- [ ] **Step 3: 全量相关测试**

```bash
pytest tests/test_valorant_*.py tests/test_continuous_analysis_guards.py tests/test_hybrid_vision_lifecycle.py tests/test_round_detector.py -v
```

- [ ] **Step 4: Commit**

```bash
git commit -m "feat: make hybrid vision the sole valorant_round boundary authority"
```

---

### Task 15: 回归基线与性能验收

**Files:**
- 无新功能文件；记录基线

- [ ] **Step 1: 实施前（或切换前）记录完整 pytest 失败清单**

```bash
pytest -v --tb=line 2>&1 | tee docs/superpowers/plans/baselines/2026-07-20-pre-hybrid-vision.txt
```

- [ ] **Step 2: 切换后对比，不得新增失败；已知无关失败逐项记录**

- [ ] **Step 3: 性能手测清单**

- 稳态延迟 P95 ≤10s（GPU/DML），CPU-only ≤15s
- 处理速度 ≥ 录像增长
- 停止后 3s 内 `idle`，无孤儿 FFmpeg
- 停录收尾不从 0 全扫

- [ ] **Step 4: 最终 commit（若有基线文档）**

```bash
git commit -m "docs: record hybrid vision regression baseline and acceptance notes"
```

---

## Spec 覆盖自检

| Spec 章节 | 对应 Task |
|-----------|-----------|
| 模型包装器 / 元数据 | 1–2 |
| 五分类 + 训练数据设计 | 10–11 |
| 帧证据 / 数字锚点 | 5 |
| 时间状态机 | 3 |
| 边界语义 / 粗扫密扫 | 6–7 |
| 证据等级 / round_key | 3, 7–8 |
| 切片列表与 `vision_confirmed` | 8–9 |
| 多房间映射复用 | 8（不改映射公式） |
| 可取消生命周期 | 4, 8 |
| 错误处理不回退旧算法 | 7–8, 14 |
| 测试策略 | 各 Task + 12, 15 |
| 发布顺序 / 删除旧路径 | 13–14 |
| 不整树覆盖 HUD worktree | 审阅锁定 |

## Placeholder 扫描

计划中无 TBD/「稍后实现」；训练任务依赖真实标注数据，但脚本与门槛已写明。Stub ONNX 保证 Task 1–9 可不阻塞训练。

## 类型一致性

- `confirm_status`: `pending` | `vision_confirmed`（入列）；前端另有 `refining` / `user_confirmed` / `ocr_confirmed`
- `boundary_source`: 固定 `valorant_hybrid_v1`
- `start_by`: `model_buy_exit`
- `end_by`: `model_result` | `model_score`
- `round_key`: `hybrid-{session}-{int(coarse_start)}`，密扫不改
- 类名：`ValorantFrameClassifier`, `RoundFSM`, `FrameEvidence`, `CancellableFFmpeg`, `detect_valorant_rounds_hybrid`
