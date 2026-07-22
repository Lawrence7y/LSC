#!/usr/bin/env python3
"""Audit Codex human labels for likely mislabels."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np

CODEX = Path(
    r"D:\Project\直播切片多人\.worktrees\valorant-frame-labeling-pilot"
    r"\datasets\valorant_phase\annotations\new_broadcast_20260721"
)
ANN = Path.home() / "LSC" / "datasets" / "valorant_phase" / "annotate"


def imread_unicode(p: Path):
    data = np.fromfile(str(p), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def main() -> None:
    labels = json.loads((CODEX / "labels.json").read_text(encoding="utf-8"))
    queue = json.loads((CODEX / "queue.json").read_text(encoding="utf-8"))
    details = json.loads(
        (ANN / "blind_codex_human_gt_details.json").read_text(encoding="utf-8")
    )["details"]
    pred_by_id = {d["id"]: d for d in details}

    by_vid: dict[str, list] = defaultdict(list)
    for item in queue:
        by_vid[item["video_id"]].append(item)
    for items in by_vid.values():
        items.sort(key=lambda x: x["timestamp_sec"])

    suspects: list[dict] = []

    def add(kind: str, item: dict, note: str, severity: int) -> None:
        d = pred_by_id.get(item["id"], {})
        suspects.append(
            {
                "kind": kind,
                "severity": severity,
                "id": item["id"],
                "video_id": item["video_id"],
                "timestamp_sec": item["timestamp_sec"],
                "rel_path": item["rel_path"],
                "gt": labels[item["id"]]["label"],
                "pred": d.get("pred"),
                "conf": d.get("conf"),
                "note": note,
            }
        )

    for items in by_vid.values():
        for i, item in enumerate(items):
            gt = labels[item["id"]]["label"]
            prev = labels[items[i - 1]["id"]]["label"] if i else None
            nxt = labels[items[i + 1]["id"]]["label"] if i + 1 < len(items) else None
            if prev and nxt and prev == nxt and gt != prev:
                add("island", item, f"alone {gt} between {prev}", 3)

    for item in queue:
        d = pred_by_id.get(item["id"])
        if not d or d["ok"]:
            continue
        gt = labels[item["id"]]["label"]
        if d["conf"] >= 0.95:
            add(
                "high_conf_disagree",
                item,
                f"model={d['pred']}@{d['conf']:.3f} vs gt={gt}",
                3,
            )
        elif d["conf"] >= 0.85:
            add(
                "high_conf_disagree",
                item,
                f"model={d['pred']}@{d['conf']:.3f} vs gt={gt}",
                2,
            )
        elif d["conf"] >= 0.70:
            add(
                "med_conf_disagree",
                item,
                f"model={d['pred']}@{d['conf']:.3f} vs gt={gt}",
                1,
            )

    # merge by id
    best: dict[str, dict] = {}
    for s in suspects:
        cur = best.get(s["id"])
        if cur is None:
            best[s["id"]] = s
            continue
        kinds = sorted(set(cur["kind"].split("+")) | {s["kind"]})
        merged = dict(s)
        merged["kind"] = "+".join(kinds)
        merged["note"] = cur["note"] + " | " + s["note"]
        merged["severity"] = max(cur["severity"], s["severity"])
        best[s["id"]] = merged

    uniq = sorted(
        best.values(),
        key=lambda x: (-x["severity"], x["video_id"], x["timestamp_sec"]),
    )
    islands = [s for s in uniq if "island" in s["kind"]]
    hc = [s for s in uniq if "high_conf" in s["kind"]]

    summary = {
        "n_labels": len(labels),
        "n_suspects": len(uniq),
        "n_islands": len(islands),
        "n_high_conf_disagree": len(hc),
        "by_kind": dict(Counter(s["kind"] for s in uniq)),
        "by_severity": dict(Counter(s["severity"] for s in uniq)),
        "island_gt_dist": dict(Counter(s["gt"] for s in islands)),
        "island_pairs": dict(
            Counter(f"{s['gt']}(between {s['note'].split()[-1]})" for s in islands)
        ),
        "hc_error_types": dict(
            Counter(f"{s['gt']}->{s['pred']}" for s in hc)
        ),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\n=== ISLANDS ===")
    for s in islands:
        conf = f"{s['conf']:.2f}" if s["conf"] is not None else "-"
        print(
            f"sev{s['severity']} {s['video_id']} t={s['timestamp_sec']:.0f}s "
            f"gt={s['gt']} pred={s['pred']}@{conf} | {s['note']}"
        )

    out = ANN / "label_audit_codex_suspects.json"
    out.write_text(
        json.dumps(
            {"summary": summary, "suspects": uniq, "islands": islands},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nwrote {out}")

    # export island thumbs for visual review
    thumb_dir = ANN / "label_audit_thumbs"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    for s in islands:
        src = CODEX / s["rel_path"]
        img = imread_unicode(src)
        if img is None:
            continue
        h, w = img.shape[:2]
        scale = 480 / max(h, w)
        small = cv2.resize(img, (int(w * scale), int(h * scale)))
        name = (
            f"isl_{s['video_id'].split('_')[1]}_t{int(s['timestamp_sec']):04d}_"
            f"gt-{s['gt']}_pred-{s['pred']}.jpg"
        )
        cv2.imencode(".jpg", small)[1].tofile(str(thumb_dir / name))
    print(f"thumbs={len(list(thumb_dir.glob('isl_*.jpg')))} -> {thumb_dir}")


if __name__ == "__main__":
    main()
