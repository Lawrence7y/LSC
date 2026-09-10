#!/usr/bin/env python3
"""Fine-tune the current ONNX classifier without reinitializing its weights.

The production model is stored as ONNX, so ``onnx2torch`` is used to recover a
trainable PyTorch graph.  Human labels receive stronger supervision, while all
pseudo-labelled frames remain in the training set with confidence-aware
weights.  A KL term against the original model prevents catastrophic drift.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from train_weighted_distill import (  # noqa: E402
    LABELS,
    INPUT_SIZE,
    NORMALIZE_MEAN,
    NORMALIZE_STD,
    Sample,
    _collect_validation,
    _dataset_digest,
    _path_key,
    _teacher_targets,
    apply_hard_sample_weights,
    collect_samples,
)


# 运行时**后处理契约**：这些键不由训练产生，而是推理侧的行为声明
# （融合权重 / 类专属稳定阈值）。教师模型靠它们决定"怎么把概率变成标签"，
# 微调产物必须原样继承，否则导出模型会在运行时静默退回
# "无融合 + 默认阈值"，与基线不可比（2026-09-10 实测踩到：v4_fused 的
# broadcast_input_fusion 与 class_stable_prob 会在重训导出时凭空消失）。
_INHERITED_META_KEYS: tuple[str, ...] = (
    "thresholds",
    "class_stable_prob",
    "broadcast_input_fusion",
    "calibration_note",
)


def _inherit_runtime_meta(teacher_dir: Path, meta: dict[str, Any]) -> list[str]:
    """把教师的运行时后处理契约并入导出元数据，返回实际继承到的键名。"""
    path = teacher_dir / "valorant_phase_v1.json"
    if not path.is_file():
        return []
    try:
        teacher_meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"警告: 无法读取教师元数据 {path}: {exc}", file=sys.stderr)
        return []
    if not isinstance(teacher_meta, dict):
        return []
    inherited: list[str] = []
    for key in _INHERITED_META_KEYS:
        if teacher_meta.get(key) is not None:
            meta[key] = teacher_meta[key]
            inherited.append(key)
    return inherited


def _seed_everything(seed: int) -> None:
    import numpy as np
    import torch

    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def _load_trainable_onnx(model_dir: Path):
    import onnx
    from onnx2torch import convert

    onnx_path = model_dir / "valorant_phase_v1.onnx"
    if not onnx_path.is_file():
        raise FileNotFoundError(onnx_path)
    return convert(onnx.load(str(onnx_path)))


def _read_image(path: Path):
    import cv2
    import numpy as np

    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"failed to read image: {path}")
    return image


def _make_datasets(samples: list[Sample], validation: list[tuple[Path, int]], teacher):
    import torch
    from PIL import Image
    from torch.utils.data import Dataset
    from torchvision import transforms

    train_tf = transforms.Compose([
        transforms.Resize((INPUT_SIZE, INPUT_SIZE)),
        transforms.RandomHorizontalFlip(p=0.15),
        transforms.ColorJitter(brightness=0.15, contrast=0.15, saturation=0.15, hue=0.02),
        transforms.ToTensor(),
        transforms.Normalize(NORMALIZE_MEAN, NORMALIZE_STD),
    ])
    eval_tf = transforms.Compose([
        transforms.Resize((INPUT_SIZE, INPUT_SIZE)),
        transforms.ToTensor(),
        transforms.Normalize(NORMALIZE_MEAN, NORMALIZE_STD),
    ])

    class TrainDataset(Dataset):
        def __len__(self):
            return len(samples)

        def __getitem__(self, index):
            sample = samples[index]
            with Image.open(sample.path) as image:
                image = image.convert("RGB")
            return (
                train_tf(image),
                sample.label,
                sample.sample_weight,
                sample.distill_weight,
                torch.tensor(teacher[_path_key(sample.path)], dtype=torch.float32),
            )

    class ValidationDataset(Dataset):
        def __len__(self):
            return len(validation)

        def __getitem__(self, index):
            path, label = validation[index]
            with Image.open(path) as image:
                image = image.convert("RGB")
            return eval_tf(image), label

    return TrainDataset(), ValidationDataset()


def _probabilities(output):
    import torch

    if isinstance(output, (tuple, list)):
        output = output[0]
    if not isinstance(output, torch.Tensor):
        raise TypeError(f"unexpected model output: {type(output)!r}")
    probabilities = output.float()
    return probabilities / probabilities.sum(dim=1, keepdim=True).clamp_min(1e-6)


def _metrics(confusion: list[list[int]]) -> tuple[float, float, list[float]]:
    f1s: list[float] = []
    for index in range(len(LABELS)):
        tp = confusion[index][index]
        fp = sum(confusion[row][index] for row in range(len(LABELS)) if row != index)
        fn = sum(confusion[index][col] for col in range(len(LABELS)) if col != index)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    total = sum(map(sum, confusion))
    accuracy = sum(confusion[index][index] for index in range(len(LABELS))) / max(total, 1)
    return accuracy, sum(f1s) / len(f1s), f1s


def train(
    samples: list[Sample],
    validation: list[tuple[Path, int]],
    teacher_targets: dict[str, Any],
    *,
    teacher_dir: Path,
    out_dir: Path,
    epochs: int,
    seed: int,
) -> None:
    import torch
    import torch.nn.functional as F
    from torch.utils.data import DataLoader

    _seed_everything(seed)
    model = _load_trainable_onnx(teacher_dir)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)
    train_dataset, val_dataset = _make_datasets(samples, validation, teacher_targets)
    train_loader = DataLoader(train_dataset, batch_size=32, shuffle=True, num_workers=0)
    val_loader = DataLoader(val_dataset, batch_size=64, shuffle=False, num_workers=0)

    class_counts = torch.zeros(len(LABELS), dtype=torch.float32)
    for sample in samples:
        class_counts[sample.label] += sample.sample_weight
    class_counts = torch.clamp(class_counts, min=1.0)
    class_weights = torch.sqrt(class_counts.sum() / (len(LABELS) * class_counts))
    class_weights = torch.clamp(class_weights, min=0.6, max=3.5).to(device)

    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-5, weight_decay=5e-5)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=max(epochs, 1))
    best_state = None
    best_macro_f1 = -1.0
    best_acc = -1.0
    best_epoch = 0

    for epoch in range(1, epochs + 1):
        model.train()
        running_loss = 0.0
        for x, y, sample_weight, distill_weight, teacher in train_loader:
            x, y = x.to(device), y.to(device)
            sample_weight = sample_weight.to(device)
            distill_weight = distill_weight.to(device)
            teacher = teacher.to(device)
            optimizer.zero_grad()
            probabilities = _probabilities(model(x)).clamp_min(1e-6)
            log_probabilities = probabilities.log()
            ce_each = F.nll_loss(log_probabilities, y, weight=class_weights, reduction="none")
            ce = (ce_each * sample_weight).sum() / sample_weight.sum().clamp_min(1e-6)
            teacher = teacher / teacher.sum(dim=1, keepdim=True).clamp_min(1e-6)
            kd_each = F.kl_div(log_probabilities, teacher.clamp_min(1e-6), reduction="none").sum(dim=1)
            kd = (kd_each * distill_weight).sum() / distill_weight.sum().clamp_min(1e-6)
            loss = ce + 0.25 * kd
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=2.0)
            optimizer.step()
            running_loss += float(loss.item())
        scheduler.step()

        model.eval()
        confusion = [[0] * len(LABELS) for _ in LABELS]
        with torch.no_grad():
            for x, y in val_loader:
                predictions = _probabilities(model(x.to(device))).argmax(dim=1).cpu().tolist()
                for truth, prediction in zip(y.tolist(), predictions, strict=True):
                    confusion[truth][prediction] += 1
        accuracy, macro_f1, f1s = _metrics(confusion)
        print(
            f"epoch {epoch}/{epochs} loss={running_loss / max(len(train_loader), 1):.4f} "
            f"val_acc={accuracy:.4f} macro_f1={macro_f1:.4f} "
            + " ".join(f"{LABELS[i]}_f1={f1s[i]:.2f}" for i in range(len(LABELS)))
        )
        if macro_f1 > best_macro_f1 or (abs(macro_f1 - best_macro_f1) < 1e-6 and accuracy > best_acc):
            best_macro_f1, best_acc, best_epoch = macro_f1, accuracy, epoch
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}

    if best_state is not None:
        model.load_state_dict(best_state)
    print(f"using best checkpoint epoch={best_epoch} macro_f1={best_macro_f1:.4f} val_acc={best_acc:.4f}")

    out_dir.mkdir(parents=True, exist_ok=True)
    onnx_path = out_dir / "valorant_phase_v1.onnx"
    meta_path = out_dir / "valorant_phase_v1.json"
    model_cpu = model.to("cpu").eval()
    import torch

    torch.onnx.export(
        model_cpu,
        torch.randn(1, 3, INPUT_SIZE, INPUT_SIZE),
        str(onnx_path),
        input_names=["input"],
        output_names=["probs"],
        dynamic_axes={"input": {0: "N"}, "probs": {0: "N"}},
        opset_version=13,
    )
    digest = hashlib.sha256(onnx_path.read_bytes()).hexdigest()
    meta = {
        "model_version": "valorant_phase_v1",
        "class_names": list(LABELS),
        "input_size": [INPUT_SIZE, INPUT_SIZE],
        "color_order": "RGB",
        "normalize_mean": list(NORMALIZE_MEAN),
        "normalize_std": list(NORMALIZE_STD),
        "threshold_version": "v1",
        "sha256": digest,
        "dataset_version": f"onnx-finetune-{len(samples)}t-{len(validation)}v",
        "thresholds": {"stable_prob": 0.55, "high_prob": 0.80},
        "seed": seed,
        "train_count": len(samples),
        "val_count": len(validation),
        "dataset_digest": _dataset_digest(samples, validation),
        "training_strategy": "onnx_initialized_weighted_human_pseudo_finetune",
    }
    inherited = _inherit_runtime_meta(teacher_dir, meta)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"继承教师运行时契约: {', '.join(inherited) if inherited else '（无）'}")
    print(f"导出完成: {onnx_path}")
    print(f"元数据:   {meta_path}")
    print(f"sha256:   {digest}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从现有 ONNX 权重增量微调 Valorant 模型")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--new-manifest", type=Path, required=True)
    parser.add_argument("--hard-manifest", type=Path, default=None)
    parser.add_argument("--teacher-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--teacher-cache", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20260907)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data_dir = args.data_dir.expanduser().resolve()
    new_manifest = args.new_manifest.expanduser().resolve()
    teacher_dir = args.teacher_dir.expanduser().resolve()
    out_dir = args.out_dir.expanduser().resolve()
    samples = collect_samples(data_dir, new_manifest)
    if args.hard_manifest is not None:
        samples = apply_hard_sample_weights(samples, args.hard_manifest.expanduser().resolve())
    validation = _collect_validation(data_dir)
    if not samples or not validation:
        raise SystemExit("训练集或验证集为空")
    targets = _teacher_targets(samples, teacher_dir, args.teacher_cache.expanduser().resolve())
    train(
        samples,
        validation,
        targets,
        teacher_dir=teacher_dir,
        out_dir=out_dir,
        epochs=max(1, int(args.epochs)),
        seed=int(args.seed),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
