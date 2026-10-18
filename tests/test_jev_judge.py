"""Jev 高光判决接入层测试（lsc/analyzer/jev_judge.py + room_handler 的 shadow/live 接线）。

覆盖两件事：

A. 判决层——无 key、超时、429/401/5xx、畸形响应、正常返回、confidence 门控、成本公式，
   以及「绝不抛异常 / key 不落日志」两条硬契约；
B. 接线层——默认 off 不 import 判决模块、shadow fire-and-forget 且不改生效对象、
   live 门控只降级不丢弃，以及门控字段缺省时导出资格判定逐字节不变。

**本文件不发任何真实网络请求**：HTTP 出口全部被替换成桩，桩的返回值就是 SystemOne
的官方契约形状（取自 GET /openapi.json 的 components.schemas）。真机验证（真 Jev 报文）
不在这里——见 docs/reports/jev-integration.md，那里贴的是原始输出与真跑命令。
"""
from __future__ import annotations

import asyncio
import copy
import dataclasses
import inspect
import json
import logging
import sys
import time
from pathlib import Path

import handlers.room_handler as RH
import pytest

from lsc.analyzer import jev_judge as J

REAL_KEY = "ts_live_SUPERSECRET_KEY_VALUE_1234567890"
ROOM_HANDLER_SRC = (
    Path(__file__).resolve().parents[1] / "python-backend" / "handlers" / "room_handler.py"
).read_text(encoding="utf-8")

# 一份「正常返回」的 SystemOne 响应：字段与路径照抄官方契约。
GOOD_BODY = {
    "model": "jev-1.13.0",
    "answers": {
        "worth_clipping": {"type": "noul", "noul": 0.91},
        "excitement": {
            "type": "score",
            "score": 2.86,
            "confidence": 0.86,
            "legend": {"0": "平淡", "1": "略有起伏", "2": "明显精彩", "3": "极高强度"},
            "probabilities": {"0": 0.0, "1": 0.0, "2": 0.13, "3": 0.87},
        },
        "tag": {
            "type": "choice",
            "choice": "clutch",
            "confidence": 0.54,
            "probabilities": {"routine": 0.0, "clutch": 0.69, "ace": 0.31},
        },
    },
    "usage": {"input_tokens": 414, "output_tokens": 77},
}


def _outcome(payload=None, status=200, error=""):
    return J._HttpOutcome(payload, status, error)


def _body(**answer_overrides):
    body = copy.deepcopy(GOOD_BODY)
    body["answers"].update(answer_overrides)
    return body


class _Recorder:
    """按顺序吐出预设响应的 _http_post 替身，并记录每次调用的入参。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, payload, *, api_key, timeout):
        self.calls.append({"payload": payload, "api_key": api_key, "timeout": timeout})
        item = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(item, BaseException):
            raise item
        return item


def _raiser(exc):
    def _fn(*_args, **_kwargs):
        raise exc

    return _fn


def _verdict(p_clip, confidence, *, worth=True, source=J.SOURCE_JEV, error="", reason="ace"):
    return J.JudgeVerdict(
        worth_clipping=worth,
        p_clip=p_clip,
        excitement=2.0,
        reason=reason,
        confidence=confidence,
        model="jev-1.13.0",
        latency_ms=12,
        usage={"input_tokens": 414, "output_tokens": 77},
        source=source,
        error=error,
    )


def _round(n, **over):
    """一个**真正可入列**的 Valorant OCR 回合（判据与 room_handler 的常量同源）。"""
    data = {
        "round_key": f"round-{n:06d}",
        "boundary_source": "valorant_ocr_v1",
        "start_by": "ocr_combat",
        "end_by": "next_prep",
        "confirm_status": "vision_confirmed",
        "start": float(n * 100),
        "end": float(n * 100 + 40),
    }
    data.update(over)
    return data


class _FakeRoom:
    def __init__(self, platform="douyu"):
        self.platform = platform


@pytest.fixture
def key_env(monkeypatch):
    monkeypatch.setenv(J.JEV_API_KEY_ENV, REAL_KEY)
    return REAL_KEY


@pytest.fixture
def fast_backoff(monkeypatch):
    """退避不真睡：重试次数由调用计数断言，不由墙钟断言。"""
    monkeypatch.setattr(J, "_backoff", lambda *a, **k: True)


# ══════════════════════════ 冻结接口 ══════════════════════════


def test_frozen_dataclass_fields_and_slots():
    names = [f.name for f in dataclasses.fields(J.JudgeVerdict)]
    assert names == [
        "worth_clipping", "p_clip", "excitement", "reason", "confidence",
        "model", "latency_ms", "usage", "source", "error",
    ]
    assert hasattr(J.JudgeVerdict, "__slots__")
    with pytest.raises(AttributeError):
        _verdict(0.5, 0.5).not_a_field = 1


def test_frozen_judge_round_signature():
    sig = inspect.signature(J.judge_round)
    assert list(sig.parameters) == ["state", "timeout"]
    assert sig.parameters["timeout"].kind is inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["timeout"].default == 8.0


def test_payload_uses_map_questions_with_the_three_primitives():
    payload = J.build_payload({"game": "valorant"})
    assert set(payload) == {"model", "state", "questions"}
    assert payload["model"] == "jev-latest"
    assert isinstance(payload["state"], str)
    questions = payload["questions"]
    assert isinstance(questions, dict), "questions 必须是 map，不是数组"
    assert questions["worth_clipping"]["type"] == "noul"
    assert set(questions["worth_clipping"]["criteria"]) == {"true", "false"}
    assert questions["excitement"]["type"] == "score"
    assert questions["excitement"]["criteria"] == ["平淡", "略有起伏", "明显精彩", "极高强度"]
    assert questions["tag"]["type"] == "choice"
    assert "options" not in questions["tag"], "候选写在 criteria 里，没有 options 字段"
    assert set(questions["tag"]["criteria"]) == {
        "ace", "clutch", "comeback", "matchpoint", "routine",
    }


def test_build_payload_does_not_mutate_the_frozen_questions():
    payload = J.build_payload({"game": "valorant"})
    payload["questions"]["tag"]["criteria"]["ace"] = "MUTATED"
    assert J.QUESTIONS["tag"]["criteria"]["ace"] != "MUTATED"


# ══════════════════════════ 配置读取 ══════════════════════════


@pytest.mark.parametrize("settings", [None, {}, {"highlight_judge_mode": "LIVE!!"},
                                      {"highlight_judge_mode": 1},
                                      {"highlight_judge_mode": ""}])
def test_mode_defaults_to_off_for_unknown_or_broken_values(settings):
    assert J.judge_mode(settings) == J.MODE_OFF


@pytest.mark.parametrize("raw,expected", [
    ("off", "off"), ("shadow", "shadow"), ("live", "live"),
    (" Live ", "live"), ("SHADOW", "shadow"),
])
def test_mode_normalization(raw, expected):
    assert J.judge_mode({"highlight_judge_mode": raw}) == expected


@pytest.mark.parametrize("settings,expected", [
    (None, 0.6), ({}, 0.6), ({"highlight_judge_min_confidence": "abc"}, 0.6),
    ({"highlight_judge_min_confidence": 5}, 1.0),
    ({"highlight_judge_min_confidence": -3}, 0.0),
    ({"highlight_judge_min_confidence": 0.45}, 0.45),
    ({"highlight_judge_min_confidence": float("nan")}, 0.6),
])
def test_min_confidence_default_and_clamp(settings, expected):
    assert J.judge_min_confidence(settings) == pytest.approx(expected)


# ══════════════════════════ state 组装（两条实测修正） ══════════════════════════


def test_state_attributes_eliminations_per_team():
    """实测：写成 "ours 1, enemy 5" 时模型把敌方的 5 杀算成我方 multikill。"""
    text = J.build_state_text({"game": "valorant", "kills_self": 1, "kills_enemy": 5})
    assert "OUR team's players took 1 elimination(s)" in text
    assert "The OPPONENT team's players took 5 elimination(s)" in text
    assert "strict attribution" in text
    assert "ours 1, enemy 5" not in text


def test_state_states_narrow_time_margin_as_explicit_fact():
    """实测：只给 round_time_left_sec=0（不是 0.1）时，0.1 秒拆包被按普通拆包处理。"""
    text = J.build_state_text({"round_time_left_sec": 0.1})
    assert "0.1 seconds" in text
    assert "only 0.1 seconds remained" in text
    assert "highly dramatic" in text


def test_state_states_clutch_and_ace_as_explicit_facts():
    text = J.build_state_text({"clutch_1vN": 3, "ace": True})
    assert "1-versus-3 clutch" in text
    assert "all our other players were already eliminated" in text
    assert "scored an ACE" in text


def test_state_declares_missing_keys_instead_of_guessing():
    text = J.build_state_text({"game": "valorant"})
    assert "NOT AVAILABLE IN THIS DATA" in text
    assert "kills_self" in text
    assert "elimination(s)" not in text
    # 没给的戏剧性要素不得出现在 DECISIVE FACTS 里（clutch_1vN 只是 UNKNOWN 清单里的键名）
    assert "1-versus-" not in text
    assert "DECISIVE FACTS" not in text


def test_state_rejects_unknown_sentinels():
    text = J.build_state_text({"kills_self": "unknown", "spike_planted": None,
                               "round_no": "n/a", "ocr_text": "  "})
    assert "elimination(s)" not in text
    assert "Round number" not in text
    assert "RAW ON-SCREEN TEXT" not in text


def test_state_dedupes_mapping_keys_from_text_fields():
    """room_id/platform/phase 是文本字段，不得被当成数值渲染。"""
    text = J.build_state_text({"room_id": "r1", "platform": "douyu", "phase": "combat"})
    assert "Broadcast platform: douyu" in text
    assert "phase of the round: combat" in text
    assert "r1" not in text, "room_id 与判决无关，不该进 state 文本"


# ══════════════════════════ 判决：失败模式 ══════════════════════════


def test_no_key_returns_disabled_without_any_request(monkeypatch):
    monkeypatch.delenv(J.JEV_API_KEY_ENV, raising=False)
    called = []
    monkeypatch.setattr(J, "_http_post", lambda *a, **k: called.append(1))
    verdict = J.judge_round({"game": "valorant"})
    assert verdict.source == J.SOURCE_DISABLED
    assert verdict.error
    assert verdict.worth_clipping is False
    assert called == [], "无 key 时不得发出任何请求"


def test_normal_response_maps_every_frozen_field(key_env, monkeypatch):
    rec = _Recorder([_outcome(GOOD_BODY)])
    monkeypatch.setattr(J, "_http_post", rec)
    verdict = J.judge_round({"game": "valorant"}, timeout=3.0)

    assert (verdict.source, verdict.error) == (J.SOURCE_JEV, "")
    assert verdict.p_clip == pytest.approx(0.91)
    assert verdict.excitement == pytest.approx(2.86)
    # confidence 取 score 头的 0.86，**不是** noul 的 0.91——noul 响应没有 confidence 字段
    assert verdict.confidence == pytest.approx(0.86)
    assert verdict.reason == "clutch"
    assert verdict.model == "jev-1.13.0"
    assert verdict.usage == {"input_tokens": 414, "output_tokens": 77}
    assert verdict.worth_clipping is True
    assert rec.calls[0]["api_key"] == REAL_KEY
    assert rec.calls[0]["payload"]["model"] == "jev-latest"


def test_p_clip_below_half_is_not_worth_clipping(key_env, monkeypatch):
    monkeypatch.setattr(J, "_http_post", _Recorder([
        _outcome(_body(worth_clipping={"type": "noul", "noul": 0.49})),
    ]))
    verdict = J.judge_round({"game": "valorant"})
    assert verdict.worth_clipping is False
    assert verdict.p_clip == pytest.approx(0.49)


def test_described_timeout_is_reported_and_not_retried(key_env, monkeypatch, fast_backoff):
    rec = _Recorder([_outcome(None, None, "timeout")])
    monkeypatch.setattr(J, "_http_post", rec)
    verdict = J.judge_round({"game": "valorant"})
    assert verdict.error == "timeout"
    assert verdict.worth_clipping is False
    assert len(rec.calls) == 1


@pytest.mark.parametrize("exc", [
    TimeoutError("timed out"), OSError("network down"), ValueError("bad value"),
    RuntimeError("thread pool broke"),
])
def test_judge_round_never_raises(key_env, monkeypatch, exc):
    monkeypatch.setattr(J, "_http_post", _raiser(exc))
    verdict = J.judge_round({"game": "valorant"})
    assert isinstance(verdict, J.JudgeVerdict)
    assert verdict.worth_clipping is False
    assert verdict.error


def test_429_is_retried_up_to_the_cap_then_reported(key_env, monkeypatch, fast_backoff):
    rec = _Recorder([_outcome(None, 429, "http 429: rate limited")])
    monkeypatch.setattr(J, "_http_post", rec)
    verdict = J.judge_round({"game": "valorant"})
    assert len(rec.calls) == 1 + J._MAX_RETRIES
    assert verdict.error.startswith("http 429")
    assert verdict.source == J.SOURCE_ERROR


def test_429_then_success_returns_a_real_verdict(key_env, monkeypatch, fast_backoff):
    rec = _Recorder([_outcome(None, 429, "http 429"), _outcome(GOOD_BODY)])
    monkeypatch.setattr(J, "_http_post", rec)
    verdict = J.judge_round({"game": "valorant"})
    assert len(rec.calls) == 2
    assert (verdict.source, verdict.error) == (J.SOURCE_JEV, "")
    assert verdict.p_clip == pytest.approx(0.91)


@pytest.mark.parametrize("status", [500, 502, 503, 504, 529])
def test_retryable_5xx_is_retried(key_env, monkeypatch, fast_backoff, status):
    rec = _Recorder([_outcome(None, status, f"http {status}")])
    monkeypatch.setattr(J, "_http_post", rec)
    verdict = J.judge_round({"game": "valorant"})
    assert len(rec.calls) == 1 + J._MAX_RETRIES
    assert verdict.error.startswith(f"http {status}")


@pytest.mark.parametrize("status", [400, 404, 418, 422])
def test_non_retryable_status_is_not_retried(key_env, monkeypatch, fast_backoff, status):
    rec = _Recorder([_outcome(None, status, f"http {status}")])
    monkeypatch.setattr(J, "_http_post", rec)
    J.judge_round({"game": "valorant"})
    assert len(rec.calls) == 1


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failure_is_a_config_error_and_never_retried(key_env, monkeypatch,
                                                          fast_backoff, status):
    """实测语义：401 是配置错误，重试没有意义。"""
    rec = _Recorder([_outcome(None, status, f"http {status}: bad key")])
    monkeypatch.setattr(J, "_http_post", rec)
    verdict = J.judge_round({"game": "valorant"})
    assert len(rec.calls) == 1
    assert verdict.source == J.SOURCE_DISABLED
    assert verdict.error


def test_retry_gives_up_when_the_budget_cannot_fit_the_backoff(key_env, monkeypatch):
    """不补丁 _backoff：预算小到放不下第一次退避时必须只发一次。"""
    rec = _Recorder([_outcome(None, 503, "http 503")])
    monkeypatch.setattr(J, "_http_post", rec)
    verdict = J.judge_round({"game": "valorant"}, timeout=0.01)
    assert len(rec.calls) == 1
    assert verdict.error.startswith("http 503")


def test_non_positive_timeout_falls_back_to_the_documented_budget(key_env, monkeypatch):
    rec = _Recorder([_outcome(GOOD_BODY)])
    monkeypatch.setattr(J, "_http_post", rec)
    assert J.judge_round({"game": "valorant"}, timeout=0).source == J.SOURCE_JEV
    assert rec.calls[0]["timeout"] == pytest.approx(J.DEFAULT_TIMEOUT_SEC, abs=1.0)


def test_backoff_respects_the_remaining_budget(monkeypatch):
    monkeypatch.setattr(J.time, "sleep", lambda _s: None)
    assert J._backoff(time.monotonic() + 0.01, 1) is False
    assert J._backoff(time.monotonic() + 30.0, 1) is True


@pytest.mark.parametrize("body", [
    None,
    {},
    {"answers": []},
    {"answers": {}},
    {"answers": {"worth_clipping": {"type": "noul", "noul": "abc"}}},
    {"answers": {"worth_clipping": {"type": "noul", "noul": 1.5}}},
    {"answers": {"worth_clipping": {"type": "noul", "noul": -0.2}}},
    {"answers": {"worth_clipping": {"noul": 0.7}}},
    {"answers": {"worth_clipping": {"noul": 0.7}, "excitement": {}}},
    {"answers": {"worth_clipping": {"noul": 0.7}, "excitement": {"score": 2.0}}},
])
def test_malformed_responses_fail_closed_without_raising(key_env, monkeypatch, body):
    monkeypatch.setattr(J, "_http_post", _Recorder([_outcome(body)]))
    verdict = J.judge_round({"game": "valorant"})
    assert verdict.source == J.SOURCE_ERROR
    assert verdict.error
    assert verdict.worth_clipping is False
    assert verdict.p_clip == 0.0


def test_non_json_body_is_reported_as_malformed(key_env, monkeypatch):
    """_http_post 自己负责把非 JSON 体折成 malformed（这里替身直接给错误串）。"""
    rec = _Recorder([_outcome(None, 200, "malformed response: JSONDecodeError")])
    monkeypatch.setattr(J, "_http_post", rec)
    verdict = J.judge_round({"game": "valorant"})
    assert verdict.error.startswith("malformed response")
    assert verdict.worth_clipping is False


def test_missing_tag_degrades_to_unknown_without_failing(key_env, monkeypatch):
    body = copy.deepcopy(GOOD_BODY)
    del body["answers"]["tag"]
    monkeypatch.setattr(J, "_http_post", _Recorder([_outcome(body)]))
    verdict = J.judge_round({"game": "valorant"})
    assert verdict.reason == "unknown"
    assert verdict.source == J.SOURCE_JEV
    assert verdict.p_clip == pytest.approx(0.91)


def test_missing_usage_degrades_to_zero_tokens(key_env, monkeypatch):
    body = copy.deepcopy(GOOD_BODY)
    del body["usage"]
    monkeypatch.setattr(J, "_http_post", _Recorder([_outcome(body)]))
    assert J.judge_round({"game": "valorant"}).usage == {
        "input_tokens": 0, "output_tokens": 0,
    }


# ══════════════════════════ key 不得泄漏 ══════════════════════════


def test_api_key_never_appears_in_logs_or_error_text(key_env, monkeypatch, caplog):
    """最坏情况：上游把 Authorization 原样回吐到错误体里，出口必须自己兜住。"""
    echo = 'http 500: {"echo": "Bearer ' + REAL_KEY + '"}'
    rec = _Recorder([_outcome(None, 500, echo)])
    monkeypatch.setattr(J, "_http_post", rec)
    monkeypatch.setattr(J, "_backoff", lambda *a, **k: True)
    with caplog.at_level(logging.DEBUG, logger="lsc.analyzer.jev_judge"):
        verdict = J.judge_round({"game": "valorant"})

    assert REAL_KEY not in verdict.error
    assert REAL_KEY not in json.dumps(verdict.as_log_record(), ensure_ascii=False)
    # 日志的 args 也要查：格式化前的原始入参同样可能带着 key
    for record in caplog.records:
        assert REAL_KEY not in str(record.args)
        assert REAL_KEY not in record.getMessage()


def test_safe_redacts_a_bare_key_literal(monkeypatch):
    monkeypatch.setenv(J.JEV_API_KEY_ENV, REAL_KEY)
    assert REAL_KEY not in J._safe("boom " + REAL_KEY + " boom")


# ══════════════════════════ 门控 ══════════════════════════


def test_gate_uses_p_clip_only():
    assert J.gate_out_reason(_verdict(0.90, 0.90), 0.6) == ""
    assert "jev_p_clip" in J.gate_out_reason(_verdict(0.50, 0.90), 0.6)
    # 精彩度头的 confidence 不参与门控：conf 低不得撤销自动导出资格（见 gate_out_reason 文档）
    assert J.gate_out_reason(_verdict(0.90, 0.50), 0.6) == ""
    assert J.gate_out_reason(_verdict(0.90, 0.00), 0.6) == ""


def test_gate_does_not_kill_the_two_measured_high_p_clip_cases():
    """回归守卫：真 Jev 实测中被旧规则（conf 门控）误杀的两个高价值局面。"""
    matchpoint = _verdict(0.87, 0.00)   # p_clip 高、精彩度分档很平
    clutch = _verdict(0.92, 0.58)       # p_clip 高、confidence 略低于 0.60
    assert J.gate_out_reason(matchpoint, 0.6) == ""
    assert J.gate_out_reason(clutch, 0.6) == ""


def test_gate_threshold_is_inclusive():
    assert J.gate_out_reason(_verdict(0.6, 0.6), 0.6) == ""
    assert J.gate_out_reason(_verdict(0.59, 0.99), 0.6) != ""


def test_gate_fails_open_when_the_verdict_is_unusable(monkeypatch):
    unusable = _verdict(0.0, 0.0, worth=False, source=J.SOURCE_DISABLED,
                        error="missing TYPESAFE_API_KEY")
    assert J.gate_out_reason(unusable, 0.6) == "", "Jev 不可用不得改变导出资格"
    monkeypatch.setattr(J, "LIVE_GATE_FAIL_OPEN", False)
    assert "jev_unavailable" in J.gate_out_reason(unusable, 0.6)


def test_disagreement_ignores_unusable_verdicts():
    broken = _verdict(0.0, 0.0, worth=False, source=J.SOURCE_ERROR, error="timeout")
    assert J.disagrees_with_original(broken, original_worth_clipping=True) is False
    assert J.disagrees_with_original(_verdict(0.9, 0.9, worth=True),
                                     original_worth_clipping=False) is True
    assert J.disagrees_with_original(_verdict(0.9, 0.9, worth=True),
                                     original_worth_clipping=True) is False


def test_verdict_is_usable_requires_both_source_and_no_error():
    assert J.verdict_is_usable(_verdict(0.9, 0.9)) is True
    assert J.verdict_is_usable(_verdict(0.9, 0.9, source=J.SOURCE_DISABLED)) is False
    assert J.verdict_is_usable(_verdict(0.9, 0.9, error="boom")) is False


def test_reason_text_renders_a_gloss():
    assert "ACE" in J.reason_text(_verdict(0.9, 0.9, reason="ace"))
    assert J.reason_text(_verdict(0.9, 0.9, reason="unknown")) == "unknown"
    assert J.reason_text(_verdict(0.9, 0.9, reason="")) == "unknown"
    assert J.reason_text(_verdict(0.9, 0.9, reason="brand_new_tag")) == "brand_new_tag"


# ══════════════════════════ 成本口径 ══════════════════════════


def test_cost_formula_matches_the_documented_rate():
    # 单次 ≈ 655 input token ≈ $0.00002751（文档按 $0.0000275 量级记）
    assert J.estimate_cost_usd({"input_tokens": 655}) == pytest.approx(0.0000275, rel=1e-3)
    assert J.estimate_cost_usd({"input_tokens": 2_500_000}) == pytest.approx(0.105, rel=1e-9)
    assert J.estimate_cost_usd({}) == 0.0
    assert J.estimate_cost_usd(None) == 0.0


# ══════════════════════════ shadow 日志 ══════════════════════════


def test_shadow_log_path_honors_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LSC_DATA_DIR", str(tmp_path))
    assert J.shadow_log_path() == tmp_path / "data" / J.SHADOW_LOG_FILENAME


def test_append_shadow_record_writes_one_jsonl_line_per_call(tmp_path, monkeypatch):
    monkeypatch.setenv("LSC_DATA_DIR", str(tmp_path))
    J.append_shadow_record({"a": 1})
    J.append_shadow_record({"b": "中"})
    lines = J.shadow_log_path().read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == [{"a": 1}, {"b": "中"}]


def test_append_shadow_record_swallows_write_failures(tmp_path, monkeypatch):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(J, "shadow_log_path", lambda: blocker / "x.jsonl")
    J.append_shadow_record({"a": 1})  # 不得抛


# ══════════════════════════ 接线：默认 off 零行为变化 ══════════════════════════


def test_off_mode_does_not_import_the_judge_module(monkeypatch):
    """默认路径的关键契约：off 时连模块都不加载（零启动延迟）。"""
    monkeypatch.setattr(RH, "load_settings", lambda: {"highlight_judge_mode": "off"})
    monkeypatch.delitem(sys.modules, "lsc.analyzer.jev_judge", raising=False)
    assert RH._jev_mode_and_threshold() is None
    assert "lsc.analyzer.jev_judge" not in sys.modules


@pytest.mark.parametrize("settings", [
    {}, {"highlight_judge_mode": "off"}, {"highlight_judge_mode": True},
    {"highlight_judge_mode": "Live!"}, {"highlight_judge_mode": "liv"},
])
def test_missing_or_broken_mode_setting_is_treated_as_off(monkeypatch, settings):
    monkeypatch.setattr(RH, "load_settings", lambda: settings)
    assert RH._jev_mode_and_threshold() is None


def test_broken_settings_read_is_treated_as_off(monkeypatch):
    def _boom():
        raise RuntimeError("settings unreadable")

    monkeypatch.setattr(RH, "load_settings", _boom)
    assert RH._jev_mode_and_threshold() is None


@pytest.mark.parametrize("mode,expected", [("shadow", "shadow"), ("live", "live")])
def test_enabled_modes_return_mode_and_threshold(monkeypatch, mode, expected):
    monkeypatch.setattr(RH, "load_settings", lambda: {
        "highlight_judge_mode": mode, "highlight_judge_min_confidence": 0.42,
    })
    assert RH._jev_mode_and_threshold() == (expected, 0.42)


def test_export_eligibility_is_unchanged_without_the_gate_field():
    round_data = _round(7)
    assert RH._is_auto_exportable_valorant_round(round_data) is True
    assert RH._is_listable_ocr_round(round_data) is True


def test_gate_field_revokes_auto_export_but_keeps_the_round_listable():
    round_data = _round(7)
    round_data[RH._JEV_GATED_REASON_FIELD] = "jev_p_clip 0.100 < min_confidence 0.60"
    assert RH._is_auto_exportable_valorant_round(round_data) is False
    assert RH._is_listable_ocr_round(round_data) is True, "门控掉 ≠ 丢弃：仍待人工确认"


def test_shadow_spawn_is_a_noop_unless_mode_is_shadow(monkeypatch):
    spawned = []
    monkeypatch.setattr(RH, "_jev_spawn", lambda coro: spawned.append(coro) or coro.close())
    RH._jev_spawn_shadow("off", "room-1", _FakeRoom(), [_round(1)], None)
    RH._jev_spawn_shadow("live", "room-1", _FakeRoom(), [_round(1)], None)
    RH._jev_spawn_shadow("shadow", "room-1", _FakeRoom(), [], None)
    assert spawned == []


def test_shadow_spawn_fires_once_for_shadow_mode(monkeypatch):
    spawned = []
    monkeypatch.setattr(RH, "_jev_spawn", lambda coro: spawned.append(coro) or coro.close())
    RH._jev_spawn_shadow("shadow", "room-1", _FakeRoom(), [_round(1)], None)
    assert len(spawned) == 1


def test_shadow_hook_is_fire_and_forget_and_gated_in_source():
    """源码级守卫：影子不得被 await（await 会把判决延迟压进分析循环）。"""
    assert "await _jev_spawn_shadow" not in ROOM_HANDLER_SRC
    assert "await _jev_shadow_worker" not in ROOM_HANDLER_SRC
    gate = "if _jev_cfg and _jev_cfg[0] == 'shadow' and listable_hl:"
    assert gate in ROOM_HANDLER_SRC


def test_live_gate_is_gated_by_the_live_flag_in_source():
    idx = ROOM_HANDLER_SRC.index("await _jev_apply_live_gate")
    window = ROOM_HANDLER_SRC[max(0, idx - 400):idx]
    assert "if _jev_cfg and _jev_cfg[0] == 'live':" in window


def test_shadow_worker_records_jsonl_and_leaves_the_round_untouched(tmp_path, monkeypatch):
    monkeypatch.setenv("LSC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(J.JEV_API_KEY_ENV, REAL_KEY)
    monkeypatch.setattr(J, "_http_post", _Recorder([_outcome(GOOD_BODY)]))

    round_data = _round(3)
    snapshot = copy.deepcopy(round_data)
    asyncio.run(RH._jev_shadow_worker("room-1", _FakeRoom(), [round_data], {}))

    assert round_data == snapshot, "shadow 不得改动生效回合对象"
    lines = J.shadow_log_path().read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["kind"] == "shadow"
    assert record["room_id"] == "room-1"
    assert record["round_key"] == "round-000003"
    assert record["p_clip"] == pytest.approx(0.91)
    assert record["original_worth_clipping"] is True
    assert record["agree"] is True
    assert record["model"] == "jev-1.13.0"


def test_shadow_worker_warns_on_disagreement(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("LSC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(J.JEV_API_KEY_ENV, REAL_KEY)
    monkeypatch.setattr(J, "_http_post", _Recorder([
        _outcome(_body(worth_clipping={"type": "noul", "noul": 0.05})),
    ]))
    with caplog.at_level(logging.WARNING, logger="handlers.room_handler"):
        asyncio.run(RH._jev_shadow_worker("room-1", _FakeRoom(), [_round(4)], {}))

    assert any("Jev shadow 与原决策不一致" in r.getMessage() for r in caplog.records)
    record = json.loads(
        J.shadow_log_path().read_text(encoding="utf-8").splitlines()[0]
    )
    assert record["agree"] is False
    assert record["original_worth_clipping"] is True
    assert record["worth_clipping"] is False


def test_shadow_worker_survives_a_failing_judge(tmp_path, monkeypatch):
    monkeypatch.setenv("LSC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(J.JEV_API_KEY_ENV, REAL_KEY)
    monkeypatch.setattr(J, "_http_post", _raiser(RuntimeError("boom")))
    asyncio.run(RH._jev_shadow_worker("room-1", _FakeRoom(), [_round(5)], {}))
    # 判决层吞掉异常并写出 error 记录，而不是让后台任务炸掉
    record = json.loads(J.shadow_log_path().read_text(encoding="utf-8").splitlines()[0])
    assert record["source"] == J.SOURCE_ERROR
    assert record["error"]


# ══════════════════════════ 接线：live 门控 ══════════════════════════


def test_live_gate_is_inert_when_mode_is_not_live(monkeypatch):
    async def _boom(_state_dict):
        raise AssertionError("非 live 模式不得调用判决")

    monkeypatch.setattr(RH, "_jev_judge_one", _boom)
    rounds = [_round(1)]
    kept, gated = asyncio.run(
        RH._jev_apply_live_gate("shadow", 0.6, "room-1", _FakeRoom(), rounds, {})
    )
    assert kept == rounds
    assert gated == []


def test_live_gate_downgrades_only_the_failing_round(monkeypatch):
    # gather 保序：verdicts[i] 对应 batch[i]，与执行顺序无关，因此用迭代器即可确定
    replies = iter([_verdict(0.10, 0.95), _verdict(0.90, 0.95)])

    async def _fake(_state_dict):
        return next(replies)

    monkeypatch.setattr(RH, "_jev_judge_one", _fake)
    rounds = [_round(1), _round(2)]
    kept, gated = asyncio.run(
        RH._jev_apply_live_gate("live", 0.6, "room-1", _FakeRoom(), rounds, {})
    )
    assert [r["round_key"] for r in kept] == ["round-000002"]
    assert [r["round_key"] for r in gated] == ["round-000001"]

    downgraded = gated[0]
    assert "jev_p_clip" in downgraded[RH._JEV_GATED_REASON_FIELD]
    assert downgraded["jev_judge"]["p_clip"] == pytest.approx(0.10)
    assert downgraded["jev_judge_reason_text"]
    assert RH._is_auto_exportable_valorant_round(downgraded) is False
    assert RH._is_listable_ocr_round(downgraded) is True


def test_live_gate_leaves_rounds_alone_when_the_verdict_is_unusable(monkeypatch):
    async def _fake(_state_dict):
        return _verdict(0.0, 0.0, worth=False, source=J.SOURCE_DISABLED, error="no key")

    monkeypatch.setattr(RH, "_jev_judge_one", _fake)
    rounds = [_round(1), _round(2)]
    kept, gated = asyncio.run(
        RH._jev_apply_live_gate("live", 0.6, "room-1", _FakeRoom(), rounds, {})
    )
    assert kept == rounds and gated == []


def test_live_gate_fails_open_when_the_judge_raises(monkeypatch):
    async def _boom(_state_dict):
        raise RuntimeError("executor died")

    monkeypatch.setattr(RH, "_jev_judge_one", _boom)
    rounds = [_round(1)]
    kept, gated = asyncio.run(
        RH._jev_apply_live_gate("live", 0.6, "room-1", _FakeRoom(), rounds, {})
    )
    assert kept == rounds and gated == []


def test_live_gate_caps_how_many_rounds_one_scan_can_gate(monkeypatch):
    judged = []

    async def _fake(_state_dict):
        judged.append(len(judged) + 1)
        return _verdict(0.10, 0.95)

    monkeypatch.setattr(RH, "_jev_judge_one", _fake)
    rounds = [_round(n) for n in range(1, 7)]
    kept, gated = asyncio.run(
        RH._jev_apply_live_gate("live", 0.6, "room-1", _FakeRoom(), rounds, {})
    )
    assert len(judged) == RH._JEV_MAX_GATED_ROUNDS
    assert len(gated) == RH._JEV_MAX_GATED_ROUNDS
    # 超出上限的回合按原逻辑放行（fail-open），不静默丢弃
    assert [r["round_key"] for r in kept] == ["round-000005", "round-000006"]
    assert len(kept) + len(gated) == len(rounds)


# ══════════════════════════ 接线：state 映射 ══════════════════════════


def test_round_state_passes_through_only_available_game_semantics():
    state = RH._jev_round_state("room-1", _FakeRoom("douyu"), _round(9, kills_self=3), {})
    assert state["room_id"] == "room-1"
    assert state["platform"] == "douyu"
    assert state["game"] == "valorant"
    assert state["mode"] == "valorant_round"
    assert state["kills_self"] == 3
    # ⚠️ round_key 是 10 秒时间桶，不是回合号：只要上游没有真正的 round_no 字段，
    # 这个键就必须缺席（宁缺勿错），不允许从 round-000009 抠出 9。
    assert "round_no" not in state
    # 管线不产出的字段一律不填、不猜
    assert "spike_planted" not in state
    assert "round_time_left_sec" not in state
    assert "extra" not in state


def test_round_state_skips_blank_values():
    state = RH._jev_round_state("room-1", _FakeRoom(), _round(9, phase="  ", kills_self=None), {})
    assert "phase" not in state
    assert "kills_self" not in state


def test_round_state_keeps_an_explicit_jev_extra_hook():
    state = RH._jev_round_state(
        "room-1", _FakeRoom(), _round(9, jev_extra={"match_point": "yes"}), {}
    )
    assert state["extra"] == {"match_point": "yes"}


def test_round_key_is_never_interpreted_as_a_round_number():
    """round_key 是 10 秒时间桶（valorant_ocr_rounds.py:_round_key），不是回合号。

    夹具里的 round-000105 约是录像 1050 秒处（≈17.5 分钟），当成「第 105 回合」
    喂给模型就是给它一个**错误事实**——而 build_state_text 会把它当明文陈述，
    比 unknown 更糟。所以接线层必须整体不填 round_no。
    """
    state = RH._jev_round_state("room-1", _FakeRoom(), _round(105), {})
    assert "round_no" not in state
    text = J.build_state_text(state)
    assert "Round number" not in text
    assert "105" not in text, "时间桶的数字不得以任何形式进入 prompt"


def test_a_real_upstream_round_no_is_passed_through_when_it_exists():
    """上游一旦真的产出 round_no，透传即可生效（接线层无需再改）。"""
    state = RH._jev_round_state(
        "room-1", _FakeRoom(), _round(105, round_no=8), {}
    )
    assert state["round_no"] == 8
    assert "Round number in the match: 8." in J.build_state_text(state)


def test_state_omits_the_line_entirely_when_a_field_is_undetermined():
    """无法确定的字段必须整体省略该行，而不是输出一个与数字同行的 unknown。"""
    text = J.build_state_text({"game": "valorant"})
    assert "unknown" not in text.lower().split("not available in this data")[0]
    assert "MATCH CONTEXT" in text

    empty = J.build_state_text({})
    assert "MATCH CONTEXT" not in empty, "没有任何上下文时连小节标题都不该出现"
    assert "Game: unknown" not in empty
    assert "NOT AVAILABLE IN THIS DATA" in empty
