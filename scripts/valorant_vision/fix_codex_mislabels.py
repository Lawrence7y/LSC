#!/usr/bin/env python3
"""Apply confirmed mislabel fixes to Codex broadcast labels and recompute accuracy."""
from __future__ import annotations

import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path

CODEX = Path(
    r"D:\Project\直播切片多人\.worktrees\valorant-frame-labeling-pilot"
    r"\datasets\valorant_phase\annotations\new_broadcast_20260721"
)
ANN = Path.home() / "LSC" / "datasets" / "valorant_phase" / "annotate"
BACKUP = CODEX / "backups"


def main() -> None:
    labels_path = CODEX / "labels.json"
    queue = json.loads((CODEX / "queue.json").read_text(encoding="utf-8"))
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    details = {
        d["id"]: d
        for d in json.loads(
            (ANN / "blind_codex_human_gt_details.json").read_text(encoding="utf-8")
        )["details"]
    }

    by_key = {(x["video_id"], float(x["timestamp_sec"])): x for x in queue}

    def iid(vid: str, t: float) -> str:
        return by_key[(vid, t)]["id"]

    HH = "broadcast_hanghang_20260721"
    ES = "broadcast_valorant_esports_20260721"

    # Explicit one-off fixes from visual audit
    explicit: dict[str, str] = {
        iid(HH, 798.0): "combat",  # was result, mid-round
        iid(ES, 1106.0): "combat",  # was result, ultimate active
        iid(ES, 1926.0): "combat",  # was result, nearsight combat
        iid(ES, 1274.0): "non_game",  # was combat, player desk cam
        iid(HH, 342.0): "non_game",  # was replay, stage/player cam
        iid(HH, 346.0): "non_game",
        iid(HH, 350.0): "non_game",
        iid(ES, 866.0): "non_game",  # was replay, player cam
        iid(HH, 1558.0): "non_game",  # HIGHLIGHTS celebration cam, no gameplay
        # buy→combat visual confirms outside hanghang main block
        iid(ES, 454.0): "combat",
        iid(ES, 554.0): "combat",
        iid(ES, 1538.0): "combat",
        iid(ES, 1670.0): "combat",
    }

    changes: list[dict] = []

    def set_label(frame_id: str, new_lab: str, reason: str) -> None:
        old = labels[frame_id]["label"]
        if old == new_lab:
            return
        labels[frame_id] = dict(labels[frame_id])
        labels[frame_id]["label"] = new_lab
        labels[frame_id]["notes"] = (
            (labels[frame_id].get("notes") or "")
            + f" | audit_fix:{old}->{new_lab} ({reason})"
        ).strip(" |")
        labels[frame_id]["annotator"] = "human+audit"
        item = next(x for x in queue if x["id"] == frame_id)
        changes.append(
            {
                "id": frame_id,
                "video_id": item["video_id"],
                "timestamp_sec": item["timestamp_sec"],
                "old": old,
                "new": new_lab,
                "reason": reason,
            }
        )

    for frame_id, new_lab in explicit.items():
        set_label(frame_id, new_lab, "visual_confirm")

    # HangHang buy block: t=706..794 inclusive every 4s → combat
    # (690-702 may still be buy; keep unless model+conf strongly combat)
    for t in range(706, 795, 4):
        key = (HH, float(t))
        if key not in by_key:
            continue
        frame_id = by_key[key]["id"]
        if labels[frame_id]["label"] == "buy":
            set_label(frame_id, "combat", "hanghang_buy_block_706_794")

    # Remaining high-conf buy→combat in hanghang 690-794 (catch 750 etc already covered)
    for item in queue:
        if item["video_id"] != HH:
            continue
        t = item["timestamp_sec"]
        if not (690 <= t <= 794):
            continue
        if labels[item["id"]]["label"] != "buy":
            continue
        d = details[item["id"]]
        if d["pred"] == "combat" and d["conf"] >= 0.9:
            set_label(item["id"], "combat", "hanghang_buy_block_hc_combat")

    # Other replay→stage: hanghang 674 if pure cam (check via pred)
    for t in (674.0,):
        key = (HH, t)
        if key in by_key:
            frame_id = by_key[key]["id"]
            d = details[frame_id]
            if labels[frame_id]["label"] == "replay" and d["pred"] == "non_game" and d["conf"] >= 0.9:
                set_label(frame_id, "non_game", "replay_stage_hc")

    # valorant t=222 replay→result high conf — likely stage; fix to non_game
    key = (ES, 222.0)
    if key in by_key:
        frame_id = by_key[key]["id"]
        if labels[frame_id]["label"] == "replay":
            set_label(frame_id, "non_game", "replay_stage_hc")

    BACKUP.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(labels_path, BACKUP / f"labels_before_audit_{stamp}.json")
    labels_path.write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")

    # rewrite manifest_labeled.jsonl
    man_lines = []
    for item in queue:
        lab = labels[item["id"]]
        man_lines.append(
            json.dumps(
                {
                    "video_id": item["video_id"],
                    "video_path": item["video_path"],
                    "timestamp_sec": item["timestamp_sec"],
                    "label": lab["label"],
                    "split": item.get("split", "train"),
                    "source_type": item.get("source_type", "broadcast"),
                    "session_id": item.get("session_id", item["video_id"]),
                    "frame_path": str(CODEX / item["rel_path"]),
                    "rel_path": item["rel_path"],
                    "notes": lab.get("notes") or "",
                },
                ensure_ascii=False,
            )
        )
    (CODEX / "manifest_labeled.jsonl").write_text(
        "\n".join(man_lines) + "\n", encoding="utf-8"
    )

    # recompute accuracy vs model preds already stored
    rows = []
    for item in queue:
        d = details[item["id"]]
        gt = labels[item["id"]]["label"]
        rows.append(
            {
                "id": item["id"],
                "video_id": item["video_id"],
                "timestamp_sec": item["timestamp_sec"],
                "split": item.get("split"),
                "gt": gt,
                "pred": d["pred"],
                "conf": d["conf"],
                "ok": d["pred"] == gt,
            }
        )

    n = len(rows)
    correct = sum(r["ok"] for r in rows)
    metrics = {
        "n": n,
        "accuracy": round(correct / n, 4),
        "correct": correct,
        "errors": n - correct,
        "n_label_fixes": len(changes),
        "fix_types": dict(Counter(f"{c['old']}->{c['new']}" for c in changes)),
        "gt_dist": dict(Counter(r["gt"] for r in rows)),
        "pred_dist": dict(Counter(r["pred"] for r in rows)),
        "error_types": {
            f"{a}->{b}": c
            for (a, b), c in Counter(
                (r["gt"], r["pred"]) for r in rows if not r["ok"]
            ).most_common()
        },
        "by_video": {},
        "by_split": {},
        "prev_accuracy": 0.7692,
    }
    for vid in sorted({r["video_id"] for r in rows}):
        sub = [r for r in rows if r["video_id"] == vid]
        metrics["by_video"][vid] = {
            "n": len(sub),
            "acc": round(sum(r["ok"] for r in sub) / len(sub), 4),
            "errors": sum(not r["ok"] for r in sub),
            "gt_dist": dict(Counter(r["gt"] for r in sub)),
        }
    for sp in sorted({r.get("split") for r in rows}):
        sub = [r for r in rows if r.get("split") == sp]
        metrics["by_split"][sp] = {
            "n": len(sub),
            "acc": round(sum(r["ok"] for r in sub) / len(sub), 4),
        }

    (ANN / "blind_codex_human_gt_metrics_fixed.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (ANN / "label_audit_fixes.json").write_text(
        json.dumps({"n": len(changes), "changes": changes}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # refresh details gt
    (ANN / "blind_codex_human_gt_details.json").write_text(
        json.dumps({"details": rows}, ensure_ascii=False), encoding="utf-8"
    )

    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    print(f"\nfixed={len(changes)}")
    for c in changes[:40]:
        print(
            f"  {c['video_id']} t={c['timestamp_sec']:.0f} {c['old']}->{c['new']} ({c['reason']})"
        )
    if len(changes) > 40:
        print(f"  ... +{len(changes) - 40} more")


if __name__ == "__main__":
    main()
