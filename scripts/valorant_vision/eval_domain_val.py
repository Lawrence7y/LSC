# -*- coding: utf-8 -*-
"""在赛事/解说域 val 帧上对比 valorant_phase 模型（v1 vs v2）。

评估集：数据集 val/ 目录下 broadcast_xuenai_*（2026-08-04 新标注赛事流）。
输出总体准确率与各类召回率，用于量化域偏移修复效果。

用法:
  python scripts/valorant_vision/eval_domain_val.py --model-dir <dir> [--tag v1]
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

DATA = Path.home() / "LSC" / "datasets" / "valorant_phase"
LABELS = ("non_game", "buy", "combat", "result", "replay")
DOMAIN_PREFIXES = ("broadcast_xuenai_",)


def collect_samples() -> list[tuple[Path, int]]:
    samples: list[tuple[Path, int]] = []
    for idx, label in enumerate(LABELS):
        d = DATA / "val" / label
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.jpg")):
            if p.name.startswith(DOMAIN_PREFIXES):
                samples.append((p, idx))
    return samples


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--tag", default="model")
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from lsc.analyzer.valorant_frame_classifier import ValorantFrameClassifier

    samples = collect_samples()
    if not samples:
        print("!! 未找到域内 val 样本")
        return
    print(f"[{args.tag}] 域内 val 样本: {len(samples)} 帧, provider 加载中...")

    clf = ValorantFrameClassifier(model_dir=args.model_dir)
    clf.load()
    print(f"[{args.tag}] model={clf.model_version} provider={clf.provider}")

    # 按文件名时间戳排序，保持时序 stacking 语义
    samples.sort(key=lambda item: item[0].name)
    imgs = []
    import cv2

    for p, _ in samples:
        img = cv2.imread(str(p))
        imgs.append(img)

    probs = clf.predict_batch(imgs)
    total = len(samples)
    correct = 0
    per_total = Counter()
    per_hit = Counter()
    conf_sum = defaultdict(float)
    low_conf = 0
    for (p, y), row in zip(samples, probs):
        pred = int(row.argmax())
        per_total[y] += 1
        conf_sum[y] += float(row[y])
        if pred == y:
            correct += 1
            per_hit[y] += 1
        if float(row.max()) < 0.7:
            low_conf += 1

    print(f"\n=== [{args.tag}] 域内 val 评估（赛事流） ===")
    print(f"总体准确率: {correct}/{total} = {correct/total:.4f}")
    print(f"低置信(<0.7)帧占比: {low_conf}/{total} = {low_conf/total:.2%}")
    print("各类召回率:")
    for idx, label in enumerate(LABELS):
        n = per_total.get(idx, 0)
        if n == 0:
            continue
        rec = per_hit.get(idx, 0) / n
        avg_conf = conf_sum[idx] / n
        print(f"  {label:9s}: recall={rec:.3f} ({per_hit.get(idx,0)}/{n}) avg_conf={avg_conf:.3f}")


if __name__ == "__main__":
    main()
