"""Profile continuous analysis for a real recording file."""
import cProfile
import os
import pstats
import sys
import time
import tracemalloc

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

VIDEO_PATH = (
    r"D:\desktop\新建文件夹\新建文件夹\新建文件夹"
    r"\douyin_流年c_091e61\recording_20260712_110152_8a1009.mp4"
)


def _get_ffmpeg():
    from lsc.config import load_config
    cfg = load_config()
    return cfg.ffmpeg_path or "ffmpeg"


def _probe_duration(ffmpeg, video_path):
    import subprocess
    from lsc.config import load_config
    cfg = load_config()
    ffprobe = cfg.ffprobe_path or "ffprobe"
    cmd = [
        ffprobe, "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    return float(result.stdout.strip()) if result.returncode == 0 else 0.0


def profile_audio_extraction(ffmpeg, video_path, duration, offset=0.0, length=120.0):
    import subprocess
    import numpy as np

    actual_len = min(length, duration - offset)
    if actual_len <= 0:
        return 0.0, b""

    cmd = [
        ffmpeg, "-y", "-ss", f"{offset:.1f}", "-t", f"{actual_len:.1f}",
        "-i", video_path, "-vn", "-ac", "1", "-ar", "16000",
        "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1",
    ]
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, timeout=30)
    wall_time = time.perf_counter() - t0

    pcm_bytes = proc.stdout if proc.returncode == 0 else b""
    return wall_time, pcm_bytes


def profile_energy_analysis(pcm_bytes):
    import numpy as np
    sample_rate = 16000

    if len(pcm_bytes) < 200:
        return 0.0, 0, 0

    audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)

    t0 = time.perf_counter()

    chunksize = int(sample_rate * 0.5)
    n = (len(audio) // chunksize) * chunksize
    if n == 0:
        return time.perf_counter() - t0, 0, 0

    chunks = audio[:n].reshape(-1, chunksize)
    energies = np.sqrt(np.mean(chunks ** 2, axis=1))
    median = np.median(energies)
    mad = np.median(np.abs(energies - median))
    threshold = median + 3.0 * 1.4826 * mad

    combat_mask = energies > threshold
    transitions = np.diff(combat_mask.astype(np.int8))
    onsets = int(np.sum(transitions == 1))
    offsets_val = int(np.sum(transitions == -1))

    wall = time.perf_counter() - t0
    return wall, onsets, offsets_val


def profile_full_analysis(ffmpeg, video_path, duration):
    from lsc.analyzer.round_detector import detect_valorant_rounds

    profiler = cProfile.Profile()
    profiler.enable()

    t0 = time.perf_counter()
    rounds = detect_valorant_rounds(
        video_path, duration=duration, time_range=(0, max(duration, 1.0)),
        refine_with_ocr=False,
    )
    wall_time = time.perf_counter() - t0

    profiler.disable()
    return wall_time, rounds, profiler


def main():
    if not os.path.isfile(VIDEO_PATH):
        print(f"[ERROR] Video file not found: {VIDEO_PATH}")
        sys.exit(1)

    ffmpeg = _get_ffmpeg()
    duration = _probe_duration(ffmpeg, VIDEO_PATH)
    if duration <= 0:
        print("[ERROR] Cannot probe video duration")
        sys.exit(1)

    print("=== Continuous Analysis Profile ===")
    print(f"File: {os.path.basename(VIDEO_PATH)} ({duration:.0f}s)")
    print(f"Path: {VIDEO_PATH}\n")

    tracemalloc.start()
    snap_before = tracemalloc.take_snapshot()

    stages = {}

    print("[1/3] Profiling audio extraction...")
    t_aud, pcm_bytes = profile_audio_extraction(ffmpeg, VIDEO_PATH, duration)
    stages["audio_extraction"] = t_aud
    mem_after_aud = tracemalloc.get_traced_memory()[0]
    print(f"  -> {t_aud:.3f}s, {len(pcm_bytes)//1024}KB PCM extracted")

    print("[2/3] Profiling energy analysis...")
    t_energ, n_on, n_off = profile_energy_analysis(pcm_bytes)
    stages["energy_analysis"] = t_energ
    mem_after_energ = tracemalloc.get_traced_memory()[0]
    print(f"  -> {t_energ:.3f}s, onsets={n_on} offsets={n_off}")

    print("[3/3] Profiling full detection (cProfile)...")
    t_full, rounds, profiler = profile_full_analysis(ffmpeg, VIDEO_PATH, duration)
    stages["full_detection"] = t_full
    snap_after = tracemalloc.take_snapshot()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f"  -> {t_full:.3f}s, {len(rounds)} rounds detected\n")

    print("=== Wall Time Breakdown ===")
    total = sum(stages.values()) or 1.0
    for name, t in stages.items():
        pct = 100 * t / total
        bar = "█" * int(20 * t / total)
        print(f"  {name:<25} {bar:<20} {t:7.3f}s  ({pct:4.1f}%)")
    print(f"  {'TOTAL':<25} {'':<20} {total:7.3f}s\n")

    print("=== cProfile Top 15 (by cumulative time) ===")
    stats = pstats.Stats(profiler)
    stats.sort_stats("cumulative")
    stats.print_stats(15)

    print("\n=== Memory Analysis ===")
    print(f"Current: {current/1e6:.1f}MB  Peak: {peak/1e6:.1f}MB")
    print(f"After audio extraction: {mem_after_aud/1e6:.1f}MB")
    print(f"After energy analysis: {mem_after_energ/1e6:.1f}MB")

    top_stats = snap_after.compare_to(snap_before, "lineno")
    print("\nTop 5 memory growth:")
    for stat in top_stats[:5]:
        print(f"  {stat}")

    stats.dump_stats("profile_results.pstats")
    print("\n(cProfile saved to profile_results.pstats)")
    print("Run 'snakeviz profile_results.pstats' to visualize")

    if rounds:
        print(f"\n=== Detected {len(rounds)} Rounds ===")
        for i, r in enumerate(rounds[:5]):
            print(f"  Round {i+1}: {r.get('start_sec',0):.1f}s - {r.get('end_sec',0):.1f}s "
                  f"(score={r.get('score',0):.2f}, tail_by={r.get('tail_by','?')})")
        if len(rounds) > 5:
            print(f"  ... and {len(rounds)-5} more")


if __name__ == "__main__":
    main()
