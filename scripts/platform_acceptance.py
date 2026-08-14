"""Run an authorized real-platform resolver/probe/lease acceptance check.

Examples::

    python scripts/platform_acceptance.py --url https://live.example/room
    python scripts/platform_acceptance.py --url https://live.example/room \
        --record-dir ./acceptance-output --duration 60 --preview
    python scripts/platform_acceptance.py --url https://live.example/a \
        --url https://live.example/b --parallel 2 --duration 7200 \
        --record-dir ./acceptance-output --preview

The command never prints cookies or signed URLs. Recording/preview execution is
opt-in and requires both ``--record-dir`` and ``--duration``, except for the
explicit ``--verification-suite`` staged lifecycle command.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lsc.config import load_config
from lsc.platforms.acceptance import (
    AcceptanceOptions,
    run_acceptance_batch,
    run_acceptance_loops,
    run_acceptance_verification_suite,
    write_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a redacted real-platform acceptance check")
    parser.add_argument(
        "--url",
        action="append",
        default=[],
        help="authorized live-room or direct-stream URL; repeat for a batch",
    )
    parser.add_argument(
        "--target",
        action="append",
        default=[],
        metavar="PLATFORM=URL",
        help="platform-qualified target; repeat to run a mixed-platform batch",
    )
    parser.add_argument("--room-id", default="acceptance")
    parser.add_argument(
        "--expected-platform",
        default="",
        help="optional platform id; fail closed if Resolver resolves a different platform",
    )
    parser.add_argument("--ffprobe", default="ffprobe", dest="ffprobe_path")
    parser.add_argument("--resolve-timeout", type=float, default=20.0)
    parser.add_argument("--probe-timeout", type=float, default=8.0)
    parser.add_argument("--quality", default="")
    parser.add_argument("--account", default="default")
    parser.add_argument("--record-dir", default="")
    parser.add_argument("--duration", type=float, default=0.0)
    parser.add_argument(
        "--max-no-progress",
        type=float,
        default=30.0,
        help="maximum seconds without upstream/recording/preview media progress (default: 30)",
    )
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--no-segmented", action="store_true")
    parser.add_argument("--segment-seconds", type=int, default=60)
    parser.add_argument("--proxy", default="", help="optional HTTP proxy URL")
    parser.add_argument(
        "--parallel",
        type=int,
        default=1,
        help="maximum concurrent room acceptance jobs (default: 1)",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=1,
        help="repeat one target as connection loops; 20 with 19 successes is the default release gate",
    )
    parser.add_argument(
        "--min-successes",
        type=int,
        default=0,
        help="minimum successful connection loops (default: ceil(iterations*0.95))",
    )
    parser.add_argument("--report", default="", help="optional JSON report path")
    parser.add_argument(
        "--verification-suite",
        action="store_true",
        help="run recording-only, preview-only and parallel lifecycle stages",
    )
    parser.add_argument("--recording-minutes", type=float, default=15.0)
    parser.add_argument("--preview-minutes", type=float, default=15.0)
    parser.add_argument("--parallel-minutes", type=float, default=30.0)
    parser.add_argument(
        "--operator-evidence",
        default="",
        help="JSON evidence file acknowledging network/restart external gates",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.url and not args.target:
        print("at least one --url or --target PLATFORM=URL is required", file=sys.stderr)
        return 2
    if args.url and args.target:
        print("--url and --target cannot be combined", file=sys.stderr)
        return 2
    target_pairs: list[tuple[str, str]] = []
    if args.target:
        for raw_target in args.target:
            platform, separator, url = str(raw_target or "").partition("=")
            platform = platform.strip().lower()
            url = url.strip()
            if not separator or not platform or not url:
                print("--target must use PLATFORM=URL", file=sys.stderr)
                return 2
            target_pairs.append((url, platform))
    else:
        target_pairs = [(str(url), str(args.expected_platform or "").strip().lower()) for url in args.url]
    urls = tuple(url for url, _platform in target_pairs)
    if args.target and args.expected_platform:
        print("--expected-platform cannot be combined with --target", file=sys.stderr)
        return 2
    if args.verification_suite:
        if len(urls) != 1:
            print("--verification-suite requires exactly one --url target", file=sys.stderr)
            return 2
        if not args.record_dir:
            print("--verification-suite requires --record-dir", file=sys.stderr)
            return 2
        if args.iterations != 1:
            print("--verification-suite cannot be combined with --iterations", file=sys.stderr)
            return 2
        if args.duration > 0:
            print("--verification-suite uses stage minute options; omit --duration", file=sys.stderr)
            return 2
        if min(args.recording_minutes, args.preview_minutes, args.parallel_minutes) <= 0:
            print("verification-suite stage minutes must be > 0", file=sys.stderr)
            return 2
    elif args.operator_evidence:
        print("--operator-evidence requires --verification-suite", file=sys.stderr)
        return 2
    if args.duration < 0:
        print("--duration must be >= 0", file=sys.stderr)
        return 2
    has_recording_lifecycle = bool(args.record_dir) or args.duration > 0
    if not args.verification_suite:
        if has_recording_lifecycle and (not args.record_dir or args.duration <= 0):
            print("--record-dir and --duration must be provided together", file=sys.stderr)
            return 2
        if args.preview and not has_recording_lifecycle:
            print("--preview requires --record-dir and --duration", file=sys.stderr)
            return 2
    if args.iterations > 1 and len(urls) != 1:
        print("--iterations requires exactly one --url target", file=sys.stderr)
        return 2
    if args.iterations > 1 and has_recording_lifecycle:
        print("--iterations is control-plane only; do not combine it with recording options", file=sys.stderr)
        return 2
    options = [
        AcceptanceOptions(
            source_url=url,
            room_id=args.room_id if len(urls) == 1 else f"{args.room_id}-{index + 1}",
            expected_platform=target_pairs[index][1],
            ffprobe_path=args.ffprobe_path,
            resolve_timeout_sec=args.resolve_timeout,
            probe_timeout_sec=args.probe_timeout,
            requested_quality=args.quality,
            account_ref=args.account,
            record_dir=args.record_dir,
            duration_sec=args.duration,
            max_no_progress_sec=max(1.0, args.max_no_progress),
            preview=args.preview,
            segmented=not args.no_segmented,
            segment_seconds=max(5, args.segment_seconds),
            network_context={"proxy_url": args.proxy} if args.proxy else {},
        )
        for index, url in enumerate(urls)
    ]
    config = load_config()
    if args.verification_suite:
        operator_evidence = None
        if args.operator_evidence:
            try:
                evidence_path = Path(args.operator_evidence).expanduser().resolve()
                loaded = json.loads(evidence_path.read_text(encoding="utf-8"))
                if not isinstance(loaded, dict):
                    raise ValueError("operator evidence must be a JSON object")
                operator_evidence = loaded
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                print(f"invalid --operator-evidence: {exc}", file=sys.stderr)
                return 2
        report = run_acceptance_verification_suite(
            options[0],
            recording_duration_sec=args.recording_minutes * 60.0,
            preview_duration_sec=args.preview_minutes * 60.0,
            parallel_duration_sec=args.parallel_minutes * 60.0,
            operator_evidence=operator_evidence,
        )
    elif args.iterations > 1:
        report = run_acceptance_loops(
            options[0],
            iterations=args.iterations,
            min_successes=(args.min_successes or None),
            max_concurrency=max(1, args.parallel),
        )
    else:
        report = run_acceptance_batch(
            options,
            max_concurrency=max(1, args.parallel),
            max_targets=max(1, int(getattr(config, "max_rooms", 12))),
            preview_limit=max(0, int(getattr(config, "max_concurrent_previews", 4))),
        )
    if args.report:
        write_report(report, args.report)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
