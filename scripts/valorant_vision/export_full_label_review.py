#!/usr/bin/env python3
"""Export all rare-class frames + top disagree for visual mislabel audit."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

CODEX = Path(
    r"D:\Project\直播切片多人\.worktrees\valorant-frame-labeling-pilot"
    r"\datasets\valorant_phase\annotations\new_broadcast_20260721"
)
ANN = Path.home() / "LSC" / "datasets" / "valorant_phase" / "annotate"
OUT = ANN / "label_audit_full_review"
THUMB = OUT / "thumbs"


def imread(p: Path):
    return cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_COLOR)


def save(img, path: Path) -> None:
    h, w = img.shape[:2]
    sc = 640 / max(h, w)
    small = cv2.resize(img, (int(w * sc), int(h * sc)))
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".jpg", small)[1].tofile(str(path))


def main() -> None:
    labels = json.loads((CODEX / "labels.json").read_text(encoding="utf-8"))
    queue = json.loads((CODEX / "queue.json").read_text(encoding="utf-8"))
    details = {
        d["id"]: d
        for d in json.loads(
            (ANN / "blind_codex_human_gt_details.json").read_text(encoding="utf-8")
        )["details"]
    }
    qmap = {x["id"]: x for x in queue}

    # all result GT
    result_ids = [iid for iid, lab in labels.items() if lab["label"] == "result"]
    # buy where model says combat high conf
    buy_combat = [
        d
        for d in details.values()
        if d["gt"] == "buy" and d["pred"] == "combat" and d["conf"] >= 0.9
    ]
    # replay where model says non_game/result high conf (likely stage mis-tagged as replay)
    replay_stagey = [
        d
        for d in details.values()
        if d["gt"] == "replay"
        and d["pred"] in ("non_game", "result")
        and d["conf"] >= 0.9
    ]
    # combat where model says non_game/result high conf (likely playercam mis-tagged combat)
    combat_cam = [
        d
        for d in details.values()
        if d["gt"] == "combat"
        and d["pred"] in ("non_game", "result")
        and d["conf"] >= 0.85
    ]

    catalog = []

    def export_group(name: str, ids_or_details, from_detail: bool = False) -> None:
        rows = ids_or_details
        for row in rows:
            if from_detail:
                d = row
                iid = d["id"]
            else:
                iid = row
                d = details.get(iid, {})
            item = qmap[iid]
            img = imread(CODEX / item["rel_path"])
            if img is None:
                continue
            gt = labels[iid]["label"]
            pred = d.get("pred", "?")
            conf = d.get("conf", 0)
            fname = (
                f"{name}__{item['video_id'].split('_')[1]}__"
                f"t{int(item['timestamp_sec']):04d}__gt-{gt}__pred-{pred}__c{conf:.2f}.jpg"
            )
            save(img, THUMB / fname)
            catalog.append(
                {
                    "group": name,
                    "id": iid,
                    "video_id": item["video_id"],
                    "timestamp_sec": item["timestamp_sec"],
                    "rel_path": item["rel_path"],
                    "gt": gt,
                    "pred": pred,
                    "conf": conf,
                    "thumb": fname,
                }
            )

    export_group("all_result", result_ids, False)
    export_group("buy_as_combat_hc", buy_combat, True)
    export_group("replay_as_stage_hc", replay_stagey, True)
    export_group("combat_as_cam_hc", combat_cam, True)

    (OUT / "catalog.json").write_text(
        json.dumps(
            {
                "n": len(catalog),
                "counts": dict(Counter(c["group"] for c in catalog)),
                "items": catalog,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps(dict(Counter(c["group"] for c in catalog)), ensure_ascii=False, indent=2))
    print(f"thumbs={len(list(THUMB.glob('*.jpg')))} -> {THUMB}")
    print("ALL RESULT:")
    for c in catalog:
        if c["group"] == "all_result":
            print(
                f"  {c['video_id']} t={c['timestamp_sec']:.0f} pred={c['pred']}@{c['conf']:.2f}"
            )


if __name__ == "__main__":
    main()
