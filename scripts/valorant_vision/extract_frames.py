"""按训练清单 JSONL 抽取单帧 JPEG（FFmpeg，可取消/超时）。"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger(__name__)

LABELS = ("non_game", "buy", "combat", "result", "replay")
SPLITS = ("train", "val", "test")
DEFAULT_OUTPUT = Path.home() / "LSC" / "datasets" / "valorant_phase"


@dataclass(frozen=True)
class ManifestRow:
    video_id: str
    video_path: str
    timestamp_sec: float
    label: str
    split: str
    source_type: str
    session_id: str
    notes: str = ""

    @classmethod
    def from_dict(cls, raw: dict) -> ManifestRow:
        missing = [
            k
            for k in (
                "video_id",
                "video_path",
                "timestamp_sec",
                "label",
                "split",
                "source_type",
                "session_id",
            )
            if k not in raw
        ]
        if missing:
            raise ValueError(f"manifest 缺少字段: {missing}")
        label = str(raw["label"])
        split = str(raw["split"])
        if label not in LABELS:
            raise ValueError(f"非法 label: {label!r}")
        if split not in SPLITS:
            raise ValueError(f"非法 split: {split!r}")
        return cls(
            video_id=str(raw["video_id"]),
            video_path=str(raw["video_path"]),
            timestamp_sec=float(raw["timestamp_sec"]),
            label=label,
            split=split,
            source_type=str(raw["source_type"]),
            session_id=str(raw["session_id"]),
            notes=str(raw.get("notes", "")),
        )


def load_manifest(path: Path) -> list[ManifestRow]:
    rows: list[ManifestRow] = []
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                rows.append(ManifestRow.from_dict(json.loads(stripped)))
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                raise ValueError(f"{path}:{line_no}: {exc}") from exc
    return rows


def dedupe_rows(rows: list[ManifestRow]) -> list[ManifestRow]:
    """按 (video_id, timestamp_sec) 去重，保留首次出现。"""
    seen: set[tuple[str, float]] = set()
    out: list[ManifestRow] = []
    for row in rows:
        key = (row.video_id, round(row.timestamp_sec, 3))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def output_path_for(row: ManifestRow, output_dir: Path) -> Path:
    ts_ms = int(round(row.timestamp_sec * 1000))
    return (
        output_dir
        / row.split
        / row.label
        / f"{row.video_id}_{ts_ms}.jpg"
    )


def extract_single_frame(
    row: ManifestRow,
    dest: Path,
    *,
    ffmpeg: str,
    timeout_sec: float,
    cancel_event: list[bool] | None = None,
) -> None:
    """用 FFmpeg 在 timestamp 处抽一帧；cancel_event[0] 为 True 时终止子进程。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{row.timestamp_sec:.3f}",
        "-i",
        row.video_path,
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(dest),
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def _on_signal(_signum: int, _frame: object) -> None:
        if cancel_event is not None:
            cancel_event[0] = True
        proc.terminate()

    prev_int = signal.signal(signal.SIGINT, _on_signal)
    prev_term = signal.signal(signal.SIGTERM, _on_signal)
    deadline = time.monotonic() + timeout_sec if timeout_sec > 0 else None
    stderr = b""
    try:
        while proc.poll() is None:
            if cancel_event and cancel_event[0]:
                proc.terminate()
                proc.wait(timeout=5)
                raise KeyboardInterrupt("抽帧已取消")
            if deadline is not None and time.monotonic() > deadline:
                proc.kill()
                proc.wait(timeout=5)
                raise RuntimeError(f"FFmpeg 超时 ({timeout_sec}s): {dest.name}")
            time.sleep(0.1)
        stderr = proc.stderr.read() if proc.stderr else b""
        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"FFmpeg 失败 (rc={proc.returncode}): {err}")
        if not dest.is_file() or dest.stat().st_size == 0:
            raise RuntimeError(f"未生成有效帧: {dest}")
    finally:
        signal.signal(signal.SIGINT, prev_int)
        signal.signal(signal.SIGTERM, prev_term)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def run_extraction(
    rows: list[ManifestRow],
    output_dir: Path,
    *,
    ffmpeg: str,
    timeout_sec: float,
    dry_run: bool,
    skip_existing: bool,
) -> tuple[int, int, int]:
    """返回 (planned, extracted, skipped)。"""
    unique = dedupe_rows(rows)
    planned = len(unique)
    extracted = 0
    skipped = 0
    cancel = [False]

    for idx, row in enumerate(unique, start=1):
        if cancel[0]:
            break
        dest = output_path_for(row, output_dir)
        if skip_existing and dest.is_file():
            skipped += 1
            _log.info("[%d/%d] 跳过已存在 %s", idx, planned, dest)
            continue
        if dry_run:
            _log.info(
                "[%d/%d] dry-run %s @ %.3fs -> %s",
                idx,
                planned,
                row.video_path,
                row.timestamp_sec,
                dest,
            )
            continue
        _log.info(
            "[%d/%d] 抽取 %s @ %.3fs -> %s",
            idx,
            planned,
            row.video_path,
            row.timestamp_sec,
            dest,
        )
        extract_single_frame(
            row,
            dest,
            ffmpeg=ffmpeg,
            timeout_sec=timeout_sec,
            cancel_event=cancel,
        )
        extracted += 1
    return planned, extracted, skipped


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="读取 Valorant 训练清单 JSONL，按行抽取单帧 JPEG。",
    )
    p.add_argument(
        "manifest",
        type=Path,
        help="清单路径（JSONL，见 manifest_schema.md）",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"输出根目录（默认 {DEFAULT_OUTPUT}，建议在仓库外）",
    )
    p.add_argument("--ffmpeg", default="ffmpeg", help="FFmpeg 可执行文件路径")
    p.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="单帧 FFmpeg 超时秒数（0 表示不限制）",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印计划路径，不调用 FFmpeg",
    )
    p.add_argument(
        "--skip-existing",
        action="store_true",
        help="目标文件已存在时跳过",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    if not args.manifest.is_file():
        _log.error("清单不存在: %s", args.manifest)
        return 2
    try:
        rows = load_manifest(args.manifest)
    except ValueError as exc:
        _log.error("%s", exc)
        return 2
    if not rows:
        _log.warning("清单为空")
        return 0
    try:
        planned, extracted, skipped = run_extraction(
            rows,
            args.output_dir,
            ffmpeg=args.ffmpeg,
            timeout_sec=args.timeout,
            dry_run=args.dry_run,
            skip_existing=args.skip_existing,
        )
    except KeyboardInterrupt:
        _log.warning("用户取消")
        return 130
    except RuntimeError as exc:
        _log.error("%s", exc)
        return 1
    _log.info(
        "完成: 计划 %d 帧, 新抽取 %d, 跳过 %d",
        planned,
        extracted,
        skipped,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
