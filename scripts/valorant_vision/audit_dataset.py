#!/usr/bin/env python3
"""Audit annotate labels for islands / suspicious transitions."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

ANN = Path.home() / "LSC" / "datasets" / "valorant_phase" / "annotate"
OUT = ANN / "audit_suspects.json"


def main() -> None:
    queue = json.loads((ANN / "queue.json").read_text(encoding="utf-8"))
    labels = json.loads((ANN / "labels.json").read_text(encoding="utf-8"))

    by_vid: dict[str, list] = defaultdict(list)
    for q in queue:
        lab = labels.get(q["id"], {}).get("label")
        by_vid[q["video_id"]].append((q, lab))

    suspects: list[dict] = []

    def add(prio: str, q: dict, cur: str | None, reason: str, suggested: str | None = None) -> None:
        suspects.append(
            {
                "priority": prio,
                "id": q["id"],
                "rel_path": q["rel_path"],
                "abs_path": q.get("abs_path"),
                "video_id": q["video_id"],
                "timestamp_sec": q["timestamp_sec"],
                "current_label": cur,
                "suggested_label": suggested,
                "reason": reason,
            }
        )

    for items in by_vid.values():
        items = sorted(items, key=lambda x: x[0]["timestamp_sec"])
        n = len(items)
        for k in range(n):
            q, lab = items[k]
            if not lab:
                add("must", q, None, "未标注")
                continue
            prev = items[k - 1][1] if k else None
            nxt = items[k + 1][1] if k + 1 < n else None
            nxt2 = items[k + 2][1] if k + 2 < n else None

            if prev and nxt and prev == nxt and lab != prev:
                add("must", q, lab, f"单帧孤岛：前后均为 {prev}", suggested=prev)

            if prev and nxt2 and prev == nxt2 and lab == nxt and lab != prev:
                add("review", q, lab, f"双帧孤岛候选：两侧 {prev}，本段 {lab}", suggested=prev)

            if lab == "result" and prev == "combat" and nxt == "combat":
                add("must", q, lab, "交战夹心 result（多为死亡/Tab）", suggested="combat")

            if lab == "buy" and prev == "combat" and nxt == "combat":
                add("must", q, lab, "交战夹心 buy（多为 Tab/商店误开）", suggested="combat")

            if lab == "replay" and prev == "combat" and nxt == "combat":
                add("must", q, lab, "交战夹心 replay", suggested="combat")

            if lab == "non_game" and prev in ("buy", "combat", "result") and nxt in (
                "buy",
                "combat",
                "result",
            ):
                add("review", q, lab, f"局内夹心 non_game（{prev}/{nxt}）", suggested=prev)

            if lab in ("result", "replay") and prev != lab and nxt != lab:
                add(
                    "review",
                    q,
                    lab,
                    f"单帧 {lab}（前后 {prev}/{nxt}），确认是否有结算大字/REPLAY",
                )

            if prev == "buy" and lab == "result" and nxt in ("buy", "non_game", "combat"):
                add("review", q, lab, "买枪→结算 跳变可疑", suggested="combat")

    # dedupe keep highest priority
    rank = {"must": 0, "review": 1}
    best: dict[str, dict] = {}
    for s in suspects:
        old = best.get(s["id"])
        if old is None or rank[s["priority"]] < rank[old["priority"]]:
            best[s["id"]] = s
        elif old and rank[s["priority"]] == rank[old["priority"]]:
            if s["reason"] not in old["reason"]:
                old["reason"] = f"{old['reason']}；{s['reason']}"

    final = sorted(
        best.values(),
        key=lambda x: (rank[x["priority"]], x["video_id"], x["timestamp_sec"]),
    )
    OUT.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"total_labels={len(labels)} unlabeled={sum(1 for q in queue if q['id'] not in labels)}")
    print(f"dist={dict(Counter(v['label'] for v in labels.values()))}")
    print(f"suspects={len(final)} by={dict(Counter(x['priority'] for x in final))}")
    print(f"by_video={dict(Counter(x['video_id'] for x in final))}")
    print("--- MUST ---")
    for x in final:
        if x["priority"] == "must":
            print(
                f"{x['rel_path']}: {x['current_label']} -> {x.get('suggested_label')} | {x['reason']}"
            )
    print("--- REVIEW (first 40) ---")
    shown = 0
    for x in final:
        if x["priority"] != "review":
            continue
        print(
            f"{x['rel_path']}: {x['current_label']} -> {x.get('suggested_label')} | {x['reason']}"
        )
        shown += 1
        if shown >= 40:
            print(f"... +{sum(1 for y in final if y['priority']=='review') - 40} more review")
            break
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
