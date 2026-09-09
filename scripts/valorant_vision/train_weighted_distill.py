#!/usr/bin/env python3
"""Fine-tune the Valorant classifier with weighted labels and teacher distillation.

This is intentionally separate from ``train_export.py``.  It keeps every
sample in the training set, while treating human labels as stronger evidence
than pseudo-labels and using the current ONNX model as a rehearsal teacher.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LABELS = ("non_game", "buy", "combat", "result", "replay")
INPUT_SIZE = 224
NORMALIZE_MEAN = (0.485, 0.456, 0.406)
NORMALIZE_STD = (0.229, 0.224, 0.225)
DEFAULT_SEED = 20260907


@dataclass(frozen=True)
class Sample:
    path: Path
    label: int
    sample_weight: float
    distill_weight: float
    source: str


def pseudo_sample_weight(confidence: float | None) -> float:
    """Return a non-zero weight for every pseudo-label confidence bucket."""
    if confidence is None:
        return 0.05
    if confidence < 0.55:
        return 0.05
    if confidence < 0.70:
        return 0.15
    return 0.30


def _win_path(raw: Any) -> Path:
    text = str(raw)
    if text.startswith("/mnt/") and len(text) > 6:
        drive = text[5].upper()
        return Path(f"{drive}:{text[6:].replace('/', chr(92))}")
    return Path(text)


def _path_key(path: Path) -> str:
    # Windows/WSL may render the repository's Chinese parent path differently
    # across processes.  The generated training filenames are unique, so the
    # label directory plus basename is a stable cache key without that prefix.
    return f"{path.parent.name}/{path.name}".casefold()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_no}: row must be an object")
        rows.append(value)
    return rows


def _new_sample_metadata(manifest_path: Path) -> dict[str, tuple[str, float]]:
    metadata: dict[str, tuple[str, float]] = {}
    for row in _read_jsonl(manifest_path):
        label = str(row.get("label") or "")
        if label not in LABELS:
            raise ValueError(f"invalid label in {manifest_path}: {label!r}")
        frame_path = _win_path(row.get("frame_path"))
        source = str(row.get("label_source") or "coarse_model")
        confidence = row.get("coarse_confidence")
        try:
            conf = float(confidence) if confidence is not None else 0.0
        except (TypeError, ValueError):
            conf = 0.0
        metadata[_path_key(frame_path)] = (source, conf)
    return metadata


def collect_samples(data_dir: Path, new_manifest: Path | None = None) -> list[Sample]:
    new_metadata = _new_sample_metadata(new_manifest) if new_manifest else {}
    samples: list[Sample] = []
    for label_index, label in enumerate(LABELS):
        label_dir = data_dir / "train" / label
        for path in sorted(label_dir.glob("*.jpg")):
            source_conf = new_metadata.get(_path_key(path))
            if source_conf is None:
                source = "original"
                sample_weight = 1.0
                distill_weight = 0.50
            elif source_conf[0] == "human":
                source = "human"
                sample_weight = 2.0
                distill_weight = 0.10
            else:
                source = "coarse_model"
                sample_weight = pseudo_sample_weight(source_conf[1])
                distill_weight = 0.50
            samples.append(Sample(path, label_index, sample_weight, distill_weight, source))
    return samples


def apply_hard_sample_weights(
    samples: list[Sample],
    hard_manifest: Path,
) -> list[Sample]:
    """Increase supervision for model errors mined from the training split."""
    metadata: dict[str, tuple[float, float]] = {}
    for row in _read_jsonl(hard_manifest):
        path = _win_path(row.get("frame_path"))
        try:
            sample_multiplier = max(1.0, float(row.get("hard_weight", 1.0)))
            distill_multiplier = min(1.0, max(0.0, float(row.get("hard_distill_weight", 0.5))))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid hard-sample weights in {hard_manifest}") from exc
        metadata[_path_key(path)] = (sample_multiplier, distill_multiplier)

    adjusted: list[Sample] = []
    for sample in samples:
        weights = metadata.get(_path_key(sample.path))
        if weights is None:
            adjusted.append(sample)
            continue
        sample_multiplier, distill_multiplier = weights
        adjusted.append(
            Sample(
                path=sample.path,
                label=sample.label,
                sample_weight=sample.sample_weight * sample_multiplier,
                distill_weight=distill_multiplier,
                source=f"{sample.source}+hard",
            )
        )
    return adjusted


def _seed_everything(seed: int) -> None:
    random.seed(seed)
    import numpy as np
    import torch

    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def _read_image(path: Path):
    import cv2
    import numpy as np

    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"failed to read image: {path}")
    return image


def _teacher_targets(samples: list[Sample], teacher_dir: Path, cache_path: Path):
    import numpy as np

    if cache_path.is_file():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if isinstance(cached, dict) and all(_path_key(item.path) in cached for item in samples):
            return {
                _path_key(item.path): np.asarray(cached[_path_key(item.path)], dtype=np.float32)
                for item in samples
            }

    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from lsc.analyzer.valorant_frame_classifier import ValorantFrameClassifier

    teacher = ValorantFrameClassifier(teacher_dir)
    teacher.load()
    targets: dict[str, Any] = {}
    for start in range(0, len(samples), 64):
        batch = samples[start : start + 64]
        images = [_read_image(item.path) for item in batch]
        probabilities = teacher.predict_batch(images)
        for item, distribution in zip(batch, probabilities, strict=True):
            targets[_path_key(item.path)] = [float(value) for value in distribution]
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(targets, ensure_ascii=False), encoding="utf-8")
    return {key: np.asarray(value, dtype=np.float32) for key, value in targets.items()}


def _collect_validation(data_dir: Path):
    return [
        (path, label_index)
        for label_index, label in enumerate(LABELS)
        for path in sorted((data_dir / "val" / label).glob("*.jpg"))
    ]


def _dataset_digest(samples: list[Sample], validation: list[tuple[Path, int]]) -> str:
    hasher = hashlib.sha256()
    for path, label in sorted(
        [(item.path, item.label) for item in samples] + validation,
        key=lambda item: str(item[0]),
    ):
        hasher.update(f"{path}:{label}:".encode())
        hasher.update(path.read_bytes())
    return hasher.hexdigest()


def train(
    samples: list[Sample],
    validation: list[tuple[Path, int]],
    teacher_targets: dict[str, Any],
    *,
    out_dir: Path,
    epochs: int,
    freeze_epochs: int,
    seed: int,
) -> None:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    from PIL import Image
    from torch.utils.data import DataLoader, Dataset
    from torchvision import models, transforms

    _seed_everything(seed)

    class FrameDataset(Dataset):
        def __init__(self, items, transform, teacher=None):
            self.items = items
            self.transform = transform
            self.teacher = teacher

        def __len__(self):
            return len(self.items)

        def __getitem__(self, index):
            if self.teacher is None:
                path, label = self.items[index]
                with Image.open(path) as image:
                    image = image.convert("RGB")
                return self.transform(image), label
            sample = self.items[index]
            with Image.open(sample.path) as image:
                image = image.convert("RGB")
            return (
                self.transform(image),
                sample.label,
                sample.sample_weight,
                sample.distill_weight,
                torch.tensor(self.teacher[_path_key(sample.path)], dtype=torch.float32),
            )

    class WeightedDataset(Dataset):
        def __init__(self, items, transform, teacher):
            self.items = items
            self.transform = transform
            self.teacher = teacher

        def __len__(self):
            return len(self.items)

        def __getitem__(self, index):
            sample = self.items[index]
            with Image.open(sample.path) as image:
                image = image.convert("RGB")
            return (
                self.transform(image),
                sample.label,
                sample.sample_weight,
                sample.distill_weight,
                torch.tensor(self.teacher[_path_key(sample.path)], dtype=torch.float32),
            )

    train_tf = transforms.Compose([
        transforms.Resize((INPUT_SIZE, INPUT_SIZE)),
        transforms.RandomHorizontalFlip(p=0.15),
        transforms.ColorJitter(brightness=0.2, contrast=0.2, saturation=0.2, hue=0.03),
        transforms.ToTensor(),
        transforms.Normalize(NORMALIZE_MEAN, NORMALIZE_STD),
    ])
    eval_tf = transforms.Compose([
        transforms.Resize((INPUT_SIZE, INPUT_SIZE)),
        transforms.ToTensor(),
        transforms.Normalize(NORMALIZE_MEAN, NORMALIZE_STD),
    ])

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = models.mobilenet_v3_small(weights=models.MobileNet_V3_Small_Weights.DEFAULT)
    model.classifier[-1] = nn.Linear(model.classifier[-1].in_features, len(LABELS))
    model.to(device)

    train_dataset = WeightedDataset(samples, train_tf, teacher_targets)
    val_dataset = FrameDataset(validation, eval_tf)
    train_loader = DataLoader(train_dataset, batch_size=32, shuffle=True, num_workers=0)
    val_loader = DataLoader(val_dataset, batch_size=64, shuffle=False, num_workers=0)

    class_counts = torch.zeros(len(LABELS), dtype=torch.float32)
    for item in samples:
        class_counts[item.label] += item.sample_weight
    class_counts = torch.clamp(class_counts, min=1.0)
    class_weights = torch.sqrt(class_counts.sum() / (len(LABELS) * class_counts))
    class_weights = torch.clamp(class_weights, min=0.6, max=3.5).to(device)

    for parameter in model.features.parameters():
        parameter.requires_grad = freeze_epochs <= 0
    optimizer = torch.optim.AdamW(model.parameters(), lr=5e-5, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=max(epochs, 1))
    best_state = None
    best_macro_f1 = -1.0
    best_acc = -1.0
    best_epoch = 0
    temperature = 2.0

    for epoch in range(1, epochs + 1):
        if epoch == freeze_epochs + 1:
            for parameter in model.features.parameters():
                parameter.requires_grad = True
        model.train()
        running_loss = 0.0
        for x, y, sample_weight, distill_weight, teacher in train_loader:
            x, y = x.to(device), y.to(device)
            sample_weight = sample_weight.to(device)
            distill_weight = distill_weight.to(device)
            teacher = teacher.to(device)
            optimizer.zero_grad()
            logits = model(x)
            ce_each = F.cross_entropy(logits, y, weight=class_weights, reduction="none")
            ce = (ce_each * sample_weight).sum() / sample_weight.sum().clamp_min(1e-6)
            student_log_probs = F.log_softmax(logits / temperature, dim=1)
            teacher_probs = teacher.clamp_min(1e-6)
            teacher_probs = teacher_probs / teacher_probs.sum(dim=1, keepdim=True)
            kd_each = F.kl_div(student_log_probs, teacher_probs, reduction="none").sum(dim=1)
            kd = (kd_each * distill_weight).sum() / distill_weight.sum().clamp_min(1e-6)
            loss = ce + (temperature * temperature * 0.5) * kd
            loss.backward()
            optimizer.step()
            running_loss += float(loss.item())
        scheduler.step()

        model.eval()
        confusion = [[0] * len(LABELS) for _ in LABELS]
        with torch.no_grad():
            for x, y in val_loader:
                logits = model(x.to(device))
                predictions = logits.argmax(dim=1).cpu().tolist()
                for truth, prediction in zip(y.tolist(), predictions, strict=True):
                    confusion[truth][prediction] += 1
        f1s = []
        for index in range(len(LABELS)):
            tp = confusion[index][index]
            fp = sum(confusion[row][index] for row in range(len(LABELS)) if row != index)
            fn = sum(confusion[index][col] for col in range(len(LABELS)) if col != index)
            precision = tp / (tp + fp) if tp + fp else 0.0
            recall = tp / (tp + fn) if tp + fn else 0.0
            f1s.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
        total = sum(map(sum, confusion))
        acc = sum(confusion[index][index] for index in range(len(LABELS))) / max(total, 1)
        macro_f1 = sum(f1s) / len(f1s)
        print(
            f"epoch {epoch}/{epochs} loss={running_loss / max(len(train_loader), 1):.4f} "
            f"val_acc={acc:.4f} macro_f1={macro_f1:.4f} "
            + " ".join(f"{LABELS[i]}_f1={f1s[i]:.2f}" for i in range(len(LABELS)))
        )
        if macro_f1 > best_macro_f1 or (abs(macro_f1 - best_macro_f1) < 1e-6 and acc > best_acc):
            best_macro_f1, best_acc, best_epoch = macro_f1, acc, epoch
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}

    if best_state is not None:
        model.load_state_dict(best_state)
    print(f"using best checkpoint epoch={best_epoch} macro_f1={best_macro_f1:.4f} val_acc={best_acc:.4f}")

    out_dir.mkdir(parents=True, exist_ok=True)
    onnx_path = out_dir / "valorant_phase_v1.onnx"
    meta_path = out_dir / "valorant_phase_v1.json"
    model_cpu = model.to("cpu").eval()
    export_model = nn.Sequential(model_cpu, nn.Softmax(dim=1))
    dummy = torch.randn(1, 3, INPUT_SIZE, INPUT_SIZE)
    torch.onnx.export(
        export_model,
        dummy,
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
        "dataset_version": f"weighted-distill-{len(samples)}t-{len(validation)}v",
        "thresholds": {"stable_prob": 0.55, "high_prob": 0.80},
        "seed": seed,
        "train_count": len(samples),
        "val_count": len(validation),
        "dataset_digest": _dataset_digest(samples, validation),
        "training_strategy": "human_weighted_pseudo_labels_with_teacher_distillation",
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"导出完成: {onnx_path}")
    print(f"元数据:   {meta_path}")
    print(f"sha256:   {digest}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="加权粗标 + 旧模型蒸馏的 Valorant 增量训练")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--new-manifest", type=Path, required=True)
    parser.add_argument("--teacher-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--teacher-cache", type=Path, default=None)
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--freeze-epochs", type=int, default=2)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data_dir = args.data_dir.expanduser().resolve()
    new_manifest = args.new_manifest.expanduser().resolve()
    teacher_dir = args.teacher_dir.expanduser().resolve()
    out_dir = args.out_dir.expanduser().resolve()
    samples = collect_samples(data_dir, new_manifest)
    validation = _collect_validation(data_dir)
    if not samples or not validation:
        raise SystemExit("训练集或验证集为空")
    cache_path = (args.teacher_cache or out_dir / "teacher_targets.json").expanduser().resolve()
    targets = _teacher_targets(samples, teacher_dir, cache_path)
    train(
        samples,
        validation,
        targets,
        out_dir=out_dir,
        epochs=max(1, int(args.epochs)),
        freeze_epochs=max(0, int(args.freeze_epochs)),
        seed=int(args.seed),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
