"""Valorant 五分类视觉模型的最小运行时包装器。

该模块只负责模型契约、provider 选择和批量推理；回合边界策略位于
``valorant_broadcast`` / ``valorant_ocr_rounds``，避免模型概率直接改变 FSM。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

import numpy as np

from lsc.analyzer.ocr_accel import (
    list_accel_candidates,
    normalize_ocr_accel,
    read_settings_ocr_accel,
)

_log = logging.getLogger(__name__)

_CLASS_NAMES = ("non_game", "buy", "combat", "result", "replay")
_DEFAULT_DIR = Path(
    os.environ.get("LSC_VALORANT_MODEL_DIR", "")
    or (Path(__file__).resolve().parent / "models")
)


class ModelContractError(RuntimeError):
    """模型文件、元数据或推理输出不符合运行时契约。"""


def _provider_name(accel: str) -> str:
    return {
        "dml": "DmlExecutionProvider",
        "cuda": "CUDAExecutionProvider",
        "cpu": "CPUExecutionProvider",
    }[accel]


class ValorantFrameClassifier:
    """线程安全懒加载的 Valorant 五分类器。"""

    def __init__(self, model_dir: Path | None = None) -> None:
        self._dir = Path(model_dir) if model_dir else _DEFAULT_DIR
        self._session: Any = None
        self._meta: dict[str, Any] | None = None
        self._provider: str | None = None
        self._provider_warning: str | None = None
        self._lock = threading.Lock()
        self._telemetry_lock = threading.Lock()
        self._inference_frames_total = 0
        self._last_inference_frames = 0
        self._inference_elapsed_total = 0.0
        self._inference_latencies_ms: deque[float] = deque(maxlen=120)

    @property
    def model_version(self) -> str:
        if self._meta is None:
            raise ModelContractError("model not loaded")
        return str(self._meta["model_version"])

    @property
    def provider(self) -> str | None:
        return self._provider

    @property
    def provider_warning(self) -> str | None:
        return self._provider_warning

    @property
    def thresholds(self) -> dict[str, float]:
        if self._meta is None:
            raise ModelContractError("model not loaded")
        return {str(k): float(v) for k, v in self._meta["thresholds"].items()}

    @property
    def class_stable_prob(self) -> dict[str, float]:
        """Optional per-class threshold used by the broadcast phase audit."""
        if self._meta is None:
            raise ModelContractError("model not loaded")
        raw = self._meta.get("class_stable_prob", {})
        if not isinstance(raw, dict):
            return {}
        return {str(key): float(value) for key, value in raw.items()}

    @property
    def telemetry(self) -> dict[str, Any]:
        """Return thread-safe runtime model timing counters for status reporting."""
        with self._telemetry_lock:
            latencies = sorted(self._inference_latencies_ms)
            if latencies:
                p50 = latencies[(len(latencies) - 1) // 2]
                p90 = latencies[min(len(latencies) - 1, int(round((len(latencies) - 1) * 0.9)))]
            else:
                p50 = p90 = 0.0
            fps = (
                self._inference_frames_total / self._inference_elapsed_total
                if self._inference_elapsed_total > 0.0
                else 0.0
            )
            return {
                "last_model_inference_frames": self._last_inference_frames,
                "model_inference_frames_total": self._inference_frames_total,
                "model_infer_fps": round(fps, 2),
                "model_infer_ms_p50": round(p50, 2),
                "model_infer_ms_p90": round(p90, 2),
                "model_version": self.model_version if self._meta is not None else None,
                "provider": self._provider,
                "provider_warning": self._provider_warning,
            }

    def _record_inference(self, frame_count: int, elapsed_sec: float) -> None:
        with self._telemetry_lock:
            self._last_inference_frames = max(0, int(frame_count))
            self._inference_frames_total += max(0, int(frame_count))
            self._inference_elapsed_total += max(0.0, float(elapsed_sec))
            self._inference_latencies_ms.append(max(0.0, float(elapsed_sec)) * 1000.0)

    def load(self) -> None:
        with self._lock:
            if self._session is not None and self._meta is not None:
                return
            onnx_path = self._dir / "valorant_phase_v1.onnx"
            meta_path = self._dir / "valorant_phase_v1.json"
            if not onnx_path.is_file() or not meta_path.is_file():
                raise ModelContractError(f"missing model or metadata under {self._dir}")
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ModelContractError(f"invalid model metadata: {exc}") from exc
            self._validate_meta(meta)
            digest = hashlib.sha256(onnx_path.read_bytes()).hexdigest()
            if digest.lower() != str(meta["sha256"]).lower():
                raise ModelContractError("sha256 mismatch")
            self._session = self._create_session(onnx_path)
            self._meta = meta
            _log.info(
                "Valorant classifier loaded: model=%s provider=%s path=%s",
                meta["model_version"], self._provider, onnx_path,
            )

    def _validate_meta(self, meta: dict[str, Any]) -> None:
        required = {
            "model_version", "class_names", "input_size", "color_order",
            "normalize_mean", "normalize_std", "threshold_version",
            "sha256", "dataset_version", "thresholds",
        }
        missing = required - set(meta)
        if missing:
            raise ModelContractError(f"metadata missing keys: {sorted(missing)}")
        promotion_state = str(meta.get("promotion_state") or "").strip().lower()
        gate_results = meta.get("gate_results")
        if promotion_state == "active" and (
            not isinstance(gate_results, dict)
            or gate_results.get("gates_passed") is not True
        ):
            raise ModelContractError(
                "active model metadata must reference a passing promotion report"
            )
        if list(meta["class_names"]) != list(_CLASS_NAMES):
            raise ModelContractError("class_names mismatch")
        if list(meta["input_size"]) != [224, 224] or meta["color_order"] != "RGB":
            raise ModelContractError("input preprocessing contract mismatch")
        try:
            mean = np.asarray(meta["normalize_mean"], dtype=np.float32)
            std = np.asarray(meta["normalize_std"], dtype=np.float32)
            stable = float(meta["thresholds"]["stable_prob"])
            high = float(meta["thresholds"]["high_prob"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ModelContractError("model metadata contract invalid") from exc
        if mean.shape != (3,) or std.shape != (3,):
            raise ModelContractError("normalize contract invalid")
        if not np.all(np.isfinite(mean)) or not np.all(np.isfinite(std)) or np.any(std <= 0):
            raise ModelContractError("normalize contract invalid")
        if not (0.0 < stable <= high <= 1.0):
            raise ModelContractError("threshold contract invalid")
        class_thresholds = meta.get("class_stable_prob", {})
        if not isinstance(class_thresholds, dict):
            raise ModelContractError("class threshold contract invalid")
        try:
            valid_class_thresholds = all(
                label in _CLASS_NAMES and 0.0 < float(value) <= 1.0
                for label, value in class_thresholds.items()
            )
        except (TypeError, ValueError):
            valid_class_thresholds = False
        if not valid_class_thresholds:
            raise ModelContractError("class threshold contract invalid")

    def _create_session(self, onnx_path: Path) -> Any:
        try:
            import onnxruntime as ort  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ModelContractError("onnxruntime is unavailable") from exc

        last_error: Exception | None = None
        self._provider_warning = None
        for accel in list_accel_candidates():
            provider = _provider_name(accel)
            try:
                session = ort.InferenceSession(
                    str(onnx_path), providers=[provider, "CPUExecutionProvider"],
                )
                actual = session.get_providers()[0]
                if accel != "cpu" and actual == "CPUExecutionProvider":
                    last_error = RuntimeError(f"{provider} unavailable")
                    continue
                self._provider = actual
                if actual == "CPUExecutionProvider":
                    requested = normalize_ocr_accel(read_settings_ocr_accel())
                    if requested != "cpu":
                        self._provider_warning = (
                            f"Valorant classifier fell back to CPU (ocr_accel={requested})"
                        )
                        _log.warning(self._provider_warning)
                return session
            except Exception as exc:  # noqa: BLE001 - 尝试下一个 provider
                last_error = exc
                _log.warning("Valorant classifier provider %s failed: %s", provider, exc)
        raise ModelContractError(f"failed to init onnx session: {last_error}")

    def predict_batch(
        self,
        frames_bgr: list[np.ndarray],
        *,
        _record_telemetry: bool = True,
    ) -> np.ndarray:
        self.load()
        if self._session is None or self._meta is None:
            raise ModelContractError("model session unavailable")
        if not frames_bgr:
            return np.zeros((0, len(_CLASS_NAMES)), dtype=np.float32)
        started = time.perf_counter()
        batch = self._preprocess_batch(frames_bgr)
        input_name = self._session.get_inputs()[0].name
        probs = np.asarray(
            self._session.run(None, {input_name: batch})[0], dtype=np.float32,
        )
        expected = (len(frames_bgr), len(_CLASS_NAMES))
        if probs.shape != expected:
            raise ModelContractError(f"probabilities shape mismatch: {probs.shape}")
        if (
            not np.all(np.isfinite(probs))
            or np.any(probs < -1e-5)
            or np.any(probs > 1.0 + 1e-5)
            or not np.allclose(probs.sum(axis=1), 1.0, atol=1e-3)
        ):
            raise ModelContractError("model output must be normalized probabilities")
        if _record_telemetry:
            self._record_inference(len(frames_bgr), time.perf_counter() - started)
        return probs

    def predict_broadcast_batch(self, frames_bgr: list[np.ndarray]) -> np.ndarray:
        """Fuse full-frame and top-HUD predictions for configured broadcast models."""
        self.load()
        if not frames_bgr:
            return np.zeros((0, len(_CLASS_NAMES)), dtype=np.float32)
        fusion = self._meta.get("broadcast_input_fusion", {}) if self._meta else {}
        if not isinstance(fusion, dict):
            fusion = {}
        top_weight = float(fusion.get("top_hud_weight", 0.0))
        full_weight = float(fusion.get("full_frame_weight", 1.0 - top_weight))
        if top_weight <= 0.0 or full_weight <= 0.0:
            return self.predict_batch(frames_bgr)
        total = full_weight + top_weight
        full_weight /= total
        top_weight /= total
        top_frames: list[np.ndarray] = []
        for frame in frames_bgr:
            height = max(1, int(frame.shape[0] * 0.34))
            top_frames.append(frame[:height, :])
        started = time.perf_counter()
        full_probs = self.predict_batch(frames_bgr, _record_telemetry=False)
        top_probs = self.predict_batch(top_frames, _record_telemetry=False)
        self._record_inference(len(frames_bgr), time.perf_counter() - started)
        return (full_probs * full_weight + top_probs * top_weight).astype(np.float32)

    def _preprocess_batch(self, frames_bgr: list[np.ndarray]) -> np.ndarray:
        import cv2

        if self._meta is None:
            raise ModelContractError("model metadata unavailable")
        size = int(self._meta["input_size"][0])
        mean = np.asarray(self._meta["normalize_mean"], dtype=np.float32).reshape(3, 1, 1)
        inv_std = 1.0 / np.asarray(self._meta["normalize_std"], dtype=np.float32).reshape(3, 1, 1)
        batch = np.empty((len(frames_bgr), 3, size, size), dtype=np.float32)
        for idx, frame in enumerate(frames_bgr):
            resized = cv2.resize(frame, (size, size), interpolation=cv2.INTER_AREA)
            rgb = resized[:, :, ::-1].astype(np.float32) / 255.0
            batch[idx] = (np.transpose(rgb, (2, 0, 1)) - mean) * inv_std
        return batch


__all__ = ["ModelContractError", "ValorantFrameClassifier", "_CLASS_NAMES"]
