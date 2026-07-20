"""训练 Valorant 五分类相位模型并导出 ONNX + 契约元数据。

数据集目录布局（由 ``extract_frames.py`` 产出）::

    <data-dir>/
      train/
        non_game/<video_id>_<timestamp_ms>.jpg
        buy/...
        combat/...
        result/...
        replay/...
      val/...
      test/...

每类标签下为 JPEG 单帧；``train`` / ``val`` 用于训练与阈值标定，``test`` 保留作盲测。

用法::

    python scripts/valorant_vision/train_export.py \\
        --data-dir ~/LSC/datasets/valorant_phase \\
        --out-dir ~/LSC/models/valorant_phase_v1 \\
        --epochs 10

成功后在 ``--out-dir`` 写出 ``valorant_phase_v1.onnx`` 与 ``valorant_phase_v1.json``。
可将二者复制到 ``lsc/analyzer/models/``，或设置环境变量 ``LSC_VALORANT_MODEL_DIR``。

需要: ``torch``, ``torchvision``；导出 ONNX 另需 ``onnx``。``--export-int8`` 尝试动态量化（可选）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

LABELS = ("non_game", "buy", "combat", "result", "replay")
SPLITS = ("train", "val")
INPUT_SIZE = 224
NORMALIZE_MEAN = (0.485, 0.456, 0.406)
NORMALIZE_STD = (0.229, 0.224, 0.225)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train MobileNetV3-Small and export valorant_phase_v1 ONNX")
    parser.add_argument(
        "--data-dir",
        type=Path,
        required=True,
        help="Dataset root with train/val/<label>/*.jpg (see extract_frames.py)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="Output directory for valorant_phase_v1.onnx and .json",
    )
    parser.add_argument("--epochs", type=int, default=10, help="Training epochs (default: 10)")
    parser.add_argument(
        "--export-int8",
        action="store_true",
        help="Apply dynamic INT8 quantization to exported ONNX (best-effort)",
    )
    return parser.parse_args()


def _collect_images(data_dir: Path, split: str) -> list[tuple[Path, int]]:
    samples: list[tuple[Path, int]] = []
    split_dir = data_dir / split
    if not split_dir.is_dir():
        return samples
    for label_idx, label in enumerate(LABELS):
        label_dir = split_dir / label
        if not label_dir.is_dir():
            continue
        for path in sorted(label_dir.glob("*.jpg")):
            if path.is_file():
                samples.append((path, label_idx))
    return samples


def _require_torch():
    try:
        import torch  # noqa: F401
        import torchvision  # noqa: F401
    except ImportError:
        print(
            "错误: 未安装 torch/torchvision，无法训练。\n"
            "请安装 PyTorch 后重试，或使用 tests/fixtures/valorant_vision 中的 stub 模型做 CI。",
            file=sys.stderr,
        )
        raise SystemExit(1)


def _build_model(num_classes: int):
    import torchvision.models as models

    weights = models.MobileNet_V3_Small_Weights.DEFAULT
    model = models.mobilenet_v3_small(weights=weights)
    in_features = model.classifier[-1].in_features
    model.classifier[-1] = __import__("torch").nn.Linear(in_features, num_classes)
    return model, weights


def _train_and_export(
    train_samples: list[tuple[Path, int]],
    val_samples: list[tuple[Path, int]],
    *,
    out_dir: Path,
    epochs: int,
    export_int8: bool,
) -> None:
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, Dataset
    from torchvision import transforms
    from PIL import Image

    class _FrameDataset(Dataset):
        def __init__(self, items: list[tuple[Path, int]], transform) -> None:
            self._items = items
            self._transform = transform

        def __len__(self) -> int:
            return len(self._items)

        def __getitem__(self, idx: int):
            path, label = self._items[idx]
            with Image.open(path) as img:
                img = img.convert("RGB")
            return self._transform(img), label

    train_tf = transforms.Compose([
        transforms.Resize((INPUT_SIZE, INPUT_SIZE)),
        transforms.ColorJitter(brightness=0.15, contrast=0.15),
        transforms.RandomApply([transforms.GaussianBlur(kernel_size=3)], p=0.2),
        transforms.ToTensor(),
        transforms.Normalize(NORMALIZE_MEAN, NORMALIZE_STD),
    ])
    eval_tf = transforms.Compose([
        transforms.Resize((INPUT_SIZE, INPUT_SIZE)),
        transforms.ToTensor(),
        transforms.Normalize(NORMALIZE_MEAN, NORMALIZE_STD),
    ])

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model, _weights = _build_model(len(LABELS))
    model.to(device)

    train_loader = DataLoader(
        _FrameDataset(train_samples, train_tf),
        batch_size=32,
        shuffle=True,
        num_workers=0,
    )
    val_loader = DataLoader(
        _FrameDataset(val_samples, eval_tf),
        batch_size=32,
        shuffle=False,
        num_workers=0,
    )

    criterion = nn.CrossEntropyLoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-4)

    for epoch in range(1, epochs + 1):
        model.train()
        running_loss = 0.0
        for batch_x, batch_y in train_loader:
            batch_x, batch_y = batch_x.to(device), batch_y.to(device)
            optimizer.zero_grad()
            logits = model(batch_x)
            loss = criterion(logits, batch_y)
            loss.backward()
            optimizer.step()
            running_loss += float(loss.item())
        model.eval()
        correct = 0
        total = 0
        with torch.no_grad():
            for batch_x, batch_y in val_loader:
                batch_x, batch_y = batch_x.to(device), batch_y.to(device)
                preds = model(batch_x).argmax(dim=1)
                correct += int((preds == batch_y).sum().item())
                total += int(batch_y.size(0))
        acc = (correct / total) if total else 0.0
        print(f"epoch {epoch}/{epochs} loss={running_loss / max(len(train_loader), 1):.4f} val_acc={acc:.4f}")

    out_dir.mkdir(parents=True, exist_ok=True)
    onnx_path = out_dir / "valorant_phase_v1.onnx"
    meta_path = out_dir / "valorant_phase_v1.json"

    model.eval()
    dummy = torch.randn(1, 3, INPUT_SIZE, INPUT_SIZE, device=device)
    model_cpu = model.to("cpu")
    export_model = nn.Sequential(model_cpu, nn.Softmax(dim=1))
    torch.onnx.export(
        export_model,
        dummy.cpu(),
        str(onnx_path),
        input_names=["input"],
        output_names=["probs"],
        dynamic_axes={"input": {0: "N"}, "probs": {0: "N"}},
        opset_version=13,
    )

    if export_int8:
        int8_path = out_dir / "valorant_phase_v1.int8.tmp.onnx"
        try:
            from onnxruntime.quantization import QuantType, quantize_dynamic

            quantize_dynamic(str(onnx_path), str(int8_path), weight_type=QuantType.QUInt8)
            int8_path.replace(onnx_path)
            print(f"INT8 运行时模型已写出: {onnx_path}")
        except Exception as exc:  # noqa: BLE001 — 量化可选
            int8_path.unlink(missing_ok=True)
            raise RuntimeError(f"INT8 量化失败: {exc}") from exc

    digest = hashlib.sha256(onnx_path.read_bytes()).hexdigest()
    dataset_version = f"manual-{len(train_samples)}t-{len(val_samples)}v"
    meta = {
        "model_version": "valorant_phase_v1",
        "class_names": list(LABELS),
        "input_size": [INPUT_SIZE, INPUT_SIZE],
        "color_order": "RGB",
        "normalize_mean": list(NORMALIZE_MEAN),
        "normalize_std": list(NORMALIZE_STD),
        "threshold_version": "v1",
        "sha256": digest,
        "dataset_version": dataset_version,
        "thresholds": {
            "stable_prob": 0.55,
            "high_prob": 0.80,
        },
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"导出完成: {onnx_path}")
    print(f"元数据:   {meta_path}")
    print(f"sha256:   {digest}")


def main() -> None:
    args = _parse_args()
    data_dir = args.data_dir.expanduser().resolve()
    if not data_dir.is_dir():
        print(f"错误: 数据集目录不存在: {data_dir}", file=sys.stderr)
        raise SystemExit(1)

    train_samples = _collect_images(data_dir, "train")
    val_samples = _collect_images(data_dir, "val")
    if not train_samples:
        print(
            f"错误: 数据集为空（{data_dir}/train/<label>/*.jpg 下无 JPEG）。\n"
            "请先运行 extract_frames.py 生成标注数据后再训练。",
            file=sys.stderr,
        )
        raise SystemExit(1)

    _require_torch()
    _train_and_export(
        train_samples,
        val_samples,
        out_dir=args.out_dir.expanduser().resolve(),
        epochs=max(1, int(args.epochs)),
        export_int8=bool(args.export_int8),
    )


if __name__ == "__main__":
    main()
