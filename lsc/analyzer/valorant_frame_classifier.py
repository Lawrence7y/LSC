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
        try:
            mean = np.asarray(meta["normalize_mean"], dtype=np.float32)
            std = np.asarray(meta["normalize_std"], dtype=np.float32)
        except (TypeError, ValueError) as exc:
            raise ModelContractError("normalize contract invalid") from exc
        if mean.shape != (3,) or std.shape != (3,) or not np.all(np.isfinite(mean)):
            raise ModelContractError("normalize contract invalid")
        if not np.all(np.isfinite(std)) or np.any(std <= 0):
            raise ModelContractError("normalize std invalid")
        thresholds = meta["thresholds"]
        try:
            stable = float(thresholds["stable_prob"])
            high = float(thresholds["high_prob"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ModelContractError("threshold contract invalid") from exc
        if not (0.0 < stable <= high <= 1.0):
            raise ModelContractError("threshold contract invalid")

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
        probs = np.asarray(
            self._session.run(None, {input_name: batch})[0],
            dtype=np.float32,
        )
        if probs.shape != (len(frames_bgr), len(_CLASS_NAMES)):
            raise ModelContractError(
                f"probabilities shape mismatch: {probs.shape}"
            )
        if (
            not np.all(np.isfinite(probs))
            or np.any(probs < -1e-5)
            or np.any(probs > 1.0 + 1e-5)
            or not np.allclose(probs.sum(axis=1), 1.0, atol=1e-3)
        ):
            raise ModelContractError("model output must be normalized probabilities")
        return probs

    def _preprocess(self, frame_bgr: np.ndarray) -> np.ndarray:
        assert self._meta is not None
        import cv2

        size = int(self._meta["input_size"][0])
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        resized = cv2.resize(rgb, (size, size), interpolation=cv2.INTER_AREA)
        x = resized.astype(np.float32) / 255.0
        mean = np.asarray(self._meta["normalize_mean"], dtype=np.float32)
        std = np.asarray(self._meta["normalize_std"], dtype=np.float32)
        x = (x - mean) / std
        return np.transpose(x, (2, 0, 1))
