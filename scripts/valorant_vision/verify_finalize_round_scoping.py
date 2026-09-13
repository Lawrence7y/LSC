"""实测定稿口径验收（只读录像，不写 sidecar/草稿）。

用法：
  python scripts/valorant_vision/verify_finalize_round_scoping.py --recording <mp4> \
    --fixture tests/fixtures/broadcast_export_case_20260911_2045 \
    --profile core|with-caps --out docs/reports/verify-round-scoping-<date>.json

profile 语义：
  core      = 只要求 Part 1（135 定稿 + 可复现项不劣化 + 076 仍拒）
  with-caps = 额外要求 Part 2（123 定稿）
退出码：0 全部达标 / 1 有不达标 / 2 用法或媒体错误。
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lsc.analyzer.valorant_broadcast import audit_broadcast_rounds_with_outcomes  # noqa: E402
from lsc.analyzer.valorant_frame_classifier import ValorantFrameClassifier  # noqa: E402

FFMPEG = r"C:/Users/Administrator/AppData/Roaming/lsc-electron/runtime/ffmpeg/ffmpeg.exe"
TOL = 1.5  # 秒

# 绝对判据：只放"能从归档夹具复现"的项（055/070/076/135，见 §6.2）
EXPECT_CORE: dict[str, tuple[float | None, str, float]] = {
    "round-000045": (514.25, "accepted", TOL),   # 补丁后可精确复现生产值（改前 534.735/coarse）
    "round-000055": (672.75, "accepted", TOL),
    "round-000070": (746.813, "accepted", TOL),
    "round-000076": (None, "rejected", 0.0),
    "round-000135": (1423.25, "accepted", TOL),   # Part 1 的收益；改前必红
    # 123：生产窗口下出点未被延长（end=1346），重开战点(1350)落在区间之外 ⇒ L1 不触发；
    # 既有门禁以 NO_EXCLUSION_EVIDENCE 拒之。L1 的价值体现在"出点被延长到后一回合"的路径
    # （宽窗口 / 将来任何 end 延长），见 docs/reports/candidate-dedup-merge-draft-20260912.md。
    "round-000123": (None, "manual_review", 0.0),
}
# L1 落地后本夹具已无「区间干净但真实出点超出 +45s」的候选 ⇒ with-caps 无额外期望；
# 该 profile 留给将来同类候选（含 123 若某天不再跨回合）。
EXPECT_WITH_CAPS = dict(EXPECT_CORE)

ROUND_KEYS = ["round-000045", "round-000055", "round-000070",
              "round-000076", "round-000123", "round-000135"]


def _probe_duration(recording: str, ffmpeg: str) -> float:
    ffprobe = str(Path(ffmpeg).with_name("ffprobe.exe"))
    if not Path(ffprobe).is_file():
        ffprobe = shutil.which("ffprobe") or "ffprobe"
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", recording],
            capture_output=True, text=True, timeout=60)
        return float((out.stdout or "0").strip() or 0.0)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0


def _candidates(fixture: Path) -> list[dict[str, Any]]:
    """候选 = 收尾 sidecar 的 coarse 边界 + result_ts（生产候选取自扫掠结果）。"""
    fin = json.loads(sorted(fixture.glob("*.finalization.json"))[0].read_text(encoding="utf-8"))
    side = {c["round_key"]: c for c in fin["accepted_candidates"]}
    raw = {"round-000076": (763.7, 843.4), "round-000123": (1232.0, 1346.0),
           "round-000135": (1352.0, 1445.0)}
    out = []
    for key in ROUND_KEYS:
        extra: dict[str, Any] = {}
        if key in side:
            s, e = float(side[key]["start_coarse"]), float(side[key]["end_coarse"])
            if side[key].get("result_ts") is not None:
                extra["result_ts"] = float(side[key]["result_ts"])
        else:
            s, e = raw[key]
        out.append({"round_key": key, "start": s, "end": e, "start_coarse": s, "end_coarse": e,
                    "start_by": "ocr_combat", "end_by": "next_prep",
                    "confirm_status": "pending", **extra})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--recording", required=True)
    ap.add_argument("--fixture", required=True)
    ap.add_argument("--profile", choices=["core", "with-caps"], default="core")
    ap.add_argument("--ffmpeg", default=FFMPEG)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    rec = Path(a.recording)
    if not rec.is_file():
        print(f"录像不存在: {rec}", file=sys.stderr)
        return 2
    duration = _probe_duration(str(rec), a.ffmpeg)
    expect = EXPECT_CORE if a.profile == "core" else EXPECT_WITH_CAPS
    clf = ValorantFrameClassifier(profile="broadcast")
    clf.load()
    rows, failed = [], []
    for cand in _candidates(Path(a.fixture)):
        t0 = time.monotonic()
        outs = audit_broadcast_rounds_with_outcomes(
            [dict(cand)], str(rec), ffmpeg_path=a.ffmpeg, classifier=clf,
            available_end=duration or None, finalize=True)
        row: dict[str, Any] = {"round_key": cand["round_key"],
                               "elapsed_sec": round(time.monotonic() - t0, 2)}
        for o in outs:
            c = o.candidate if isinstance(getattr(o, "candidate", None), dict) else {}
            row.update(status=getattr(o, "status", None), reason=getattr(o, "reason", None),
                       end=c.get("end"), end_by=c.get("end_by"),
                       end_quality=c.get("end_quality"), audit=c.get("broadcast_audit"),
                       scan_end=c.get("broadcast_audit_scan_end"))
        want = expect.get(cand["round_key"])
        if want is not None:
            w_end, w_status, tol = want
            ok = row.get("status") == w_status
            note = ""
            if w_end is not None and row.get("end") is not None:
                note = f"delta={abs(float(row['end']) - w_end):.3f}s"
                ok = ok and abs(float(row["end"]) - w_end) <= tol
                ok = ok and row.get("end_quality") == "precise"
            row["expect"] = {"end": w_end, "status": w_status, "tol": tol, "note": note, "ok": ok}
            if not ok:
                failed.append(row["round_key"])
        rows.append(row)
        print(f"{row['round_key']}: {row.get('status')} end={row.get('end')} "
              f"{row.get('end_by')} {row.get('end_quality')} ({row['elapsed_sec']}s)",
              file=sys.stderr, flush=True)
    report = {"profile": a.profile, "recording": str(rec), "recording_duration_sec": duration,
              "rows": rows, "failed": failed, "passed": not failed}
    out = Path(a.out)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(out)
    if failed:
        print(f"不达标: {', '.join(failed)}", file=sys.stderr)
        return 1
    print(f"全部达标 -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
