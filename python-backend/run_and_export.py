"""Run detect_valorant_rounds on the test video and export clips using ClipExporter.

Pipeline:
    detect_valorant_rounds -> ClipExporter -> output clips to specified directory
    Monitor CPU/memory throughout.
"""
import os
import sys
import time
import json
from datetime import datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import psutil

VIDEO_PATH = (
    r"D:\desktop\新建文件夹\新建文件夹\新建文件夹"
    r"\douyin_HangHang_bfc241\recording_20260711_155959_30e760.mp4"
)
OUTPUT_DIR = r"D:\desktop\新建文件夹\新建文件夹\新建文件夹"
NUM_CYCLES = 5
ANALYSIS_INTERVAL = 5


def get_child_processes(proc):
    try:
        children = proc.children(recursive=True)
        return [
            {"pid": c.pid, "name": c.name(), "status": c.status(),
             "cpu_pct": c.cpu_percent(), "mem_mb": c.memory_info().rss / 1e6}
            for c in children
            if c.status() != "zombie"
        ]
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return []


def export_round_video(source_path, output_path, ffmpeg_path, start_sec, end_sec):
    """Use ClipExporter logic to export a clip via FFmpeg copy or transcode."""
    import subprocess

    duration = end_sec - start_sec
    if duration <= 0:
        return False, "zero duration"

    cmd = [
        ffmpeg_path, "-y",
        "-ss", f"{start_sec:.3f}",
        "-i", source_path,
        "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]
    cmd = [str(c) for c in cmd]

    t0 = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, timeout=120)
    wall = time.perf_counter() - t0

    if proc.returncode != 0:
        stderr_tail = proc.stderr.decode("utf-8", errors="replace")[-500:]
        return False, f"FFmpeg exit={proc.returncode}: {stderr_tail}"

    size_mb = os.path.getsize(output_path) / 1e6 if os.path.isfile(output_path) else 0
    return True, f"{wall:.2f}s, {size_mb:.1f}MB"


def run_analysis_cycle(proc, video_path, duration, cycle_num, scan_start, scan_end, ffmpeg_path):
    from lsc.analyzer.round_detector import detect_valorant_rounds

    print(f"\n{'='*60}")
    print(f"Cycle {cycle_num}: analyzing {scan_start:.0f}s - {scan_end:.0f}s")

    mem_before = proc.memory_info().rss / 1e6
    t0 = time.perf_counter()
    rounds = detect_valorant_rounds(
        video_path,
        duration=duration,
        time_range=(scan_start, scan_end),
        refine_with_ocr=False,
    )
    detect_time = time.perf_counter() - t0
    mem_after = proc.memory_info().rss / 1e6

    print(f"  Detection: {detect_time:.3f}s, {len(rounds)} rounds, mem {mem_before:.1f} -> {mem_after:.1f} MB")

    # Export each valid round
    exported = []
    for i, r in enumerate(rounds):
        start = r.get("start_sec", 0)
        end = r.get("end_sec", 0)
        score = r.get("score", 0)
        tail_by = r.get("tail_by", "?")
        title = r.get("title", f"Round_{i+1}")

        # Fix zero-duration rounds
        if end <= start:
            end = start + max(5.0, (scan_end - scan_start) * 0.3)
        if (end - start) < 3.0:
            continue

        safe_title = "".join(c if c.isalnum() or c in "-_" else "_" for c in title)[:40]
        clip_fn = f"clip_c{cycle_num:02d}_r{i+1:02d}_{safe_title}_{start:.0f}-{end:.0f}s.mp4"
        clip_path = os.path.join(OUTPUT_DIR, clip_fn)

        mem_exp_before = proc.memory_info().rss / 1e6
        ok, msg = export_round_video(video_path, clip_path, ffmpeg_path, start, end)
        mem_exp_after = proc.memory_info().rss / 1e6

        status = "OK" if ok else "FAIL"
        print(f"  Export [{status}] {clip_fn}: {msg} (mem {mem_exp_before:.1f} -> {mem_exp_after:.1f} MB)")

        if ok:
            exported.append({
                "file": clip_fn,
                "title": title,
                "start": start, "end": end,
                "score": score, "tail_by": tail_by,
            })

    # Clean up lingering ffmpeg processes
    for c in get_child_processes(proc):
        if "ffmpeg" in c.get("name", "").lower():
            try:
                psutil.Process(c["pid"]).terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

    return {"cycle": cycle_num, "detect_time": detect_time, "rounds": len(rounds), "exported": exported}


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
    print("Valorant Round Detector + Clip Exporter")
    print(f"  Video: {VIDEO_PATH}")
    print(f"  Duration: {duration:.0f}s")
    print(f"  Output: {OUTPUT_DIR}")
    print(f"  Cycles: {NUM_CYCLES}")
    print("="*60)

    time.sleep(1)
    idle_cpu = proc.cpu_percent(interval=1.0)
    print(f"\n[IDLE] CPU={idle_cpu:.1f}%")

    results = []
    window = 120.0
    scan_pos = 0.0

    for cycle in range(1, NUM_CYCLES + 1):
        scan_end = min(scan_pos + window, duration)
        result = run_analysis_cycle(
            proc, VIDEO_PATH, duration, cycle, scan_pos, scan_end, cfg_ffmpeg
        )
        results.append(result)
        scan_pos = scan_end
        if scan_pos >= duration:
            break
        print(f"  [waiting {ANALYSIS_INTERVAL}s...]")
        time.sleep(ANALYSIS_INTERVAL)

    total_exported = sum(len(r["exported"]) for r in results)
    total_rounds = sum(r["rounds"] for r in results)
    total_detect_time = sum(r["detect_time"] for r in results)

    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print(f"  Rounds detected: {total_rounds}")
    print(f"  Clips exported:  {total_exported}")
    print(f"  Total detect time: {total_detect_time:.3f}s")
    print(f"  Peak memory: {proc.memory_info().rss/1e6:.1f} MB")
    print(f"  Clips in: {OUTPUT_DIR}")
    print("="*60)

    manifest_path = os.path.join(OUTPUT_DIR, "clips_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump({
            "video": VIDEO_PATH,
            "duration": duration,
            "timestamp": datetime.now().isoformat(),
            "cycles": results,
            "total_exported": total_exported,
        }, f, ensure_ascii=False, indent=2)
    print(f"  Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
