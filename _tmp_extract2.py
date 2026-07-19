import json, os, re
from pathlib import Path
from collections import defaultdict

log = Path(os.environ["APPDATA"]) / "lsc-electron" / "logs" / "backend-stdout.log"
out = Path(r"D:/Project/直播切片多人/_tmp_lsc_ctx_utf8.txt")

# markers timeline
marker_re = re.compile(
    r"(2026-07-15 \d{2}:\d{2}:\d{2}).*round_detector:.*(duration=(\d+)s, markers=(\d+))"
)
ocr_info_re = re.compile(
    r"(2026-07-15 \d{2}:\d{2}:\d{2}).*round_detector: OCR.*: (\d+) .*\(duration=(\d+)s, markers=(\d+)\)"
)
kick_re = re.compile(
    r"(2026-07-15 \d{2}:\d{2}:\d{2}).*kick worker: room_id=([0-9a-f]+), dur=(\d+)s, range=([\d.]+)-([\d.]+), OCR=(\w+), full=(\w+), finalize=(\w+)"
)
threading_err = []
markers_warn = []
ocr_promote = []
kicks = []

with open(log, "rb") as f:
    for raw in f:
        line = raw.decode("utf-8", errors="replace")
        if "2026-07-15" not in line:
            continue
        m = re.search(r"2026-07-15 (\d{2}):", line)
        if not m or int(m.group(1)) < 21:
            continue
        if "threading" in line and "not defined" in line:
            threading_err.append(line.strip()[:300])
        km = kick_re.search(line)
        if km:
            kicks.append(km.groups())
        if "markers=" in line and "round_detector" in line:
            if "WARNING" in line:
                mm = marker_re.search(line)
                if mm:
                    markers_warn.append((mm.group(1), int(mm.group(3)), int(mm.group(4)), line.strip()[:200]))
            if "INFO" in line and "OCR" in line:
                im = ocr_info_re.search(line)
                if im:
                    ocr_promote.append((im.group(1), int(im.group(2)), int(im.group(3)), int(im.group(4))))

# confirms
confirms = []
with open(log, "rb") as f:
    for raw in f:
        line = raw.decode("utf-8", errors="replace")
        if "Received WS message: type=confirm_highlight_clip" not in line:
            continue
        if "2026-07-15" not in line:
            continue
        m = re.search(r"2026-07-15 (\d{2}:\d{2}:\d{2}).*data=(\{.*\})", line)
        if not m:
            continue
        hh = int(m.group(1)[:2])
        if hh < 20:
            continue
        try:
            data = eval(m.group(2))
        except Exception:
            continue
        confirms.append((m.group(1), data))

base = Path(r"D:/desktop")
analyses = {}
for fp in base.rglob("*.analysis.json"):
    if "20260715" not in fp.name:
        continue
    data = json.loads(fp.read_text(encoding="utf-8"))
    rid = data.get("room_id")
    analyses[rid] = {
        "file": str(fp.resolve()),
        "name": fp.name,
        "analyzed_at": data.get("analyzed_at"),
        "mode": data.get("mode"),
        "highlights": data.get("highlights") or [],
        "parent": fp.parent.name,
    }

# dedupe analyses by resolve
# already unique by room mostly

with open(out, "w", encoding="utf-8") as w:
    w.write("THREADING_ERR count=%d\n" % len(threading_err))
    for t in threading_err[:10]:
        w.write(t + "\n")
    w.write("\nMARKERS_WARN count=%d (unique ts+dur+markers)\n" % len(markers_warn))
    seen = set()
    for ts, dur, mk, rawl in markers_warn:
        key = (ts, dur, mk)
        if key in seen:
            continue
        seen.add(key)
        w.write("%s duration=%ss markers=%s\n" % (ts, dur, mk))
    w.write("\nOCR_PROMOTE_INFO count=%d\n" % len(ocr_promote))
    seen = set()
    for ts, n, dur, mk in ocr_promote:
        key = (ts, n, dur, mk)
        if key in seen:
            continue
        seen.add(key)
        w.write("%s rounds=%s duration=%ss markers=%s\n" % (ts, n, dur, mk))
    w.write("\nKICKS unique rooms/ranges sample\n")
    by_room = defaultdict(list)
    for ts, rid, dur, a, b, ocr, full, fin in kicks:
        by_room[rid].append((ts, dur, a, b, ocr, full, fin))
    for rid, items in by_room.items():
        w.write("room=%s kicks=%d first=%s last=%s\n" % (rid, len(items), items[0], items[-1]))
        # finalize true
        fins = [x for x in items if x[-1] == "True"]
        w.write("  finalize_true=%d\n" % len(fins))
        for x in fins[:5]:
            w.write("  FIN %s\n" % (x,))

    w.write("\n=== CONFIRMS vs ANALYSIS ===\n")
    for ts, d in confirms:
        rid = d.get("room_id")
        rk = d.get("round_key")
        a = analyses.get(rid)
        if not a:
            w.write("%s %s NO_ANALYSIS room=%s start=%s end=%s\n" % (ts, rk, rid, d.get("start"), d.get("end")))
            continue
        hit = next((h for h in a["highlights"] if h.get("round_key") == rk), None)
        if not hit:
            w.write("%s CONFIRM %s %s-%s MISSING in %s (hl=%d)\n" % (
                ts, rk, d.get("start"), d.get("end"), a["name"], len(a["highlights"])))
            continue
        cs, ce = float(d["start"]), float(d["end"])
        hs, he = float(hit["start"]), float(hit["end"])
        w.write("%s %s %s confirm=%.1f-%.1f analysis=%.1f-%.1f dS=%.1f dE=%.1f by=%s/%s\n" % (
            ts, a["name"], rk, cs, ce, hs, he, cs - hs, ce - he, hit.get("start_by"), hit.get("end_by")))

    w.write("\n=== HIGHLIGHTS ===\n")
    for rid, a in sorted(analyses.items(), key=lambda x: x[1]["name"]):
        w.write("\nFILE %s\nROOM %s\nPARENT %s\nAT %s\n" % (a["name"], rid, a["parent"], a.get("analyzed_at")))
        ocr_starts = sum(1 for h in a["highlights"] if str(h.get("start_by", "")).startswith("ocr"))
        ocr_ends = sum(1 for h in a["highlights"] if str(h.get("end_by", "")) in ("next_buy", "ocr_result", "ocr_prev_end") or str(h.get("end_by", "")).startswith("ocr"))
        next_buy = sum(1 for h in a["highlights"] if h.get("end_by") == "next_buy")
        w.write("count=%d ocr_start=%d ocrish_end=%d next_buy_end=%d\n" % (
            len(a["highlights"]), ocr_starts, ocr_ends, next_buy))
        for h in a["highlights"]:
            w.write("  %s: %.3f-%.3f by=%s/%s score=%s\n" % (
                h.get("round_key"), float(h.get("start")), float(h.get("end")),
                h.get("start_by"), h.get("end_by"), h.get("score")))

print("wrote", out)
print("threading", len(threading_err), "markers_warn", len(markers_warn), "promote", len(ocr_promote), "kicks", len(kicks), "confirms", len(confirms))
