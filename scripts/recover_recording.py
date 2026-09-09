"""Non-destructive recovery and optional post-hoc analysis for a recording.

The input is never overwritten.  A broken MP4 is remuxed to ``.repaired.mp4``
and a redacted JSON report is written next to the selected output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

# Windows packaged Python may launch this file with ``scripts/`` as the first
# import path.  Add the repository root so the same command works directly
# and as ``python -m scripts.recover_recording``.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lsc.recorder.capture import validate_recording
from lsc.utils.recording_repair import repair_recording


def resolve_output_path(input_path: str, output_path: str | None = None) -> Path:
    source = Path(input_path).expanduser().resolve()
    if output_path:
        target = Path(output_path).expanduser().resolve()
    else:
        target = source.with_name(f"{source.stem}.repaired{source.suffix}")
    if target == source:
        raise ValueError("output path must differ from input path")
    return target


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_report(
    *,
    input_path: Path,
    output_path: Path | None,
    input_valid: bool,
    input_error: str,
    output_valid: bool | None,
    output_error: str = "",
    analysis: Any = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": 1,
        "input_path": str(input_path),
        "input_exists": input_path.is_file(),
        "input_size_bytes": input_path.stat().st_size if input_path.is_file() else 0,
        "input_sha256": sha256_file(input_path) if input_path.is_file() else None,
        "input_valid": bool(input_valid),
        "input_error": input_error,
        "output_path": str(output_path) if output_path else None,
        "output_valid": output_valid,
        "output_error": output_error,
        "analysis": analysis,
    }
    if output_path and output_path.is_file():
        report["output_size_bytes"] = output_path.stat().st_size
        report["output_sha256"] = sha256_file(output_path)
    return report


def recover(input_path: str, *, output_path: str | None = None, analyze: bool = False, force: bool = False) -> dict[str, Any]:
    source = Path(input_path).expanduser().resolve()
    target = resolve_output_path(str(source), output_path)
    valid, error = validate_recording(str(source))
    repaired_path: Path | None = None
    output_valid: bool | None = valid
    output_error = ""
    analysis: Any = None

    if not valid or force:
        target.parent.mkdir(parents=True, exist_ok=True)
        repaired = repair_recording(str(source), str(target))
        if repaired:
            repaired_path = Path(repaired).resolve()
            output_valid, output_error = validate_recording(str(repaired_path))
        else:
            output_valid = False
            output_error = "FFmpeg remux failed"
    else:
        # Keep a report target but never copy or rewrite an already valid input.
        repaired_path = None

    if analyze and output_valid and repaired_path:
        from lsc.analyzer.valorant_plugin import ValorantAnalyzerPlugin

        analysis = ValorantAnalyzerPlugin().analyze_file(
            str(repaired_path),
            options={"valorant_profile": "auto"},
        )

    report_path = (repaired_path or source).with_suffix(".recovery.json")
    report = build_report(
        input_path=source,
        output_path=repaired_path,
        input_valid=valid,
        input_error=error,
        output_valid=output_valid,
        output_error=output_error,
        analysis=analysis,
    )
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["report_path"] = str(report_path)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Non-destructively repair an LSC recording")
    parser.add_argument("input", help="input recording path")
    parser.add_argument("--output", help="separate repaired output path")
    parser.add_argument("--analyze", action="store_true", help="run local post-hoc Valorant analysis on the repaired copy")
    parser.add_argument("--force", action="store_true", help="force remux even if basic container validation passes")
    args = parser.parse_args()
    report = recover(args.input, output_path=args.output, analyze=args.analyze, force=args.force)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("output_valid") else 2


if __name__ == "__main__":
    raise SystemExit(main())
