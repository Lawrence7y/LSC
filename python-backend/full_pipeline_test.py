import os, sys, time, json
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import psutil
from datetime import datetime

VIDEO_PATH = (
    r"D:\desktop\新建文件夹\新建文件夹\新建文件夹"
    r"\douyin_HangHang_bfc241\recording_20260711_155959_30e760.mp4"
)
OUTPUT_DIR = r"D:\desktop\新建文件夹\新建文件夹\新建文件夹"


def export_clip(source_path, output_path, ffmpeg_path, start_sec, end_sec):
    import subprocess
    duration = end_sec - start_sec
    if duration <= 0:
        return False, "zero duration", 0

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

    t0 = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, timeout=60)
    wall = time.perf_counter() - t0

    if proc.returncode != 0:
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
            return False, f"both failed", 0
        size_mb = os.path.getsize(output_path) / 1e6 if os.path.isfile(output_path) else 0
        return True, f"{wall+wall2:.2f}s(re)", size_mb

    size_mb = os.path.getsize(output_path) / 1e6 if os.path.isfile(output_path) else 0
    return True, f"{wall:.2f}s(copy)", size_mb


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
    if duration <= 0:
        print("[ERROR] Cannot probe duration")
        sys.exit(1)

    print("="*60)
    print("FULL PIPELINE TEST: detect -> export -> verify")
    print(f"  Video: {os.path.basename(VIDEO_PATH)} ({duration:.0f}s)")
    print(f"  Output: {OUTPUT_DIR}")
    print(f"  Time: {datetime.now().strftime('%H:%M:%S')}")
    print("="*60)

    # Step 1: Detection
    from lsc.analyzer.round_detector import detect_valorant_rounds
    print("\n[1/3] Running detect_valorant_rounds...")
    mem_before = proc.memory_info().rss / 1e6
    t0 = time.perf_counter()
    all_rounds = detect_valorant_rounds(VIDEO_PATH, duration=duration, refine_with_ocr=False)
    detect_time = time.perf_counter() - t0
    mem_after = proc.memory_info().rss / 1e6
    print(f"  Detected {len(all_rounds)} rounds in {detect_time:.3f}s")
    print(f"  Memory: {mem_before:.1f} -> {mem_after:.1f} MB")

    # Verify 548-688 coverage
    covered_548_688 = any(r.get('start', 0) <= 688 and r.get('end', 0) >= 548 for r in all_rounds)
    print(f"  548-688 region covered: {'YES' if covered_548_688 else 'NO'}")

    # Step 2: Filter and export
    print(f"\n[2/3] Exporting clips...")
    valid_rounds = []
    for r in all_rounds:
        start = r.get('start', 0)
        end = r.get('end', 0)
        if end - start >= 3.0 and start >= 0 and end <= duration:
            valid_rounds.append(r)
    print(f"  Valid rounds: {len(valid_rounds)}")

    # Clean up old clips first
    for f in os.listdir(OUTPUT_DIR):
        if f.startswith("clip_") and f.endswith(".mp4"):
            os.remove(os.path.join(OUTPUT_DIR, f))

    exported = []
    t_export_start = time.perf_counter()
    for i, r in enumerate(valid_rounds[:60]):
        start = r.get('start', 0)
        end = r.get('end', 0)
        tail_by = r.get('tail_by', '?')
        score = r.get('score', 0)
        round_idx = r.get('round_index', i + 1)

        clip_fn = f"clip_r{round_idx:02d}_{start:.0f}s-{end:.0f}s_{tail_by}.mp4"
        clip_path = os.path.join(OUTPUT_DIR, clip_fn)

        ok, msg, size_mb = export_clip(VIDEO_PATH, clip_path, cfg_ffmpeg, start, end)
        status = "OK" if ok else "FAIL"
        print(f"  [{status}] {clip_fn}: {msg}, {size_mb:.1f}MB")

        if ok:
            exported.append({"file": clip_fn, "start": start, "end": end, "score": score, "tail_by": tail_by})

        if (i + 1) % 20 == 0:
            time.sleep(0.5)

    total_export_time = time.perf_counter() - t_export_start

    # Step 3: Summary
    print(f"\n[3/3] Summary")
    print("="*60)
    print(f"  Rounds detected: {len(all_rounds)}")
    print(f"  Valid rounds: {len(valid_rounds)}")
    print(f"  Clips exported: {len(exported)}")
    print(f"  Detection time: {detect_time:.3f}s")
    print(f"  Export time: {total_export_time:.1f}s")
    print(f"  Avg per clip: {total_export_time/max(len(exported),1):.3f}s")
    print(f"  Peak memory: {proc.memory_info().rss/1e6:.1f} MB")
    print(f"  548-688 covered: {'YES' if covered_548_688 else 'NO'}")
    print(f"  Completed: {datetime.now().strftime('%H:%M:%S')}")

    # Save manifest
    manifest_path = os.path.join(OUTPUT_DIR, "clips_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump({
            "video": VIDEO_PATH,
            "duration": duration,
            "timestamp": datetime.now().isoformat(),
            "total_rounds": len(all_rounds),
            "exported": len(exported),
            "detect_time": detect_time,
            "export_time": total_export_time,
            "covered_548_688": covered_548_688,
            "clips": exported,
        }, f, ensure_ascii=False, indent=2)
    print(f"  Manifest: {manifest_path}")

    # Verify output files exist
    print(f"\n  Output dir contents:")
    for fn in sorted(os.listdir(OUTPUT_DIR)):
        if fn.startswith("clip_") and fn.endswith(".mp4"):
            fp = os.path.join(OUTPUT_DIR, fn)
            sz = os.path.getsize(fp) / 1e6
            print(f"    {fn} ({sz:.1f}MB)")


if __name__ == "__main__":
    main()
