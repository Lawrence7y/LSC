"""OCR 加速后端选择：auto / dml / cuda / cpu。"""
from __future__ import annotations

import json
import logging
import os
import platform
import re
import tempfile
import time
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

_process_probe: dict[str, Any] | None = None

# Minimal valid 1x1 white PNG (last-resort probe image).
_MINIMAL_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x01\x01\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

VALID_OCR_ACCEL = frozenset({"auto", "dml", "cuda", "cpu"})
_ALIAS = {
    "automatic": "auto",
    "directml": "dml",
    "dml": "dml",
    "cuda": "cuda",
    "gpu": "cuda",
    "cpu": "cpu",
    "auto": "auto",
}

_PROBE_CACHE_TTL_SEC = 7 * 86400

def normalize_ocr_accel(value: Any) -> str:
    if value is None:
        return "auto"
    key = str(value).strip().lower()
    if not key:
        return "auto"
    mapped = _ALIAS.get(key, key)
    if mapped not in VALID_OCR_ACCEL:
        _log.warning("非法 ocr_accel=%r，回退 auto", value)
        return "auto"
    return mapped


def _onnx_providers() -> list[str]:
    try:
        import onnxruntime as ort  # type: ignore[import-untyped]
        return list(ort.get_available_providers())
    except Exception as exc:
        _log.debug("onnxruntime providers 不可用: %s", exc)
        return []


def _is_windows_dml_capable() -> bool:
    if platform.system() != "Windows":
        return False
    # Win10 1903+ ≈ build 18362
    m = re.search(r"(\d+)$", platform.version())
    if not m:
        return True
    try:
        return int(m.group(1)) >= 18362
    except ValueError:
        return True


def list_accel_candidates() -> list[str]:
    providers = set(_onnx_providers())
    out: list[str] = []
    if "DmlExecutionProvider" in providers and _is_windows_dml_capable():
        out.append("dml")
    if "CUDAExecutionProvider" in providers:
        out.append("cuda")
    out.append("cpu")
    return out


def resolve_ocr_accel(
    mode: Any,
    *,
    probe_timings: dict[str, float] | None = None,
) -> str:
    """将用户设置解析为实际可用后端。

    probe_timings: 可选 {\"dml\": ms, \"cuda\": ms, \"cpu\": ms}，auto 时选最小正值。
    """
    normalized = normalize_ocr_accel(mode)
    candidates = list_accel_candidates()
    if normalized == "auto":
        if probe_timings:
            usable = {
                k: v
                for k, v in probe_timings.items()
                if k in candidates and isinstance(v, (int, float)) and v > 0
            }
            if usable:
                return min(usable, key=lambda k: usable[k])
        # 无探针时偏好顺序：dml → cuda → cpu
        for pref in ("dml", "cuda", "cpu"):
            if pref in candidates:
                return pref
        return "cpu"
    if normalized in candidates:
        return normalized
    _log.warning("ocr_accel=%s 不可用（candidates=%s），回退 cpu", normalized, candidates)
    return "cpu"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _user_data_root() -> Path | None:
    """Return Electron's writable user-data directory when running packaged."""
    raw = os.environ.get("LSC_DATA_DIR", "").strip()
    return Path(raw) if raw else None


def _probe_cache_path() -> Path:
    root = _user_data_root()
    return (root / "data" if root else _repo_root() / "data") / "ocr_accel_probe.json"


# 须与 python-backend/handlers/room_handler.py SETTINGS_FILE 一致
def _settings_path() -> Path:
    root = _user_data_root()
    return (root if root else _repo_root() / "python-backend") / "settings.json"


def rapidocr_kwargs_for(effective: str) -> dict[str, Any]:
    eff = normalize_ocr_accel(effective)
    if eff == "dml":
        return {"det_use_dml": True, "cls_use_dml": True, "rec_use_dml": True}
    if eff == "cuda":
        return {"det_use_cuda": True, "cls_use_cuda": True, "rec_use_cuda": True}
    return {}


def load_probe_cache() -> dict[str, Any] | None:
    path = _probe_cache_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _log.debug("读 OCR 探针缓存失败: %s", exc)
        return None
    saved_at = float(data.get("saved_at", 0))
    if time.time() - saved_at > _PROBE_CACHE_TTL_SEC:
        return None
    return data  # type: ignore[no-any-return]


def save_probe_cache(
    timings: dict[str, float],
    *,
    selected: str,
    ort_version: str,
) -> None:
    try:
        path = _probe_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "timings": timings,
            "selected": selected,
            "ort_version": ort_version,
            "saved_at": time.time(),
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        _log.warning("写 OCR 探针缓存失败（非致命）: %s", exc)


def _ort_version() -> str:
    try:
        import onnxruntime as ort
        return str(getattr(ort, "__version__", "unknown"))
    except Exception:
        return "unknown"


def _make_probe_image() -> Path:
    """Create a small PNG for OCR micro-benchmark."""
    fd, raw_path = tempfile.mkstemp(suffix=".png")
    path = Path(raw_path)
    try:
        import os
        os.close(fd)
    except OSError:
        pass

    try:
        from PIL import Image, ImageDraw, ImageFont

        img = Image.new("RGB", (320, 80), color="white")
        draw = ImageDraw.Draw(img)
        text = "购买阶段"
        try:
            font = ImageFont.truetype("msyh.ttc", 28)
        except OSError:
            try:
                font = ImageFont.truetype("arial.ttf", 28)
            except OSError:
                font = ImageFont.load_default()  # type: ignore[assignment]
        draw.text((12, 24), text, fill="black", font=font)
        img.save(path, format="PNG")
        return path
    except Exception as exc:
        _log.debug("PIL probe image failed: %s", exc)

    try:
        import cv2
        import numpy as np

        img = np.full((80, 320, 3), 255, dtype=np.uint8)  # type: ignore[assignment]
        text = "BUY PHASE"
        try:
            cv2.putText(  # type: ignore[call-overload]
                img,
                text,
                (12, 52),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.0,
                (0, 0, 0),
                2,
                cv2.LINE_AA,
            )
        except Exception as exc:
            _log.debug("cv2 putText for OCR probe skipped: %s", exc)
        cv2.imwrite(str(path), img)  # type: ignore[call-overload]
        return path
    except Exception as exc:
        _log.debug("cv2 probe image failed: %s", exc)

    path.write_bytes(_MINIMAL_PNG)
    return path


def run_micro_benchmark() -> dict[str, float]:
    """Warm-up + timed OCR on probe image per candidate; return ms timings."""
    from rapidocr_onnxruntime import RapidOCR  # type: ignore[import-untyped]

    image_path = _make_probe_image()
    timings: dict[str, float] = {}
    try:
        for cand in list_accel_candidates():
            kwargs = rapidocr_kwargs_for(cand)
            inst = None
            try:
                inst = RapidOCR(**kwargs)
                inst(str(image_path))
                t0 = time.perf_counter()
                inst(str(image_path))
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                timings[cand] = elapsed_ms
            except Exception as exc:
                _log.debug("OCR micro-benchmark skip %s: %s", cand, exc)
            finally:
                del inst
    finally:
        try:
            image_path.unlink(missing_ok=True)
        except OSError as exc:
            _log.debug("probe image cleanup failed: %s", exc)
    return timings


def run_probe_if_needed(mode: str | None) -> str:
    global _process_probe

    normalized = normalize_ocr_accel(mode)
    if normalized != "auto":
        return resolve_ocr_accel(normalized, probe_timings=None)

    ort_ver = _ort_version()

    if (
        _process_probe is not None
        and _process_probe.get("ort_version") == ort_ver
        and _process_probe.get("selected")
    ):
        return str(_process_probe["selected"])

    cache = load_probe_cache()
    if cache and cache.get("ort_version") == ort_ver:
        timings = cache.get("timings") or {}
        _process_probe = {
            "timings": timings,
            "selected": cache.get("selected"),
            "ort_version": ort_ver,
        }
        selected = resolve_ocr_accel("auto", probe_timings=timings)
        _log.info("OCR accel from cache: %s timings=%s", selected, timings)
        return selected

    timings = run_micro_benchmark()
    if timings:
        selected = resolve_ocr_accel("auto", probe_timings=timings)
        save_probe_cache(timings, selected=selected, ort_version=ort_ver)
        _process_probe = {
            "timings": timings,
            "selected": selected,
            "ort_version": ort_ver,
        }
        _log.info("OCR accel selected: %s (probe: %s)", selected, timings)
        return selected

    selected = resolve_ocr_accel("auto", probe_timings=None)
    _log.info("OCR accel selected (no probe timings): %s", selected)
    return selected


def create_ocr(mode: str | None = None) -> Any:
    from rapidocr_onnxruntime import RapidOCR  # type: ignore[import-untyped]

    effective = run_probe_if_needed(mode)
    kwargs = rapidocr_kwargs_for(effective)
    try:
        inst = RapidOCR(**kwargs)
    except Exception as exc:
        _log.warning("RapidOCR(%s) 失败，回退 CPU: %s", effective, exc)
        inst = RapidOCR()
    else:
        _log.info("OCR accel active: %s kwargs=%s", effective, kwargs)
    return inst


def read_settings_ocr_accel() -> str:
    path = _settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return normalize_ocr_accel(data.get("ocr_accel"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        _log.debug("读 settings ocr_accel 失败，回退 auto: %s", exc)
        return "auto"


def ffmpeg_hwaccel_args(effective_or_mode: str) -> list[str]:
    mode = normalize_ocr_accel(effective_or_mode)
    if mode == "cpu":
        return []
    if mode == "cuda":
        return ["-hwaccel", "cuda"]
    if mode in ("dml", "auto") and platform.system() == "Windows":
        return ["-hwaccel", "d3d11va"]
    return []


def gpu_scale_available() -> bool:
    """检测当前环境是否支持 scale_cuda 滤镜（缓存结果）。"""
    try:
        import shutil

        from lsc.config import load_config
        from lsc.utils.gpu_ffmpeg import scale_cuda_available

        cfg = load_config()
        ffmpeg = cfg.ffmpeg_path or shutil.which("ffmpeg") or "ffmpeg"
        return scale_cuda_available(ffmpeg)
    except Exception as exc:
        _log.debug("gpu_scale_available probe failed: %s", exc)
        return False


def build_hwaccel_vf(
    cpu_vf: str,
    *,
    gpu_scale_pattern: str | None = None,
) -> tuple[list[str], str]:
    """构建硬件加速滤镜链。

    当 GPU scale 可用且提供了 gpu_scale_pattern 时，将 CPU scale 替换为
    hwupload_cuda + scale_cuda + hwdownload,format=nv12 链路，把缩放留在 GPU。

    Args:
        cpu_vf: 原始 CPU 滤镜链（如 "fps=1.0,crop=260:115:520:85,scale=780:345,showinfo"）
        gpu_scale_pattern: 可选，CPU scale 滤镜的正则模式（如 ``r"scale=\\d+:\\d+"``），
            匹配到的部分会被替换为 GPU 链路。

    Returns:
        (hwaccel_args, vf_string) 元组：
        - hwaccel_args: 额外的输入侧硬解参数（通常为 d3d11va）
        - vf_string: 最终 -vf 滤镜字符串
    """
    if not gpu_scale_pattern or not gpu_scale_available():
        return [], cpu_vf

    # 将 CPU scale 替换为 GPU 链路
    gpu_chain = "hwupload_cuda,{gpu_scale},hwdownload,format=nv12"
    try:
        new_vf = re.sub(
            gpu_scale_pattern,
            lambda m: gpu_chain.format(gpu_scale=m.group(0).replace("scale=", "scale_cuda=")),
            cpu_vf,
            count=1,
        )
        if new_vf != cpu_vf:
            hw = ffmpeg_hwaccel_args(read_settings_ocr_accel())
            return hw, new_vf
    except Exception as exc:
        _log.debug("build_hwaccel_vf substitution failed: %s", exc)
    return [], cpu_vf


def run_ffmpeg_with_hwaccel_fallback(
    cmd_without_hwaccel: list[str],
    *,
    hwaccel_args: list[str],
    timeout: int = 360,
) -> Any:
    from lsc.utils.process_launcher import run_hidden

    def _insert(cmd: list[str], hw: list[str]) -> list[str]:
        if not hw:
            return list(cmd)
        return [cmd[0], *hw, *cmd[1:]]

    first = _insert(cmd_without_hwaccel, hwaccel_args)
    result = run_hidden(
        first,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        timeout=timeout,
    )
    if result.returncode == 0 or not hwaccel_args:
        return result
    _log.warning("FFmpeg hwaccel 失败 (code=%s)，回退软解", result.returncode)
    return run_hidden(
        cmd_without_hwaccel,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        timeout=timeout,
    )
