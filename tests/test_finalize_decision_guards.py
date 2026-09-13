"""收尾判定块的防回归守卫（`delivery_complete` 自引用事故）。

现场（在途 C6 改动）：`room_handler._continuous_analysis_loop` 的收尾判定块里，
HEAD 原有的初始化行

    delivery_complete = not bool(_peek_refine_results(state))

被误删，只剩

    delivery_complete = delivery_complete and not bool(...)

自引用 ⇒ 该分支一旦进入就抛 `UnboundLocalError`（子类 NameError）。整块被包在
大循环的 try 里，表现是"收尾反复补扫/卡住"而不是崩溃，容易被忽略——`ruff F821`
是当时唯一抓到它的检查。因此这里放两道守卫：定向（初始化必须早于自引用）+ 全类
（该文件不得含任何未定义名）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ROOM_HANDLER = ROOT / "python-backend" / "handlers" / "room_handler.py"


def test_finalize_block_initializes_delivery_complete() -> None:
    """收尾判定的 `delivery_complete` 必须先初始化、再参与 `and` 自引用。"""
    src = ROOM_HANDLER.read_text(encoding="utf-8")
    anchor = "if not coverage_complete or pending_audit or not delivery_complete:"
    assert anchor in src, "收尾完成判定行不见了"
    index = src.index(anchor)
    # 先剥掉注释行：说明性注释里也含这句自引用文本，直接 find 会被骗
    window = chr(10).join(
        line
        for line in src[max(0, index - 3000): index].splitlines()
        if not line.lstrip().startswith("#")
    )
    init_at = window.find("delivery_complete = not bool(_peek_refine_results(state))")
    self_ref_at = window.find("delivery_complete = delivery_complete and")
    assert init_at >= 0, (
        "缺少 delivery_complete 初始化行（C6 回归：只剩自引用 → UnboundLocalError）"
    )
    assert self_ref_at >= 0, "自引用赋值不见了（逻辑已改写？请同步更新本守卫）"
    assert init_at < self_ref_at, "初始化必须出现在自引用之前"


def test_room_handler_has_no_undefined_names() -> None:
    """全类守卫：`room_handler.py` 不得含未定义名（ruff F821）。

    比"某一行没了"更通用：删掉任意一处初始化都会在这里红（成本 <1s）。
    """
    probe = subprocess.run(
        [sys.executable, "-m", "ruff", "--version"], capture_output=True, text=True
    )
    if probe.returncode != 0:
        pytest.skip("ruff 不可用，跳过 F821 守卫")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--select",
            "F821",
            "--no-cache",
            str(ROOM_HANDLER),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"room_handler 含未定义名（F821）:{proc.stdout}{proc.stderr}"
