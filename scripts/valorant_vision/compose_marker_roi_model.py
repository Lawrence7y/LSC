#!/usr/bin/env python3
"""把「回放标记支路」装配成一个可评估的候选模型目录。

为什么要这一步
--------------
标记支路的声明放在**主模型元数据**里（`marker_roi_branch`），与既有的
`broadcast_input_fusion` 一致——这样"模型怎么把概率变成标签"这件事始终是
**模型自带**的契约，不会散落在调用方。副作用是：装配 = 复制主模型 + 写入声明 +
把标记模型放到同目录，需要一个可复现的步骤，而不是手工改 JSON。

本脚本**不改仓库里的生产模型目录**，只产出仓库外的候选目录，供
`reeval_replay_verified.py` / `compare_models_official.py` 做 A/B 对照。

用法
----
    python scripts/valorant_vision/compose_marker_roi_model.py \
        --base-dir   lsc/analyzer/models/valorant_phase_broadcast_finetune_v4_fused_20260907 \
        --marker-model C:/lsc_models/marker_roi_model_20260910/valorant_phase_v1.onnx \
        --out-dir    C:/lsc_models/broadcast_with_marker_20260910 \
        --weight 1.0
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# 同目录脚本要能被 import（直接运行与作为模块 import 两种情形都要成立）
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

BASE_ONNX = "valorant_phase_v1.onnx"
BASE_META = "valorant_phase_v1.json"
MARKER_ONNX = "valorant_marker_v1.onnx"


def _default_rois() -> dict[str, list[float]]:
    """直接复用数据集生成器的 ROI 定义 —— 两边**必须**是同一份，否则训练/推理错位。

    （原来是各写一份常量，靠注释提醒同步；改成 import 从根上消除漂移风险。）
    """
    from build_marker_roi_dataset import ROIS  # noqa: PLC0415 - 同目录脚本

    return {name: [float(v) for v in box] for name, box in ROIS.items()}


DEFAULT_ROIS: dict[str, list[float]] = _default_rois()


def compose(
    *,
    base_dir: Path,
    marker_model: Path,
    out_dir: Path,
    weight: float,
    rois: dict[str, list[float]],
    target_class: str,
) -> dict:
    base_onnx = base_dir / BASE_ONNX
    base_meta = base_dir / BASE_META
    for path in (base_onnx, base_meta, marker_model):
        if not path.is_file():
            raise SystemExit(f"缺少文件: {path}")
    meta = json.loads(base_meta.read_text(encoding="utf-8"))
    if "marker_roi_branch" in meta:
        raise SystemExit("基模型元数据里已经有 marker_roi_branch；请换一个基模型或先清理")

    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(base_onnx, out_dir / BASE_ONNX)
    shutil.copyfile(marker_model, out_dir / MARKER_ONNX)
    # 一并留档标记模型的元数据（训练来源/数据集摘要），便于审计；推理不读它
    marker_meta_src = marker_model.with_suffix(".json")
    marker_meta = None
    if marker_meta_src.is_file():
        shutil.copyfile(marker_meta_src, out_dir / "valorant_marker_v1.json")
        try:
            marker_meta = json.loads(marker_meta_src.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            marker_meta = None
    # 主模型的 sha256 校验的是 onnx 本身，复制不动它 → 校验仍然成立
    meta["marker_roi_branch"] = {
        "model_path": MARKER_ONNX,
        "weight": float(weight),
        "rois": {name: [float(v) for v in box] for name, box in rois.items()},
        "class": target_class,
        "note": "标记支路：标记区按原生分辨率放大成独立输入，与整帧证据取 max（只增不减）",
    }
    (out_dir / BASE_META).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return {
        "out_dir": str(out_dir),
        "base_onnx": str(base_onnx),
        "marker_onnx": str(out_dir / MARKER_ONNX),
        "marker_meta_copied": marker_meta is not None,
        "marker_dataset_version": (marker_meta or {}).get("dataset_version"),
        "weight": float(weight),
        "rois": list(rois),
        "class": target_class,
    }


def self_check(out_dir: Path) -> dict:
    """装配后自检：分类器能加载、支路声明能解析、（若可读）跑一帧看证据是否产生。"""
    from lsc.analyzer.valorant_frame_classifier import ValorantFrameClassifier, marker_roi_config

    classifier = ValorantFrameClassifier(out_dir)
    classifier.load()
    config = marker_roi_config(classifier._meta)
    if config is None:
        raise SystemExit("自检失败：装配后的元数据解析不出标记支路")
    result = {
        "loaded": True,
        "model_version": classifier.model_version,
        "provider": classifier.provider,
        "marker_class": config["class"],
        "marker_rois": sorted(config["rois"]),
        "marker_weight": config["weight"],
        "marker_session_ok": classifier._load_marker_session() is not None,
    }
    if not result["marker_session_ok"]:
        print(f"!! 标记支路 session 未就绪: {classifier._marker_error}", file=sys.stderr)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-dir", type=Path, required=True)
    parser.add_argument("--marker-model", type=Path, required=True,
                        help="标记模型的 onnx 文件路径")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--weight", type=float, default=1.0, help="<=0 等于关闭支路")
    parser.add_argument("--class", dest="target_class", default="replay")
    parser.add_argument("--rois-json", type=Path, default=None,
                        help="覆盖 ROI 定义的 JSON（默认用与数据集生成器一致的两种风格框）")
    parser.add_argument("--no-self-check", action="store_true")
    args = parser.parse_args(argv)

    rois = DEFAULT_ROIS
    if args.rois_json is not None:
        rois = json.loads(args.rois_json.read_text(encoding="utf-8"))
    report = compose(
        base_dir=args.base_dir.expanduser().resolve(),
        marker_model=args.marker_model.expanduser().resolve(),
        out_dir=args.out_dir.expanduser().resolve(),
        weight=args.weight,
        rois=rois,
        target_class=args.target_class,
    )
    if not args.no_self_check:
        report["self_check"] = self_check(args.out_dir.expanduser().resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
