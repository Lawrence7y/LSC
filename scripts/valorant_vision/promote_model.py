#!/usr/bin/env python3
"""Safely promote a Valorant model only after a machine-readable gate pass.

The command is intentionally fail-closed: a missing report, a false
``gates_passed`` value, an incomplete provenance summary, or a SHA mismatch
leaves the production directory untouched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any


MODEL_NAME = "valorant_phase_v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def promotion_failures(report: dict[str, Any], candidate_dir: Path) -> list[str]:
    failures: list[str] = []
    if report.get("gates_passed") is not True:
        failures.append("promotion report gates_passed=false")
    if report.get("evaluation_mode") != "broadcast_runtime":
        failures.append("evaluation_mode must be broadcast_runtime")
    if report.get("gate_failures"):
        failures.append("promotion report contains gate_failures")
    summary = report.get("data_summary")
    if not isinstance(summary, dict):
        failures.append("missing data_summary")
    else:
        support = summary.get("class_support") or {}
        missing = [name for name in ("non_game", "buy", "combat", "result", "replay") if not support.get(name)]
        if missing:
            failures.append(f"test support missing: {','.join(missing)}")
        if int(summary.get("source_session_count", 0) or 0) < 3:
            failures.append("fewer than three independently tracked source sessions")
        source_sessions = summary.get("source_sessions_by_type") or {}
        missing_sources = [
            source for source in ("broadcast", "pov")
            if int(source_sessions.get(source, 0) or 0) < 3
        ]
        if missing_sources:
            failures.append(
                "source session gate failed: " + ",".join(missing_sources)
            )
    candidate_meta = _read_json(candidate_dir / f"{MODEL_NAME}.json")
    onnx_path = candidate_dir / f"{MODEL_NAME}.onnx"
    if candidate_meta is None or not onnx_path.is_file():
        failures.append("candidate model or metadata is missing")
    else:
        required_meta = {
            "model_version", "class_names", "input_size", "color_order",
            "normalize_mean", "normalize_std", "threshold_version",
            "sha256", "dataset_version", "thresholds",
        }
        missing_meta = sorted(required_meta - set(candidate_meta))
        if missing_meta:
            failures.append("candidate metadata missing: " + ",".join(missing_meta))
        actual_sha = sha256_file(onnx_path)
        if actual_sha.lower() != str(candidate_meta.get("sha256") or "").lower():
            failures.append("candidate metadata sha256 mismatch")
        report_sha = str(report.get("model_sha256") or "")
        if report_sha and report_sha.lower() != actual_sha.lower():
            failures.append("promotion report model_sha256 mismatch")
    return failures


def promote_model(
    candidate_dir: Path,
    production_dir: Path,
    report_path: Path,
) -> dict[str, Any]:
    candidate_dir = candidate_dir.resolve()
    production_dir = production_dir.resolve()
    report_path = report_path.resolve()
    if candidate_dir == production_dir:
        return {"promoted": False, "errors": ["candidate and production directories must differ"]}
    report = _read_json(report_path)
    if report is None:
        return {"promoted": False, "errors": ["promotion report is missing or invalid"]}
    failures = promotion_failures(report, candidate_dir)
    if failures:
        return {"promoted": False, "errors": failures}
    source_model = candidate_dir / f"{MODEL_NAME}.onnx"
    source_meta = candidate_dir / f"{MODEL_NAME}.json"
    target_model = production_dir / source_model.name
    target_meta = production_dir / source_meta.name
    old_sha = sha256_file(target_model) if target_model.is_file() else None
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup_dir = production_dir.parent / "backups" / f"{MODEL_NAME}_{stamp}"
    temp_dir = production_dir / f".promotion-{os.getpid()}-{stamp}"
    model_replaced = False
    metadata_replaced = False
    backup_model = backup_dir / target_model.name
    backup_metadata = backup_dir / target_meta.name
    try:
        production_dir.mkdir(parents=True, exist_ok=True)
        temp_dir.mkdir()
        shutil.copy2(source_model, temp_dir / source_model.name)
        metadata = json.loads(source_meta.read_text(encoding="utf-8"))
        metadata.update({
            "promotion_state": "active",
            "evaluation_mode": report.get("evaluation_mode"),
            "evaluation_data_summary": report.get("data_summary"),
            "gate_results": {
                "gates_passed": True,
                "gate_failures": [],
            },
            "promotion_report_path": str(report_path),
            "rollback_model_sha": old_sha,
            "promoted_at": time.time(),
        })
        (temp_dir / source_meta.name).write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if target_model.is_file() or target_meta.is_file():
            backup_dir.mkdir(parents=True, exist_ok=True)
            if target_model.is_file():
                shutil.copy2(target_model, backup_dir / target_model.name)
            if target_meta.is_file():
                shutil.copy2(target_meta, backup_dir / target_meta.name)
        os.replace(temp_dir / source_model.name, target_model)
        model_replaced = True
        os.replace(temp_dir / source_meta.name, target_meta)
        metadata_replaced = True
    except (OSError, TypeError, ValueError) as exc:
        # Restore the previous pair if the second atomic rename fails; a model
        # without matching metadata is not a valid runtime contract.
        if model_replaced:
            if backup_model.is_file():
                shutil.copy2(backup_model, target_model)
            elif target_model.exists():
                target_model.unlink()
        if metadata_replaced:
            if backup_metadata.is_file():
                shutil.copy2(backup_metadata, target_meta)
            elif target_meta.exists():
                target_meta.unlink()
        shutil.rmtree(temp_dir, ignore_errors=True)
        return {"promoted": False, "errors": [f"promotion failed: {exc}"]}
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    return {
        "promoted": True,
        "model_sha256": sha256_file(target_model),
        "rollback_model_sha": old_sha,
        "backup_dir": str(backup_dir) if backup_dir.exists() else None,
        "production_dir": str(production_dir),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument("--production-dir", type=Path, default=Path("lsc/analyzer/models"))
    parser.add_argument("--promotion-report", type=Path, required=True)
    args = parser.parse_args(argv or sys.argv[1:])
    result = promote_model(args.candidate_dir, args.production_dir, args.promotion_report)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("promoted") else 2


if __name__ == "__main__":
    raise SystemExit(main())
