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
import time
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


def install_in_place(
    *,
    base_dir: Path,
    marker_model: Path,
    weight: float,
    rois: dict[str, list[float]],
    target_class: str,
) -> dict:
    """把标记支路**装进生产模型目录本体**（原地），让真实程序无需环境变量即可加载。

    安全性：
    - **不动** `valorant_phase_v1.onnx`（`sha256` 校验针对它，保持有效）；
    - 先把原元数据备份为 `valorant_phase_v1.json.bak-<时间戳>`，回滚只需还原该文件；
    - 已安装过（元数据里已有 `marker_roi_branch`）时**拒绝重复安装**，避免叠加声明。
    """
    base_onnx = base_dir / BASE_ONNX
    base_meta = base_dir / BASE_META
    for path in (base_onnx, base_meta, marker_model):
        if not path.is_file():
            raise SystemExit(f"缺少文件: {path}")
    meta = json.loads(base_meta.read_text(encoding="utf-8"))
    if "marker_roi_branch" in meta:
        raise SystemExit(
            f"{base_meta} 里已有 marker_roi_branch；要换模型请先回滚（还原 .bak-* 并删 valorant_marker_v1.onnx）"
        )
    backup = base_meta.with_name(BASE_META + ".bak-" + time.strftime("%Y%m%d_%H%M%S"))
    shutil.copyfile(base_meta, backup)
    shutil.copyfile(marker_model, base_dir / MARKER_ONNX)
    marker_meta_src = marker_model.with_suffix(".json")
    if marker_meta_src.is_file():
        shutil.copyfile(marker_meta_src, base_dir / "valorant_marker_v1.json")
    meta["marker_roi_branch"] = {
        "model_path": MARKER_ONNX,
        "weight": float(weight),
        "rois": {name: [float(v) for v in box] for name, box in rois.items()},
        "class": target_class,
        "note": "标记支路：标记区按原生分辨率放大成独立输入，与整帧证据取 max（只增不减）",
    }
    base_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "mode": "in_place",
        "production_dir": str(base_dir),
        "meta_backup": str(backup),
        "marker_onnx": str(base_dir / MARKER_ONNX),
        "weight": float(weight),
        "rois": list(rois),
        "class": target_class,
        "rollback": f"复制回 {backup} 为 {BASE_META} 并删除 {base_dir / MARKER_ONNX}",
    }


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
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="候选目录（与 --in-place 二选一）")
    parser.add_argument("--in-place", action="store_true",
                        help="装进 --base-dir 生产目录本体（真实环境测试/上线）")
    parser.add_argument("--weight", type=float, default=1.0, help="<=0 等于关闭支路")
    parser.add_argument("--class", dest="target_class", default="replay")
    parser.add_argument("--rois-json", type=Path, default=None,
                        help="覆盖 ROI 定义的 JSON（默认用与数据集生成器一致的两种风格框）")
    parser.add_argument("--no-self-check", action="store_true")
    args = parser.parse_args(argv)

    rois = DEFAULT_ROIS
    if args.rois_json is not None:
        rois = json.loads(args.rois_json.read_text(encoding="utf-8"))
    if bool(args.in_place) == bool(args.out_dir):
        parser.error("必须且只能指定 --out-dir 或 --in-place 之一")
    base_dir = args.base_dir.expanduser().resolve()
    marker_model = args.marker_model.expanduser().resolve()
    if args.in_place:
        report = install_in_place(
            base_dir=base_dir, marker_model=marker_model,
            weight=args.weight, rois=rois, target_class=args.target_class,
        )
        target_dir = base_dir
    else:
        target_dir = args.out_dir.expanduser().resolve()
        report = compose(
            base_dir=base_dir, marker_model=marker_model,
            out_dir=target_dir, weight=args.weight, rois=rois,
            target_class=args.target_class,
        )
    if not args.no_self_check:
        report["self_check"] = self_check(target_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
