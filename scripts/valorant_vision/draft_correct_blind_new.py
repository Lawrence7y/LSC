#!/usr/bin/env python3
"""Draft-correct two esports blind videos; report accuracy; queue for human review."""
from __future__ import annotations

import json
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ANN = Path.home() / "LSC" / "datasets" / "valorant_phase" / "annotate"
QUEUE = ANN / "queue.json"
LABELS = ANN / "labels.json"
BACKUP = ANN / "backups"

# Confirmed by visual audit
VISUAL_OVERRIDES: dict[str, str] = {
    "pov_dianjing/frame_000057.jpg": "replay",
    "pov_dianjing/frame_000092.jpg": "replay",
    "pov_dianjing/frame_000134.jpg": "replay",
    "pov_hanghang/frame_000151.jpg": "combat",  # sniper view, no REPLAY badge
}


def conf_of(item: dict) -> float:
    m = re.search(r"conf=([0-9.]+)", item.get("reason") or "")
    return float(m.group(1)) if m else 0.0


def recover_pred(item: dict, labels: dict) -> str | None:
    pred = item.get("suggested_label") or item.get("current_label")
    if pred:
        return pred
    m = re.search(r"模型[^=]*=?\s*(\w+)|模型预测 (\w+)", item.get("reason") or "")
    if m:
        return m.group(1) or m.group(2)
    m2 = re.search(r"模型=(\w+)", item.get("reason") or "")
    if m2:
        return m2.group(1)
    if item["id"] in labels:
        return labels[item["id"]].get("label")
    return None


def main() -> None:
    queue = json.loads(QUEUE.read_text(encoding="utf-8"))
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    new_items = [
        x
        for x in queue
        if x["rel_path"].startswith("pov_dianjing/") or x["rel_path"].startswith("pov_hanghang/")
    ]
    if not new_items:
        raise SystemExit("no dianjing/hanghang items")

    preds: dict[str, dict] = {}
    for item in new_items:
        pred = recover_pred(item, labels)
        preds[item["id"]] = {
            "pred": pred or "combat",
            "conf": conf_of(item),
            "rel_path": item["rel_path"],
        }

    gt: dict[str, str] = {}
    notes: dict[str, str] = {}
    for item in new_items:
        iid = item["id"]
        rel = item["rel_path"].replace("\\", "/")
        pred = preds[iid]["pred"]
        if rel in VISUAL_OVERRIDES:
            gt[iid] = VISUAL_OVERRIDES[rel]
            notes[iid] = "visual_override"
        elif pred == "result":
            # Esports folders: sampled high-conf "result" were all player-cam/timeout/stage
            gt[iid] = "non_game"
            notes[iid] = "esports_result_as_playercam"
        elif pred == "replay" and rel not in VISUAL_OVERRIDES:
            gt[iid] = "combat"
            notes[iid] = "unconfirmed_replay_to_combat"
        else:
            gt[iid] = pred
            notes[iid] = "model_pred"

    by_vid: dict[str, list] = defaultdict(list)
    for item in sorted(new_items, key=lambda x: (x["video_id"], x["timestamp_sec"])):
        by_vid[item["video_id"]].append(item)
    for items in by_vid.values():
        for i, item in enumerate(items):
            iid = item["id"]
            if notes.get(iid) in ("visual_override", "esports_result_as_playercam"):
                continue
            lab = gt[iid]
            prev = gt[items[i - 1]["id"]] if i else None
            nxt = gt[items[i + 1]["id"]] if i + 1 < len(items) else None
            conf = preds[iid]["conf"]
            if prev and nxt and prev == nxt and lab != prev and conf < 0.8:
                gt[iid] = prev
                notes[iid] = f"island_fix_from_{lab}"

    details = []
    for item in new_items:
        iid = item["id"]
        p = preds[iid]["pred"]
        g = gt[iid]
        details.append(
            {
                "id": iid,
                "rel_path": item["rel_path"],
                "video_id": item["video_id"],
                "timestamp_sec": item["timestamp_sec"],
                "pred": p,
                "gt": g,
                "conf": preds[iid]["conf"],
                "ok": p == g,
                "note": notes.get(iid, ""),
            }
        )

    n = len(details)
    correct = sum(d["ok"] for d in details)
    by_video_acc = {}
    for vid in sorted({d["video_id"] for d in details}):
        rows = [d for d in details if d["video_id"] == vid]
        by_video_acc[vid] = {
            "n": len(rows),
            "acc": round(sum(r["ok"] for r in rows) / len(rows), 4),
            "errors": sum(not r["ok"] for r in rows),
            "gt_dist": dict(Counter(r["gt"] for r in rows)),
            "pred_dist": dict(Counter(r["pred"] for r in rows)),
        }

    metrics = {
        "n": n,
        "accuracy": round(correct / n, 4),
        "correct": correct,
        "errors": n - correct,
        "gt_dist": dict(Counter(d["gt"] for d in details)),
        "pred_dist": dict(Counter(d["pred"] for d in details)),
        "error_types": {
            f"{a}->{b}": c
            for (a, b), c in Counter(
                (d["gt"], d["pred"]) for d in details if not d["ok"]
            ).most_common()
        },
        "by_video": by_video_acc,
        "caveat": (
            "草案真值：模型预测 + 电竞规则(result→non_game选手镜头; "
            "未确认replay→combat) + 少量目视确认。供复核，非最终真值。"
        ),
    }
    (ANN / "blind_new_metrics_draft.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (ANN / "blind_new_gt_draft.json").write_text(
        json.dumps({"details": details}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))

    BACKUP.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(QUEUE, BACKUP / f"queue_{stamp}.json")
    shutil.copy2(LABELS, BACKUP / f"labels_{stamp}.json")

    review_items = []
    for d in details:
        iid = d["id"]
        item = next(x for x in new_items if x["id"] == iid)
        if iid in labels:
            del labels[iid]
        entry = dict(item)
        entry["priority"] = "review_draft"
        entry["current_label"] = d["pred"]
        entry["suggested_label"] = d["gt"]
        entry["reason"] = (
            f"模型={d['pred']}({d['conf']:.3f}) → 草案={d['gt']}"
            + ("" if d["ok"] else "｜已自动修正")
        )
        entry["notes"] = entry["reason"]
        review_items.append(entry)

    review_items.sort(
        key=lambda x: (
            0 if x["current_label"] != x["suggested_label"] else 1,
            0 if x["suggested_label"] in ("result", "replay", "buy", "non_game") else 1,
            x["video_id"],
            x["timestamp_sec"],
        )
    )
    rest = [
        x
        for x in queue
        if not (
            x["rel_path"].startswith("pov_dianjing/")
            or x["rel_path"].startswith("pov_hanghang/")
        )
    ]
    QUEUE.write_text(json.dumps(review_items + rest, ensure_ascii=False, indent=2), encoding="utf-8")
    LABELS.write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")
    mismatch = sum(1 for x in review_items if x["current_label"] != x["suggested_label"])
    print(f"queued_for_review={len(review_items)} mismatches_first={mismatch}")
    print("Refresh http://127.0.0.1:8765/ → 只看未标")


if __name__ == "__main__":
    main()
