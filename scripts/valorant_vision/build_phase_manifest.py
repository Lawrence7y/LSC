#!/usr/bin/env python3
"""为合并数据集 `datasets/valorant_phase` 生成带溯源（source_type/session_id）的清单。

为什么需要
----------
官方晋级门禁（`promote_model.promotion_failures`）要求被评估 split 的
`data_summary.source_session_count >= 3`，且 **broadcast 与 pov 各有 >=3 个独立来源会话**。
而该数据集的帧名是**前缀式**（`ann_broadcast_<会话>_<ts>.jpg` / `bc_broadcast_…` /
`ann_pov_…`），与两份既有清单的**后缀式**命名
（`<会话>_<ts>_ann_broadcast_<会话>_<ts>.jpg`）**basename 完全不重叠** ——
因此直接用既有清单评估会得到 `source_session_count = 0`（2026-09-10 实测复现）。

做法
----
已知会话名在 broadcast 与 pov 两套语料间**互不重叠**，故按"会话名子串匹配"即可
同时定出 `source_type` 与 `session_id`（长名优先匹配，避免子串误命中）；难例挖矿产物
用别名表兜底（如 `hardpov` → `hard_pov_mined`）。无法归属的帧**不臆测**，直接跳过并计数。

同时保证第三个 pov 来源会话 `fish_live` 已就位：其 111 帧原本只在
`datasets/valorant_phase_pov/test/`（该目录 gitignore、且该会话不在任何 train 中），
脚本按需**复制**进 `datasets/valorant_phase/val/{类}/`（复制而非移动：原处保持完整；
因 fish_live 不出现在任何训练集中，不会造成 train/val 泄漏）。

用法
----
    python scripts/valorant_vision/build_phase_manifest.py            # 生成 + 补齐 fish_live
    python scripts/valorant_vision/build_phase_manifest.py --check    # 只报告，不复制
    python scripts/valorant_vision/build_phase_manifest.py --out <path>
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import shutil
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_VISION_DIR = _REPO_ROOT / "scripts/valorant_vision"

CLASSES = ("non_game", "buy", "combat", "result", "replay")
COMBINED_DIR = _REPO_ROOT / "datasets/valorant_phase"
POV_DIR = _REPO_ROOT / "datasets/valorant_phase_pov"
DEFAULT_OUT = _VISION_DIR / "manifest_phase_combined.jsonl"
# 补齐第三个 pov 来源会话所用的会话名与来源分片
THIRD_POV_SESSION = "fish_live"


def known_sessions() -> dict[str, str]:
    """从两份权威清单读出 {会话名: source_type}（会话名跨语料不重叠）。"""
    sessions: dict[str, str] = {}
    for name, source in (
        ("manifest_broadcast.jsonl", "broadcast"),
        ("manifest_pov.jsonl", "pov"),
    ):
        path = _VISION_DIR / name
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            session = json.loads(line).get("session_id")
            if session:
                sessions[str(session)] = source
    # 难例挖矿产物的别名
    if "hard_pov_mined" in sessions:
        sessions.setdefault("hardpov", "pov")
    return sessions


def derive_provenance(filename: str, sessions: dict[str, str]) -> tuple[str | None, str | None]:
    """按会话名子串匹配推导 (source_type, session_id)；无法归属返回 (None, None)。"""
    aliases = {"hardpov": "hard_pov_mined"}
    for key in sorted(sessions, key=len, reverse=True):
        if key in filename:
            session = aliases.get(key, key)
            return sessions.get(session, sessions[key]), session
    return None, None


def timestamp_of(filename: str) -> float | None:
    """从帧名中取首个数字作为 timestamp_sec（>=1000 视为毫秒）。"""
    numbers = re.findall(r"_(\d+)(?=\.|_|$)", Path(filename).stem)
    if not numbers:
        return None
    value = int(numbers[0])
    return round(value / 1000.0, 3) if value >= 1000 else float(value)


def ensure_third_pov_session(*, dry_run: bool) -> tuple[int, list[dict]]:
    """把 fish_live 的帧复制进 val（若尚未存在），返回 (新增数, 清单行)。"""
    rows: list[dict] = []
    added = 0
    for cls in CLASSES:
        source_dir = POV_DIR / "test" / cls
        if not source_dir.is_dir():
            continue
        for src in sorted(source_dir.glob(f"*{THIRD_POV_SESSION}*.jpg")):
            dst = COMBINED_DIR / "val" / cls / src.name
            if not dst.exists():
                if dry_run:
                    added += 1
                else:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                    added += 1
            rows.append(
                {
                    "video_id": THIRD_POV_SESSION,
                    "video_path": None,
                    "frame_path": str(dst),
                    "timestamp_sec": timestamp_of(src.name),
                    "label": cls,
                    "split": "val",
                    "source_type": "pov",
                    "session_id": THIRD_POV_SESSION,
                    "notes": "third_pov_session_for_promotion_gate",
                }
            )
    return added, rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--data-dir", type=Path, default=COMBINED_DIR)
    parser.add_argument("--check", action="store_true", help="只报告，不复制帧、不写清单")
    args = parser.parse_args(argv)

    sessions = known_sessions()
    if not sessions:
        print("!! 未读到任何已知会话（缺少 manifest_broadcast/pov）", file=sys.stderr)
        return 2

    data_dir = args.data_dir.expanduser().resolve()
    rows: list[dict] = []
    unmatched: collections.Counter[str] = collections.Counter()
    for split in ("train", "val"):
        for cls in CLASSES:
            class_dir = data_dir / split / cls
            if not class_dir.is_dir():
                continue
            for path in sorted(class_dir.glob("*.jpg")):
                source, session = derive_provenance(path.name, sessions)
                if source is None:
                    unmatched[f"{split}/{cls}"] += 1
                    continue
                rows.append(
                    {
                        "video_id": session,
                        "video_path": None,
                        "frame_path": str(path),
                        "timestamp_sec": timestamp_of(path.name),
                        "label": cls,
                        "split": split,
                        "source_type": source,
                        "session_id": session,
                        "notes": "derived_from_filename",
                    }
                )

    added, pov_rows = ensure_third_pov_session(dry_run=args.check)
    # 去重：补齐的 fish_live 帧一旦落盘，就会被上面的"派生扫描"自然纳入，
    # 若再无脑 extend 会造成同帧双计（实测 +111）。
    covered = {row["frame_path"] for row in rows}
    pov_rows = [row for row in pov_rows if row["frame_path"] not in covered]
    rows.extend(pov_rows)

    per_source: dict[str, set[str]] = collections.defaultdict(set)
    for row in rows:
        if row["split"] == "val":
            per_source[row["source_type"]].add(row["session_id"])

    print(f"已知会话: {len(sessions)}（broadcast/pov 互不重叠）")
    print(f"清单条目: {len(rows)}"
          f"（其中第三 pov 会话 {THIRD_POV_SESSION} 共 {len(pov_rows)} 帧，本次新增 {added}）")
    if unmatched:
        print("无会话归属（跳过，不臆测）: " + ", ".join(
            f"{k}={v}" for k, v in sorted(unmatched.items())))
    print("\nval 来源会话（晋级要求各 >=3）:")
    ok = True
    for source in ("broadcast", "pov"):
        found = sorted(per_source.get(source, []))
        passed = len(found) >= 3
        ok = ok and passed
        print(f"  {source}: {len(found)} [{'PASS' if passed else 'FAIL'}] -> {found}")
    print("\n=> " + ("两侧会话数均达标" if ok else "仍有来源会话不足，需再补"))

    if args.check:
        print("\n（--check：未复制帧、未写清单）")
        return 0 if ok else 1

    out = args.out.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    print(f"\n已写出清单: {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
