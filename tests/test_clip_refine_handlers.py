"""Task 2: pending 入列且停止自动导出 — 契约测试。

Spec 要求：
1. 所有闭合回合（含 pending）立即广播 clip_queued 入列
2. clip_queued 载荷包含 confirm_status 和 round_key
3. pending/ocr_confirmed 不入 _deferred_export_jobs 自动冲刷队列
4. 用户确认后仍不自动导出，仅手动
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


