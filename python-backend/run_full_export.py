import os, sys
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import time
import json
import psutil
from datetime import datetime

VIDEO_PATH = (
    r"D:\desktop\新建文件夹\新建文件夹\新建文件夹"
    r"\douyin_HangHang_bfc241\recording_20260711_155959_30e760.mp4"
)
OUTPUT_DIR = r"D:\desktop\新建文件夹\新建文件夹\新建文件夹"
WINDOW_SEC = 120.0  # per cycle
NUM_CYCLES = 47  # full video: 5685/120 ≈ 47
INTERVAL_SEC = 5  # wait between cycles (compressed)


def export_clip(source_path, output_path, ffmpeg_path, start_sec, end_sec):
    """Export a clip using FFmpeg -c copy (fast, no re-encode).
    
    Uses stream copy for speed. Output is GOP-aligned (may be ~1-2s off at boundaries).
    For precise frame-accurate cuts, re-encode instead.
    """
    import subprocess

    duration = end_sec - start_sec
    if duration <= 0:
        return False, "zero duration"

    cmd = [
        ffmpeg_path, "-y",
        "-ss", f"{start_sec:.3f}",
        "-i", source_path,
        "-t", f"{duration:.3f}",
        "-c", "copy",
        "-avoid_negative_ts", "make_zero",
        "-movflags", "+faststart",
        output_path,
    ]
    cmd = [str(c) for c in cmd]

    t0 = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, timeout=60)
    wall = time.perf_counter() - t0

    if proc.returncode != 0:
        stderr_tail = proc.stderr.decode("utf-8", errors="replace")[-300:]
        # Fallback: re-encode if copy fails (e.g., source has unusual codec)
        cmd_re = [
            ffmpeg_path, "-y",
            "-ss", f"{start_sec:.3f}",
            "-i", source_path,
            "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            output_path,
        ]
        t1 = time.perf_counter()
        proc2 = subprocess.run(cmd_re, capture_output=True, timeout=120)
        wall2 = time.perf_counter() - t1
        if proc2.returncode != 0:
            return False, f"Both copy and re-encode failed"
        size_mb = os.path.getsize(output_path) / 1e6 if os.path.isfile(output_path) else 0
        return True, f"{wall+wall2:.2f}s(fallback), {size_mb:.1f}MB"

    size_mb = os.path.getsize(output_path) / 1e6 if os.path.isfile(output_path) else 0
    return True, f"{wall:.2f}s, {size_mb:.1f}MB"


def run_full_video(proc, video_path, duration, ffmpeg_path):
    """Run detection ONCE on full video, slice into windows for export."""
    from lsc.analyzer.round_detector import detect_valorant_rounds

    print(f"\n{'='*60}")
    print(f"Running detect_valorant_rounds on FULL video ({duration:.0f}s)")
    print(f"{'='*60}")

    mem_before = proc.memory_info().rss / 1e6
    t0 = time.perf_counter()

    all_rounds = detect_valorant_rounds(
        video_path,
        duration=duration,
        refine_with_ocr=False,
    )

    detect_time = time.perf_counter() - t0
    mem_after = proc.memory_info().rss / 1e6

    print(f"  Detected {len(all_rounds)} rounds in {detect_time:.3f}s")
    print(f"  Memory: {mem_before:.1f} -> {mem_after:.1f} MB")

    # Filter out rounds with duration <= min_combat_duration (or start>=end)
    valid_rounds = []
    for r in all_rounds:
        start = r.get('start', 0)
        end = r.get('end', 0)
        if end - start >= 3.0 and start >= 0 and end <= duration:
            valid_rounds.append(r)

    print(f"  Valid rounds (duration >= 3s): {len(valid_rounds)}")

    # Export each valid round
    exported = []
    for i, r in enumerate(valid_rounds):
        start = r.get('start', 0)
        end = r.get('end', 0)
        tail_by = r.get('tail_by', '?')
        score = r.get('score', 0)
        round_idx = r.get('round_index', i + 1)

        clip_fn = f"clip_r{round_idx:02d}_{start:.0f}s-{end:.0f}s_{tail_by}.mp4"
        clip_path = os.path.join(OUTPUT_DIR, clip_fn)

        ok, msg = export_clip(video_path, clip_path, ffmpeg_path, start, end)

        status = "OK" if ok else "FAIL"
        print(f"  [{status}] {clip_fn} ({msg})")

        if ok:
            exported.append({
                "file": clip_fn,
                "start": start, "end": end,
                "score": score,
                "tail_by": tail_by,
            })

        # Brief pause to let system breathe
        time.sleep(0.5)

    return detect_time, all_rounds, exported


def main():
    proc = psutil.Process()

    if not os.path.isfile(VIDEO_PATH):
        print(f"[ERROR] Not found: {VIDEO_PATH}")
        sys.exit(1)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    from lsc.config import load_config
    cfg = load_config()
    cfg_ffmpeg = cfg.ffmpeg_path or "ffmpeg"

    import subprocess
    ffprobe = cfg.ffprobe_path or "ffprobe"
    cmd = [ffprobe, "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", VIDEO_PATH]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    duration = float(result.stdout.strip()) if result.returncode == 0 else 0

    print("="*60)
    print("Valorant Round Detector + Exporter (Full Video)")
    print(f"  Video: {os.path.basename(VIDEO_PATH)}")
    print(f"  Duration: {duration:.0f}s")
    print(f"  Output: {OUTPUT_DIR}")
    print(f"  Started: {datetime.now().strftime('%H:%M:%S')}")
    print("="*60)

    # IDLE baseline
    time.sleep(1)
    idle_cpu = proc.cpu_percent(interval=1)
    print(f"[IDLE] CPU={idle_cpu:.1f}% Mem={proc.memory_info().rss/1e6:.1f}MB")

    # SINGLE PASS detection + export
    detect_time, all_rounds, exported = run_full_video(
        proc, VIDEO_PATH, duration, cfg_ffmpeg
    )

    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print(f"  Total rounds detected: {len(all_rounds)}")
    print(f"  Valid rounds: {len(exported)}")
    print(f"  Clips exported: {len(exported)}")
    print(f"  Detection time: {detect_time:.3f}s")
    print(f"  Peak memory: {proc.memory_info().rss/1e6:.1f} MB")
    print(f"  Output: {OUTPUT_DIR}")
    print(f"  Completed: {datetime.now().strftime('%H:%M:%S')}")
    print("="*60)

    # Save manifest
    manifest = {
        "video": VIDEO_PATH,
        "duration": duration,
        "timestamp": datetime.now().isoformat(),
        "total_rounds": len(all_rounds),
        "exported": len(exported),
        "detect_time": detect_time,
        "clips": exported,
    }
    manifest_path = os.path.join(OUTPUT_DIR, "clips_manifest_fixed.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"\n  Manifest saved: {manifest_path}")

    # Memory check
    mem_drift = proc.memory_info().rss / 1e6 - 24.2  # from idle baseline
    if mem_drift > 50:
        print(f"  POSSIBLE MEMORY LEAK: {mem_drift:+.1f} MB drift")
    else:
        print(f"  Memory stable: {mem_drift:+.1f} MB drift")


if __name__ == "__main__":
    main()
