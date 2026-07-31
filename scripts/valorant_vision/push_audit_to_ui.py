#!/usr/bin/env python3
"""Push dataset audit suspects to the front of the label UI queue."""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

ANN = Path.home() / "LSC" / "datasets" / "valorant_phase" / "annotate"
QUEUE = ANN / "queue.json"
LABELS = ANN / "labels.json"
AUDIT = ANN / "audit_report.json"
SUSPECTS = ANN / "audit_suspects.json"
BACKUP = ANN / "backups"


def main() -> None:
    queue = json.loads(QUEUE.read_text(encoding="utf-8"))
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    report = json.loads(AUDIT.read_text(encoding="utf-8"))
    auto = json.loads(SUSPECTS.read_text(encoding="utf-8")) if SUSPECTS.exists() else []

    by_rel = {q["rel_path"].replace("\\", "/"): q for q in queue}

    # Build ordered review list: must fixes → uncertain → other auto suspects
    ordered: list[dict] = []
    seen: set[str] = set()

    def push(rel: str, *, priority: str, current: str | None, suggested: str | None, reason: str) -> None:
        rel = rel.replace("\\", "/")
        q = by_rel.get(rel)
        if q is None or q["id"] in seen:
            return
        seen.add(q["id"])
        entry = dict(q)
        entry["label"] = None
        entry["priority"] = priority
        entry["current_label"] = current or labels.get(q["id"], {}).get("label")
        entry["suggested_label"] = suggested
        entry["reason"] = reason
        bits = []
        if entry["current_label"]:
            bits.append(f"原标:{entry['current_label']}")
        if suggested:
            bits.append(f"建议:{suggested}")
        bits.append(reason)
        entry["notes"] = " | ".join(bits)
        ordered.append(entry)

    for item in report["visually_confirmed_fixes"]:
        push(
            item["rel_path"],
            priority="must",
            current=item["current_label"],
            suggested=item["correct_label"],
            reason=item["reason"],
        )
    for item in report.get("uncertain", []):
        push(
            item["rel_path"],
            priority="uncertain",
            current=item["current_label"],
            suggested="combat",
            reason=item["note"],
        )
    for item in report.get("visually_confirmed_ok", []):
        push(
            item["rel_path"],
            priority="confirm_ok",
            current=item["label"],
            suggested=item["label"],
            reason=f"审查建议保持 | {item.get('note', '')}",
        )
    for item in auto:
        push(
            item["rel_path"],
            priority=item.get("priority", "review"),
            current=item.get("current_label"),
            suggested=item.get("suggested_label"),
            reason=item.get("reason", ""),
        )

    BACKUP.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(QUEUE, BACKUP / f"queue_{stamp}.json")
    shutil.copy2(LABELS, BACKUP / f"labels_{stamp}.json")

    cleared = 0
    for entry in ordered:
        if entry["id"] in labels:
            del labels[entry["id"]]
            cleared += 1

    rest = [q for q in queue if q["id"] not in seen]
    new_queue = ordered + rest
    QUEUE.write_text(json.dumps(new_queue, ensure_ascii=False, indent=2), encoding="utf-8")
    LABELS.write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"review_head={len(ordered)} cleared_labels={cleared}")
    print(f"by_priority: must={sum(1 for e in ordered if e['priority']=='must')} "
          f"uncertain={sum(1 for e in ordered if e['priority']=='uncertain')} "
          f"confirm_ok={sum(1 for e in ordered if e['priority']=='confirm_ok')} "
          f"other={sum(1 for e in ordered if e['priority'] not in ('must','uncertain','confirm_ok'))}")
    print("Refresh http://127.0.0.1:8765/ → 只看未标")
    for e in ordered[:12]:
        print(f"  [{e['priority']}] {e['rel_path']}: {e.get('current_label')} -> {e.get('suggested_label')}")


if __name__ == "__main__":
    main()
