from pathlib import Path
import re

log = Path(r"C:\Users\Administrator\AppData\Roaming\lsc-electron\logs\backend-stdout.log")
out = Path(r"D:\Project\直播切片多人\_tmp_lsc_curated_report.txt")
ROOM = "144e9b6f"

in_window_keys = re.compile(
    r"144e9b6f|round_detector|kick worker|confirm_highlight|export_clip|"
    r"markers=|refine_with_ocr|Onset|ocr_refine|"
    r"round_phase|pending_rounds|confirmed_rounds|scan_reason|"
    r"continuous_highlights|clip_queued|valorant_round|仅入列|ocr升格|回合分割",
    re.I,
)

def reconstruct(line: str) -> str:
    m = re.search(r"OCR .*?\(duration=(\d+)s, markers=(\d+)\)", line)
    if m and "round_detector" in line and "WARNING" in line:
        prefix = line[: line.index("OCR")]
        return (
            f"{prefix}OCR 回合状态分割无有效回合 "
            f"(duration={m.group(1)}s, markers={m.group(2)})，回退到纯音频检测"
        )
    m2 = re.search(r"OCR .*?: (\d+) .*?\(duration=([\d.]+)s, markers=(\d+)\)", line)
    if m2 and "round_detector" in line and "INFO" in line:
        prefix = line[: line.index("OCR")]
        return (
            f"{prefix}OCR 回合状态分割: {m2.group(1)} 个粗粒度回合 "
            f"(duration={m2.group(2)}s, markers={m2.group(3)})"
        )
    if "kick worker" in line:
        line = re.sub(
            r"\[INFO\] lsc\.handlers: .*?kick worker",
            "[INFO] lsc.handlers: 持续分析 kick worker",
            line,
        )
    # 仅入列 / ocr升格 patterns with Arguments
    if "Arguments:" in line and ROOM in line:
        pass
    return line

selected = []
status_snaps = []
line_no = 0

with log.open("r", encoding="utf-8", errors="replace") as f:
    pending_ts = None
    for raw in f:
        line_no += 1
        if raw.startswith("2026-07-15 21:"):
            minute = int(raw[14:16])
            if not (25 <= minute <= 48):
                continue
            if not in_window_keys.search(raw):
                continue
            if "mse_" in raw or "rooms_updated" in raw:
                continue
            if ("Sending WS response" in raw or "Received WS message" in raw):
                if ROOM not in raw and "confirm_highlight" not in raw and "export_clip" not in raw:
                    continue
            selected.append((line_no, reconstruct(raw.rstrip()), raw[11:19]))
            pending_ts = raw[11:19]
        elif "Arguments:" in raw and ROOM in raw and "get_continuous_analysis_status" in raw:
            fields = {}
            for key in [
                "analyzed_duration", "recorded_duration", "confirmed_rounds",
                "pending_rounds", "total_highlights", "scan_mode", "scan_reason",
                "scan_range", "refine_with_ocr", "round_phase", "progress",
                "full_rescan", "analysis_stage",
            ]:
                m = re.search(rf"'{key}': ([^,}}]+)", raw)
                if m:
                    fields[key] = m.group(1).strip()
            if fields and pending_ts:
                status_snaps.append((line_no, pending_ts, fields))

cats = {k: [] for k in ["kick", "no_round", "warn", "confirm", "export", "enqueue", "other"]}
for ln, line, ts in selected:
    item = (ln, line, ts)
    if "kick worker" in line:
        cats["kick"].append(item)
    elif "markers=" in line and "round_detector" in line:
        cats["no_round"].append(item)
    elif "confirm_highlight" in line:
        cats["confirm"].append(item)
    elif "export_clip" in line:
        cats["export"].append(item)
    elif any(k in line for k in ["仅入列", "ocr升格", "入列("]):
        cats["enqueue"].append(item)
    elif ("WARNING" in line or "ERROR" in line) and re.search(r"OCR|round|回合", line, re.I):
        cats["warn"].append(item)
    elif re.search(r"OCR|round_detector|Onset|回合|分割", line, re.I):
        cats["other"].append(item)

picked = {}
for name in ["kick", "no_round", "warn", "confirm", "export", "enqueue", "other"]:
    for ln, line, ts in cats[name]:
        picked[ln] = (line, ts)

# Attach compact status before each no_round / kick
def nearest_status(ts):
    best = None
    for ln, sts, fields in status_snaps:
        if sts <= ts:
            best = fields
        else:
            break
    return best

# Build context blocks for 无有效回合
context_blocks = []
for ln, line, ts in cats["no_round"]:
    # find kick within previous 15 lines of selected
    prev_kick = None
    for kln, kline, kts in cats["kick"]:
        if kts <= ts:
            prev_kick = (kln, kline, kts)
    st = nearest_status(ts)
    context_blocks.append((ln, line, ts, prev_kick, st))

timeline = sorted((ln, picked[ln][0]) for ln in picked)
if len(timeline) > 80:
    keep = set()
    for name in ["kick", "no_round", "warn", "confirm", "export", "enqueue"]:
        keep.update(x[0] for x in cats[name])
    others = [x for x in cats["other"] if x[0] not in keep]
    budget = max(0, 70 - len(keep))
    if others and budget:
        step = max(1, len(others) // budget)
        for x in others[::step][:budget]:
            keep.add(x[0])
    timeline = [(ln, picked[ln][0]) for ln in sorted(keep)]

out_lines = []
out_lines.append("=== LSC OCR/回合 精选 2026-07-15 21:25-21:48 | room=144e9b6f (月子) ===")
out_lines.append("注: stdout 中文因 Logging rollover 损坏；「无有效回合」文案已按 round_detector.py 源码还原。")
out_lines.append("")
out_lines.append("--- 「无有效回合」及上下文 (markers / 前回 kick / 状态) ---")
for ln, line, ts, prev_kick, st in context_blocks:
    out_lines.append(f"")
    out_lines.append(f"[{ts}] 无有效回合 @ L{ln}")
    if prev_kick:
        out_lines.append(f"  prev kick L{prev_kick[0]}: {prev_kick[1][:220]}")
    if st:
        out_lines.append(
            "  status: "
            + ", ".join(f"{k}={v}" for k, v in st.items()
                        if k in ("analyzed_duration","recorded_duration","confirmed_rounds",
                                 "pending_rounds","total_highlights","scan_mode","scan_reason",
                                 "round_phase","refine_with_ocr","full_rescan"))
        )
    out_lines.append(f"  WARN: {line}")

out_lines.append("")
out_lines.append(f"--- 时间线精选 ({len(timeline)} 行) ---")
for ln, line in timeline:
    if len(line) > 420:
        line = line[:420] + "..."
    out_lines.append(f"L{ln}|{line}")

out.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
print(
    f"OK timeline={len(timeline)} no_round={len(cats['no_round'])} "
    f"kick={len(cats['kick'])} confirm={len(cats['confirm'])} "
    f"export={len(cats['export'])} warn={len(cats['warn'])} other={len(cats['other'])}"
)
