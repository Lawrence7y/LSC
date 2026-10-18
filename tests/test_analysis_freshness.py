"""分析结果新鲜度判据的行为测试（mtime + 体积 双证据）。

原判据只看 mtime：复制录像、搬目录、moov 修复后重写容器都会推进 mtime 而内容未变，
于是数小时的分析结果被静默判为过期并重跑（或直接丢失）。
"""
from __future__ import annotations

import os
import time

from persistence import (
    is_analysis_stale,
    load_analysis_results,
    save_analysis_results,
)


def _video(tmp_path, content: bytes = b"x" * 4096):
    path = tmp_path / "2026-09-18_10-00-00_至_11-00-00.mp4"
    path.write_bytes(content)
    return str(path)


def test_mtime_未变_不过期(tmp_path):
    video = _video(tmp_path)
    assert save_analysis_results(video, "room-1", "valorant_round", [{"start": 1.0}]) is True
    stored = load_analysis_results(video)
    assert stored is not None
    assert is_analysis_stale(video, stored) is False


def test_mtime_变了但体积未变_仍算有效(tmp_path):
    """这就是被修掉的缺陷：无害触碰不该丢弃分析结果。"""
    video = _video(tmp_path)
    save_analysis_results(video, "room-1", "valorant_round", [{"start": 1.0}])
    stored = load_analysis_results(video)
    assert stored is not None
    # 前进 2s，超过 0.5s 容差；体积不变
    future = time.time() + 2
    os.utime(video, (future, future))
    assert is_analysis_stale(video, stored) is False


def test_mtime_与体积都变了_判定过期(tmp_path):
    video = _video(tmp_path)
    save_analysis_results(video, "room-1", "valorant_round", [{"start": 1.0}])
    stored = load_analysis_results(video)
    assert stored is not None
    with open(video, "ab") as fh:
        fh.write(b"y" * 1024)
    future = time.time() + 2
    os.utime(video, (future, future))
    assert is_analysis_stale(video, stored) is True


def test_旧_sidecar_无体积字段时保持严格判据(tmp_path):
    """不能因为放宽判据而让老数据被误判为有效 —— 缺第二证据就按老的来。"""
    video = _video(tmp_path)
    save_analysis_results(video, "room-1", "valorant_round", [{"start": 1.0}])
    stored = load_analysis_results(video)
    assert stored is not None
    legacy = {k: v for k, v in stored.items() if k != "video_size"}
    future = time.time() + 2
    os.utime(video, (future, future))
    assert is_analysis_stale(video, legacy) is True


def test_录制文件不存在_判定过期(tmp_path):
    video = _video(tmp_path)
    save_analysis_results(video, "room-1", "valorant_round", [{"start": 1.0}])
    stored = load_analysis_results(video)
    assert stored is not None
    os.remove(video)
    assert is_analysis_stale(video, stored) is True


def test_sidecar_记录了体积(tmp_path):
    video = _video(tmp_path)
    save_analysis_results(video, "room-1", "valorant_round", [{"start": 1.0}])
    stored = load_analysis_results(video)
    assert stored is not None
    assert stored["video_size"] == os.path.getsize(video)
