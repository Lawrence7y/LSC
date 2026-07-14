"""Run continuous analysis with resource monitoring."""
import os
import sys
import time
import json
import tracemalloc
from datetime import datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import psutil

VIDEO_PATH = (
    r"D:\desktop\新建文件夹\新建文件夹\新建文件夹"
    r"\douyin_流年c_091e61\recording_20260712_110152_8a1009.mp4"
)
ANALYSIS_INTERVAL = 5  # seconds between cycles (compressed for testing)
NUM_CYCLES = 5


def get_system_baseline(proc):
    """Get current CPU% baseline."""
    return {
        "cpu_percent": proc.cpu_percent(),
        "mem_rss_mb": proc.memory_info().rss / 1e6,
        "num_threads": proc.num_threads(),
    }


def get_child_processes(proc):
    """Get child processes (ffmpeg etc.)."""
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


def format_rounds(rounds):
    """Format round detection results."""
    lines = []
    for i, r in enumerate(rounds):
        start = r.get("start_sec", 0)
        end = r.get("end_sec", 0)
        score = r.get("score", 0)
        tail_by = r.get("tail_by", "?")
        title = r.get("title", f"Round {i+1}")
        lines.append(f"  [{i+1}] {start:6.1f}s - {end:6.1f}s  score={score:.2f}  tail_by={tail_by}  title={title}")
    return "\n".join(lines) if lines else "  (no rounds)"


def run_analysis_cycle(proc, video_path, duration, cycle_num, scan_start, scan_end):
    """Run one analysis cycle and measure all resources."""
    from lsc.analyzer.round_detector import detect_valorant_rounds

    print(f"\n{'='*60}")
    print(f"Cycle {cycle_num}: analyzing {scan_start:.0f}s - {scan_end:.0f}s")
    print(f"{'='*60}")

    # Before state
    mem_before = proc.memory_info().rss / 1e6
    cpu_before = proc.cpu_percent()
    snap_before = tracemalloc.take_snapshot()

    # Get children before
    children_before = get_child_processes(proc)

    # Run detection
    t0 = time.perf_counter()
    rounds = detect_valorant_rounds(
        video_path,
        duration=duration,
        time_range=(scan_start, scan_end),
        refine_with_ocr=False,
    )
    wall_time = time.perf_counter() - t0

    # After state
    mem_after = proc.memory_info().rss / 1e6
    cpu_after = proc.cpu_percent()
    snap_after = tracemalloc.take_snapshot()
    children_after = get_child_processes(proc)

    # Memory diff
    mem_delta = mem_after - mem_before

    # Top memory growth
    top_growth = snap_after.compare_to(snap_before, "lineno")[:3]

    # FFmpeg child lifecycle
    ffmpeg_children = [c for c in children_after if "ffmpeg" in c.get("name", "").lower()]

    # Output
    print(f"  Wall time:     {wall_time:.3f}s")
    print(f"  Memory before: {mem_before:.1f} MB")
    print(f"  Memory after:  {mem_after:.1f} MB")
    print(f"  Memory delta:  {mem_delta:+.1f} MB")
    print(f"  CPU% (after):  {cpu_after:.1f}%")
    print(f"  Active children: {len(children_after)} (ffmpeg: {len(ffmpeg_children)})")
    print(f"\n  Detected {len(rounds)} rounds:")
    print(format_rounds(rounds))

    if top_growth:
        print(f"\n  Top 3 memory growth:")
        for stat in top_growth:
            if stat.size_diff > 0:
                print(f"    {stat.traceback.format()[-1]}")
                print(f"      +{stat.size_diff / 1024:.1f} KB")

    # Clean up any lingering ffmpeg
    for c in ffmpeg_children:
        try:
            child = psutil.Process(c["pid"])
            if child.status() not in ("terminated", "zombie"):
                print(f"  [cleanup] Terminating lingering ffmpeg PID={c['pid']}")
                child.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    return {
        "cycle": cycle_num,
        "wall_time": wall_time,
        "mem_before_mb": mem_before,
        "mem_after_mb": mem_after,
        "mem_delta_mb": mem_delta,
        "num_rounds": len(rounds),
        "rounds": rounds,
    }


def main():
    proc = psutil.Process()

    # Verify file
    if not os.path.isfile(VIDEO_PATH):
        print(f"[ERROR] Video file not found: {VIDEO_PATH}")
        sys.exit(1)

    # Get video duration
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
    print(f"Continuous Analysis Resource Monitor")
    print(f"  Video: {os.path.basename(VIDEO_PATH)} ({duration:.0f}s)")
    print(f"  Cycles: {NUM_CYCLES}")
    print(f"  Python PID: {proc.pid}")
    print(f"  Start: {datetime.now().strftime('%H:%M:%S')}")
    print("="*60)

    # System idle baseline
    tracemalloc.start()
    time.sleep(2)  # Let CPU% stabilize
    idle_cpu = proc.cpu_percent(interval=1.5)
    idle_mem = proc.memory_info().rss / 1e6
    print(f"\n[IDLE] CPU={idle_cpu:.1f}%  Memory={idle_mem:.1f} MB")

    # Simulate continuous analysis
    results = []
    window_size = 120  # seconds per analysis window
    scan_pos = 0.0

    for cycle in range(1, NUM_CYCLES + 1):
        scan_end = min(scan_pos + window_size, duration)

        result = run_analysis_cycle(
            proc, VIDEO_PATH, duration, cycle, scan_pos, scan_end
        )
        results.append(result)

        scan_pos = scan_end
        if scan_pos >= duration:
            print(f"\nReached end of video at {scan_pos:.0f}s")
            break

        # Wait interval
        print(f"\n  [waiting {ANALYSIS_INTERVAL}s until next cycle...]")
        time.sleep(ANALYSIS_INTERVAL)

    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)

    total_wall = sum(r["wall_time"] for r in results)
    total_rounds = sum(r["num_rounds"] for r in results)
    peak_mem = max(r["mem_after_mb"] for r in results)
    avg_mem_delta = sum(r["mem_delta_mb"] for r in results) / len(results)

    print(f"  Total analysis cycles:  {len(results)}")
    print(f"  Total wall time:        {total_wall:.3f}s")
    print(f"  Avg per cycle:          {total_wall/len(results):.3f}s")
    print(f"  Total rounds detected:  {total_rounds}")
    print(f"  Peak memory:            {peak_mem:.1f} MB")
    print(f"  Avg memory delta/cycle: {avg_mem_delta:+.1f} MB")
    print(f"  CPU during analysis:    ~{proc.cpu_percent():.1f}% (cumulative)")

    # Final memory snapshot
    final_snap = tracemalloc.take_snapshot()
    final_stats = final_snap.statistics("lineno")[:5]
    print(f"\n  Final memory top 5:")
    for stat in final_stats:
        size = stat.size / 1024
        if size > 100:
            print(f"    {stat.traceback.format()[-1]}: {size:.1f} KB")

    print("\n" + "="*60)
    print(f"Resource monitor complete. {datetime.now().strftime('%H:%M:%S')}")

    memory_leak = results[-1]["mem_after_mb"] - results[0]["mem_before_mb"]
    if memory_leak > 50:
        print(f"⚠️  WARNING: Possible memory leak! {memory_leak:+.1f} MB over {len(results)} cycles")
    else:
        print(f"✓ Memory stable. Total drift: {memory_leak:+.1f} MB over {len(results)} cycles")


if __name__ == "__main__":
    main()
