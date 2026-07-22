#!/usr/bin/env python3
"""Scan labeled frames for suspicious labels and rebuild a re-label queue.

Usage:
  python scripts/valorant_vision/scan_relabel_queue.py
  # then refresh http://127.0.0.1:8765/ and click「只看未标」
"""
from __future__ import annotations

import json
import shutil
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path.home() / "LSC" / "datasets" / "valorant_phase" / "annotate"
QUEUE = ROOT / "queue.json"
LABELS = ROOT / "labels.json"
REVIEW = ROOT / "review_needed.json"
BACKUP_DIR = ROOT / "backups"

# Keep re-label set manageable: must + review islands, plus a capped boundary sample.
MAX_BOUNDARY = 40
MAX_BOUNDARY_PER_VIDEO = 12

KNOWN_MUST = {
    "pov_ling_20260720_134749_000983": ("combat", "爆能器已部署；前后 combat"),
    "pov_beilie_20260721_141303_000060": ("combat", "无胜负大字，回合末交战"),
    "pov_beilie_20260721_141303_000090": ("combat", "死亡镜头计时器仍在跑"),
    "pov_beilie_20260721_141303_000172": ("buy", "buy 段中的 Tab 孤岛"),
    "broadcast_yuezi_20260720_202557_000227": ("buy", "经济总览板非 REPLAY"),
    "pov_tangqihua_20260721_141301_000025": ("combat", "Tab+战斗记录无结算大字"),
}

PRIO_RANK = {"must": 0, "review": 1, "boundary": 2}


def add(suspects: list[dict], prio: str, q: dict, reason: str, *, suggested=None, current=None) -> None:
    suspects.append(
        {
            "priority": prio,
            "id": q["id"],
            "rel_path": q["rel_path"],
            "abs_path": q.get("abs_path"),
            "video_id": q["video_id"],
            "video_path": q.get("video_path"),
            "timestamp_sec": q["timestamp_sec"],
            "source_type": q.get("source_type"),
            "session_id": q.get("session_id"),
            "split": q.get("split"),
            "label": None,
            "notes": "",
            "current_label": current,
            "suggested_label": suggested,
            "reason": reason,
        }
    )


def dedupe(suspects: list[dict]) -> list[dict]:
    best: dict[str, dict] = {}
    for s in suspects:
        old = best.get(s["id"])
        if old is None or PRIO_RANK[s["priority"]] < PRIO_RANK[old["priority"]]:
            best[s["id"]] = s
            continue
        if PRIO_RANK[s["priority"]] == PRIO_RANK[old["priority"]]:
            if s["reason"] not in old["reason"]:
                old["reason"] = f"{old['reason']}；{s['reason']}"
            if s.get("suggested_label") and not old.get("suggested_label"):
                old["suggested_label"] = s["suggested_label"]
    out = list(best.values())
    out.sort(key=lambda x: (PRIO_RANK[x["priority"]], x["video_id"], x["timestamp_sec"]))
    return out


def scan(queue: list[dict], labels: dict) -> list[dict]:
    by_vid: dict[str, list] = defaultdict(list)
    for i, q in enumerate(queue):
        lab = labels.get(q["id"], {}).get("label")
        by_vid[q["video_id"]].append((i, q, lab))

    suspects: list[dict] = []

    for items in by_vid.values():
        items = sorted(items, key=lambda x: x[1]["timestamp_sec"])
        n = len(items)
        for k in range(n):
            _, q, lab = items[k]
            if not lab:
                continue
            prev_lab = items[k - 1][2] if k > 0 else None
            next_lab = items[k + 1][2] if k + 1 < n else None
            next2 = items[k + 2][2] if k + 2 < n else None

            # Single-frame island
            if prev_lab and next_lab and prev_lab == next_lab and lab != prev_lab:
                add(
                    suspects,
                    "must",
                    q,
                    f"单帧孤岛：前后均为 {prev_lab}，本帧 {lab}",
                    suggested=prev_lab,
                    current=lab,
                )

            # Double-frame island (flag both via separate iterations)
            if (
                prev_lab
                and next2
                and prev_lab == next2
                and lab == next_lab
                and lab != prev_lab
            ):
                add(
                    suspects,
                    "review",
                    q,
                    f"双帧孤岛候选：两侧为 {prev_lab}，本段 {lab}",
                    suggested=prev_lab,
                    current=lab,
                )

            if prev_lab == "buy" and lab == "result" and next_lab in ("buy", "non_game"):
                add(
                    suspects,
                    "review",
                    q,
                    "买枪直接跳结算且无交战过渡",
                    suggested="combat",
                    current=lab,
                )

            if lab == "replay" and prev_lab == "combat" and next_lab == "combat":
                add(
                    suspects,
                    "must",
                    q,
                    "交战夹心的 replay，多为死亡镜头误标",
                    suggested="combat",
                    current=lab,
                )

            if lab == "result" and prev_lab == "combat" and next_lab == "combat":
                add(
                    suspects,
                    "must",
                    q,
                    "交战夹心的 result，多为死亡/Tab 误标",
                    suggested="combat",
                    current=lab,
                )

            if lab == "buy" and prev_lab == "combat" and next_lab == "combat":
                add(
                    suspects,
                    "must",
                    q,
                    "交战夹心的 buy，多为误开商店或 Tab",
                    suggested="combat",
                    current=lab,
                )

            if lab == "non_game" and prev_lab in ("buy", "combat", "result") and next_lab in (
                "buy",
                "combat",
                "result",
            ):
                add(
                    suspects,
                    "review",
                    q,
                    f"局内夹心 non_game（前后 {prev_lab}/{next_lab}）",
                    suggested=prev_lab,
                    current=lab,
                )

            if prev_lab == "buy" and lab == "result" and next_lab == "combat":
                add(
                    suspects,
                    "review",
                    q,
                    "买枪→结算→交战，顺序可疑",
                    suggested="combat",
                    current=lab,
                )

            # Singleton rare labels: easy to mis-click, worth a second look
            if lab in ("result", "replay") and prev_lab != lab and next_lab != lab:
                add(
                    suspects,
                    "review",
                    q,
                    f"单帧 {lab}（前后为 {prev_lab}/{next_lab}），请确认是否有胜负大字/REPLAY",
                    current=lab,
                )

    # Known prior audit
    by_id = {q["id"]: q for q in queue}
    for kid, (sug, reason) in KNOWN_MUST.items():
        q = by_id.get(kid)
        if q:
            cur = labels.get(kid, {}).get("label")
            add(suspects, "must", q, reason, suggested=sug, current=cur)

    # Boundary frames: only result/replay edges, balanced across videos
    boundary_by_vid: dict[str, list[dict]] = defaultdict(list)
    for vid, items in by_vid.items():
        items = sorted(items, key=lambda x: x[1]["timestamp_sec"])
        for k in range(1, len(items)):
            _, q0, l0 = items[k - 1]
            _, q1, l1 = items[k]
            if not l0 or not l1 or l0 == l1:
                continue
            if not ({"result", "replay"} & {l0, l1}):
                continue
            tmp: list[dict] = []
            add(tmp, "boundary", q0, f"边界前帧 {l0}→{l1}", current=l0)
            add(tmp, "boundary", q1, f"边界后帧 {l0}→{l1}", current=l1)
            boundary_by_vid[vid].extend(tmp)

    selected_boundary: list[dict] = []
    # Round-robin / per-video cap so one long POV doesn't dominate
    vids = sorted(boundary_by_vid.keys())
    per_vid_idx = {v: 0 for v in vids}
    while len(selected_boundary) < MAX_BOUNDARY:
        progressed = False
        for v in vids:
            bucket = boundary_by_vid[v]
            i = per_vid_idx[v]
            taken_for_v = sum(1 for b in selected_boundary if b["video_id"] == v)
            if i >= len(bucket) or taken_for_v >= MAX_BOUNDARY_PER_VIDEO:
                continue
            selected_boundary.append(bucket[i])
            per_vid_idx[v] = i + 1
            progressed = True
            if len(selected_boundary) >= MAX_BOUNDARY:
                break
        if not progressed:
            break

    suspects.extend(selected_boundary)
    return dedupe(suspects)


def main() -> None:
    queue = json.loads(QUEUE.read_text(encoding="utf-8"))
    labels = json.loads(LABELS.read_text(encoding="utf-8"))

    final = scan(queue, labels)
    counts = Counter(x["priority"] for x in final)

    # Backup before mutating
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(QUEUE, BACKUP_DIR / f"queue_{stamp}.json")
    shutil.copy2(LABELS, BACKUP_DIR / f"labels_{stamp}.json")

    # Clear labels for review items so UI shows them as unlabeled
    cleared = 0
    for item in final:
        if item["id"] in labels:
            del labels[item["id"]]
            cleared += 1

    # Rebuild queue: review items first (must → review → boundary), then the rest
    review_ids = {x["id"] for x in final}
    review_queue = []
    for item in final:
        # restore full queue schema fields from original queue entry
        base = next(q for q in queue if q["id"] == item["id"])
        entry = dict(base)
        entry["label"] = None
        notes_bits = []
        if item.get("current_label"):
            notes_bits.append(f"原标:{item['current_label']}")
        if item.get("suggested_label"):
            notes_bits.append(f"建议:{item['suggested_label']}")
        if item.get("reason"):
            notes_bits.append(item["reason"])
        entry["notes"] = " | ".join(notes_bits)
        entry["priority"] = item["priority"]
        entry["current_label"] = item.get("current_label")
        entry["suggested_label"] = item.get("suggested_label")
        entry["reason"] = item.get("reason")
        review_queue.append(entry)

    rest = [q for q in queue if q["id"] not in review_ids]
    new_queue = review_queue + rest

    QUEUE.write_text(json.dumps(new_queue, ensure_ascii=False, indent=2), encoding="utf-8")
    LABELS.write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")
    REVIEW.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"suspects={len(final)} cleared_labels={cleared}")
    print(f"by_priority={dict(counts)}")
    print(f"by_video={dict(Counter(x['video_id'] for x in final))}")
    print(f"queue_head={len(review_queue)} review items first")
    print(f"wrote {REVIEW}")
    print(f"backup -> {BACKUP_DIR} ({stamp})")
    print("Refresh http://127.0.0.1:8765/ then click「只看未标」")


if __name__ == "__main__":
    main()
