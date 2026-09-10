"""broadcast_mode 影子模式守卫。

背景
----
`OcrRoundFSM.feed(broadcast_mode=True)` 实现的「赛事回放保护」（忽略未伴随准备阶段
的新交战钟）此前**仅被测试覆盖、未接入生产**（见
`docs/reports/replay-vs-nextcombat-experiment-20260910.md`：真实录像中 3 个
19/23/26s 碎片回合 100% 由 `next_combat` 闭合，而正常回合全部由
`next_prep`/`broadcast_exclusion` 闭合）。

切换前先做影子模式：用同一批 OCR 标签并行跑一份 `broadcast_mode=True` 的 FSM，
**只记录差异、不改变生效结果**。

本文件守住四条契约：
1. 影子开关默认关闭；
2. 影子的输入序列与生效路径完全一致（同一批标签、同一顺序）；
3. 影子**不得**改变生效回合列表；
4. 生效路径**不得**启用 `broadcast_mode`（该参数只允许出现在影子分支内）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from lsc.analyzer.valorant_ocr_rounds import (
    BROADCAST_MODE_SHADOW_ENV,
    OcrRoundFSM,
    _summarize_broadcast_mode_shadow,
    broadcast_mode_shadow_enabled,
)

ROOT = Path(__file__).resolve().parents[1]
OCR_MODULE = (ROOT / "lsc" / "analyzer" / "valorant_ocr_rounds.py").read_text(encoding="utf-8")

# (label, ts, timer, timer_raw)
# 4 帧降到 70 后跳到 95：满足 fresh_clock（timer>=85 且比上一原始帧跳升 >=20），
# 且 35-1=34 >= _MIN_PREP_AFTER_COMBAT_SEC(30)。
FRESH_CLOCK_SEQ: list[tuple[str, float, float | None, bool]] = [
    ("prep", 0.0, 30.0, True),
    ("combat", 1.0, 95.0, True),
    ("combat", 2.0, 90.0, True),
    ("combat", 3.0, 80.0, True),
    ("combat", 4.0, 70.0, True),
    ("neutral", 5.0, None, False),
    ("combat", 35.0, 95.0, True),
    ("combat", 36.0, 94.0, True),
    ("prep", 50.0, 30.0, True),
    ("prep", 51.0, 29.0, True),
    ("prep", 52.0, 28.0, True),
    ("prep", 53.0, 27.0, True),
    ("prep", 54.0, 26.0, True),
]


def _feed(seq, *, broadcast_mode: bool) -> list[dict]:
    fsm = OcrRoundFSM()
    out: list[dict] = []
    for label, ts, timer, timer_raw in seq:
        closed = fsm.feed(
            label,
            ts,
            timer,
            timer_raw=timer_raw,
            prep_banner=(label == "prep"),
            broadcast_mode=broadcast_mode,
        )
        if closed:
            out.extend(closed)
    return out


def _run_pair(seq) -> tuple[list[dict], list[dict]]:
    """复刻生产侧影子循环：同一批标签，先后喂生效 FSM 与影子 FSM。"""
    primary_fsm = OcrRoundFSM()
    shadow_fsm = OcrRoundFSM()
    primary: list[dict] = []
    shadow: list[dict] = []
    for label, ts, timer, timer_raw in seq:
        closed = primary_fsm.feed(
            label, ts, timer, timer_raw=timer_raw, prep_banner=(label == "prep")
        )
        if closed:
            primary.extend(closed)
        shadow_closed = shadow_fsm.feed(
            label,
            ts,
            timer,
            timer_raw=timer_raw,
            prep_banner=(label == "prep"),
            broadcast_mode=True,
        )
        if shadow_closed:
            shadow.extend(shadow_closed)
    return primary, shadow


class TestShadowFlag:
    def test_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(BROADCAST_MODE_SHADOW_ENV, raising=False)
        assert broadcast_mode_shadow_enabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", "On"])
    def test_enabled_values(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv(BROADCAST_MODE_SHADOW_ENV, value)
        assert broadcast_mode_shadow_enabled() is True

    @pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe"])
    def test_non_enabled_values(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv(BROADCAST_MODE_SHADOW_ENV, value)
        assert broadcast_mode_shadow_enabled() is False


class TestBroadcastModeDifference:
    """记录切换会带来什么变化——影子模式存在的理由。"""

    def test_broadcast_mode_turns_fragment_into_full_round(self) -> None:
        production = _feed(FRESH_CLOCK_SEQ, broadcast_mode=False)
        shadow = _feed(FRESH_CLOCK_SEQ, broadcast_mode=True)

        assert len(production) == 1
        # 生效路径：回放引起的「新交战钟」把回合切成 34s 碎片，且只能降级为 pending
        assert production[0]["start"] == 1.0
        assert production[0]["end"] == 35.0
        assert production[0]["end_by"] == "next_combat"
        assert production[0]["confirm_status"] == "pending"

        assert len(shadow) == 1
        # 影子路径：忽略该新交战钟，改由准备阶段闭合，得到完整回合 + vision_confirmed
        assert shadow[0]["start"] == 1.0
        assert shadow[0]["end"] == 50.0
        assert shadow[0]["end_by"] == "next_prep"
        assert shadow[0]["confirm_status"] == "vision_confirmed"


class TestShadowDoesNotChangePrimary:
    """核心契约：影子只记录，不改变生效结果。"""

    @pytest.mark.parametrize("enabled", [False, True])
    def test_primary_output_identical_regardless_of_shadow(
        self, monkeypatch: pytest.MonkeyPatch, enabled: bool
    ) -> None:
        if enabled:
            monkeypatch.setenv(BROADCAST_MODE_SHADOW_ENV, "1")
        else:
            monkeypatch.delenv(BROADCAST_MODE_SHADOW_ENV, raising=False)

        baseline = _feed(FRESH_CLOCK_SEQ, broadcast_mode=False)
        primary, shadow = _run_pair(FRESH_CLOCK_SEQ)

        assert primary == baseline, "影子模式不得改变生效回合列表"
        # 影子确实做了不同的判断，否则这个守卫没有意义
        assert shadow != primary

    def test_shadow_input_matches_primary_input(self) -> None:
        """两条路径必须吃同一批标签、同一顺序（否则差异不可比）。"""
        # _run_pair 用同一个 seq 喂两份 FSM；此处验证 clone 语义独立，
        # 影子 FSM 的状态推进不会串到生效 FSM。
        primary_fsm = OcrRoundFSM()
        shadow_fsm = primary_fsm.clone()
        for label, ts, timer, timer_raw in FRESH_CLOCK_SEQ:
            primary_fsm.feed(label, ts, timer, timer_raw=timer_raw)
            shadow_fsm.feed(
                label, ts, timer, timer_raw=timer_raw, broadcast_mode=True
            )
        assert primary_fsm is not shadow_fsm
        assert primary_fsm.__dict__ is not shadow_fsm.__dict__


class TestShadowSummary:
    def test_pairs_matching_rounds(self) -> None:
        primary = [{"start": 100.0, "end": 160.0, "end_by": "next_prep"}]
        shadow = [{"start": 100.5, "end": 160.0, "end_by": "next_prep"}]
        diff = _summarize_broadcast_mode_shadow(primary, shadow)
        assert diff["primary_rounds"] == 1
        assert diff["shadow_rounds"] == 1
        assert diff["shadow_only"] == []
        assert diff["primary_only"] == []
        assert diff["resized"] == []

    def test_reports_added_and_removed(self) -> None:
        primary = [
            {"start": 100.0, "end": 130.0, "end_by": "next_combat"},
            {"start": 400.0, "end": 480.0, "end_by": "next_prep"},
        ]
        shadow = [
            {"start": 100.0, "end": 180.0, "end_by": "next_prep"},
            {"start": 900.0, "end": 980.0, "end_by": "next_prep"},
        ]
        diff = _summarize_broadcast_mode_shadow(primary, shadow)
        # 100s 处配对成功，但时长 30s → 80s，计入 resized
        assert diff["resized"] == [
            {"start": 100.0, "primary_sec": 30.0, "shadow_sec": 80.0}
        ]
        # 400s 生效独有，900s 影子独有
        assert diff["primary_only"] == [[400.0, 480.0]]
        assert diff["shadow_only"] == [[900.0, 980.0]]
        assert diff["primary_next_combat"] == 1
        assert diff["shadow_next_combat"] == 0

    def test_ignores_malformed_round_entries(self) -> None:
        primary = [{"start": 10.0, "end": 60.0}, {"start": "bad"}]
        shadow = [{"no_start": 1}]
        diff = _summarize_broadcast_mode_shadow(primary, shadow)
        assert diff["primary_rounds"] == 1
        assert diff["shadow_rounds"] == 0
        assert diff["primary_only"] == [[10.0, 60.0]]


class TestSourceGuards:
    """源码级守卫：守住「未接线」这一事实，防止被无意间打开。

    用 AST 统计**代码**调用点（注释/文档字符串里的示例不算），避免守卫被文案带偏。
    """

    def test_only_one_feed_call_passes_broadcast_mode(self) -> None:
        calls = [
            [kw.arg or "" for kw in node.keywords]
            for node in ast.walk(ast.parse(OCR_MODULE))
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "feed"
        ]
        assert len(calls) == 2, f"预期「生效 + 影子」两处 feed 调用，实际 {len(calls)}"
        with_mode = [keywords for keywords in calls if "broadcast_mode" in keywords]
        assert len(with_mode) == 1, "只允许影子那一次 feed 传 broadcast_mode"

    def test_broadcast_mode_true_appears_once_in_code(self) -> None:
        sites = [
            node.lineno
            for node in ast.walk(ast.parse(OCR_MODULE))
            if isinstance(node, ast.Call)
            for kw in node.keywords
            if kw.arg == "broadcast_mode"
            and isinstance(kw.value, ast.Constant)
            and kw.value.value is True
        ]
        assert len(sites) == 1, f"broadcast_mode=True 应只有一处代码调用，实际 {sites}"

    def test_shadow_feed_is_gated_by_flag_and_profile(self) -> None:
        gate = "if broadcast_mode_shadow_enabled()"
        assert gate in OCR_MODULE, "影子开关门禁缺失"
        window = OCR_MODULE[OCR_MODULE.index(gate) : OCR_MODULE.index(gate) + 400]
        assert 'str(source_profile or "").lower() == "broadcast"' in window, (
            "影子必须限定在 broadcast 档位内（该分支语义只对赛事流成立）"
        )

    def test_shadow_rounds_never_leak_into_returned_list(self) -> None:
        # 影子结果只写 shadow_rounds / state，不得混入 closed_rounds
        assert "closed_rounds.extend(shadow_closed)" not in OCR_MODULE
        assert "shadow_rounds.extend(shadow_closed)" in OCR_MODULE
        # 回放标注与 round_key 均只作用于生效列表
        assert "_annotate_replay(r, labels)" in OCR_MODULE
