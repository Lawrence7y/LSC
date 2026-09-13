"""离线复算赛事候选的视觉审计结论（**只读**，不写录像/sidecar/草稿）。

用途（2026-09-11 20:45 现场）：回答"某个候选在后视素材充足时究竟能不能定稿"，
而不是靠推测。候选可取导出请求的 clips.json（`{"clips": [...]}` 或裸列表）。

示例：
    python scripts/valorant_vision/reaudit_broadcast_candidates.py \\
      --recording "…/2026-09-11_20-10-29_至_2026-09-11_20-38-42.mp4" \\
      --candidates tests/fixtures/broadcast_export_case_20260911_2045/clips.json \\
      --round-keys round-000076,round-000123,round-000135 \\
      --out docs/reports/reaudit-2045-20260911.json

判定口径与生产一致：`audit_broadcast_rounds_with_outcomes` + `finalize=True`
（离线时文件已定格，后视窗口按完整时长给足），逐条记录 outcome/end_by/end_quality/耗时。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lsc.analyzer.valorant_broadcast import audit_broadcast_rounds_with_outcomes  # noqa: E402
from lsc.analyzer.valorant_frame_classifier import ValorantFrameClassifier  # noqa: E402

_DEFAULT_FFMPEG = r"C:/Users/Administrator/AppData/Roaming/lsc-electron/runtime/ffmpeg/ffmpeg.exe"


def _load_candidates(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        items = payload.get("clips") or payload.get("candidates") or []
    else:
        items = payload
    return [item for item in items if isinstance(item, dict)]


def _to_audit_item(candidate: dict[str, Any]) -> dict[str, Any]:
    """把导出/切片形态的候选还原成审计引擎期望的 OCR 候选形态。"""
    start = float(candidate.get("start") or 0.0)
    end = float(candidate.get("end") or 0.0)
    return {
        "round_key": str(candidate.get("round_key") or ""),
        "start": start,
        "end": end,
        "start_coarse": float(candidate.get("start_coarse") or start),
        "end_coarse": float(candidate.get("end_coarse") or end),
        "start_by": str(candidate.get("start_by") or "ocr_combat"),
        "end_by": str(candidate.get("end_by") or "next_prep"),
        "confirm_status": str(candidate.get("confirm_status") or "pending"),
    }


def _probe_duration(recording: str, ffmpeg: str) -> float:
    import shutil
    import subprocess

    ffprobe = str(Path(ffmpeg).with_name("ffprobe.exe"))
    if not Path(ffprobe).is_file():
        ffprobe = shutil.which("ffprobe") or "ffprobe"
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", recording],
            capture_output=True, text=True, timeout=60,
        )
        return float((out.stdout or "0").strip() or 0.0)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recording", required=True, help="定稿录像路径（只读）")
    parser.add_argument("--candidates", required=True, help="候选 JSON（clips.json 或裸列表）")
    parser.add_argument("--round-keys", default="", help="逗号分隔；缺省=全部候选")
    parser.add_argument("--out", required=True, help="报告 JSON 输出路径")
    parser.add_argument("--ffmpeg", default=_DEFAULT_FFMPEG)
    parser.add_argument("--lookahead-sec", type=float, default=45.0)
    args = parser.parse_args(argv)

    recording = Path(args.recording)
    if not recording.is_file():
        print(f"录像不存在: {recording}", file=sys.stderr)
        return 2
    candidates = _load_candidates(Path(args.candidates))
    wanted = {item.strip() for item in args.round_keys.split(",") if item.strip()}
    if wanted:
        candidates = [
            item for item in candidates if str(item.get("round_key") or "") in wanted
        ]
    if not candidates:
        print("没有匹配的候选", file=sys.stderr)
        return 2

    duration = _probe_duration(str(recording), args.ffmpeg)
    classifier = ValorantFrameClassifier(profile="broadcast")
    classifier.load()

    results: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: float(item.get("start") or 0.0)):
        item = _to_audit_item(candidate)
        started = time.monotonic()
        entry: dict[str, Any] = {
            "round_key": item["round_key"],
            "input": {
                "start": item["start"],
                "end": item["end"],
                "end_by": item["end_by"],
                "confirm_status": item["confirm_status"],
            },
            "recording": str(recording),
            "available_end": duration,
        }
        try:
            outcomes = audit_broadcast_rounds_with_outcomes(
                [dict(item)],
                str(recording),
                ffmpeg_path=args.ffmpeg,
                classifier=classifier,
                available_end=duration or None,
                # 离线=文件已定格：后视窗口给足，只回答"素材够时能否定稿"
                finalize=True,
                lookahead_sec=args.lookahead_sec,
            )
        except Exception as exc:  # noqa: BLE001 — 单条失败不阻断其余候选
            entry["error"] = f"{type(exc).__name__}: {exc}"
            outcomes = []
        entry["elapsed_sec"] = round(time.monotonic() - started, 3)
        entry["outcomes"] = [
            {
                "status": getattr(outcome, "status", None),
                "reason": getattr(outcome, "reason", None),
                "start": getattr(outcome, "start", None),
                "end": getattr(outcome, "end", None),
                "end_by": getattr(getattr(outcome, "candidate", None), "get", lambda *_: None)("end_by")
                if getattr(outcome, "candidate", None) else None,
                "candidate": {
                    key: getattr(outcome, "candidate", {}).get(key)
                    for key in ("start", "end", "confirm_status", "broadcast_audit",
                                "end_by", "end_quality", "end_refined", "boundary_quality")
                    if isinstance(getattr(outcome, "candidate", None), dict)
                },
            }
            for outcome in outcomes
        ]
        results.append(entry)
        print(
            f"{item['round_key']}: {[o['status'] for o in entry['outcomes']]} "
            f"({entry['elapsed_sec']}s)",
            file=sys.stderr,
        )

    report = {
        "source": "reaudit_broadcast_candidates",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "recording": str(recording),
        "recording_duration_sec": duration,
        "lookahead_sec": args.lookahead_sec,
        "finalize": True,
        "candidate_count": len(results),
        "results": results,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out_path)
    print(f"已写入 {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
