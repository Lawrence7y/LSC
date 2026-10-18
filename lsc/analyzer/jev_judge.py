"""Jev（TypeSafe SystemOne）高光判决接入层。

接入点与开关见 docs/reports/jev-integration.md。本模块把「这一回合是否值得切成
高光」从纯 OCR + 规则，扩展成一个**可选**的外部结构化判决，全程可回滚、可对照。

三条硬约束贯穿本模块：

1. **零行为变化**：highlight_judge_mode 默认 "off"；off 时调用方不 import 本模块
   也能跑，且本模块任何路径都**不向调用方抛异常**。
2. **可回滚**：判决只是附加证据（shadow）或门控开关（live），关掉开关即回到原管线。
3. **不阻塞**：judge_round 是同步阻塞函数，必须在 run_in_executor 里调用
   （项目既有线程池 _ai_executor，见 CLAUDE.md 第 8 节）。总耗时受 timeout 约束。

冻结接口
--------
JudgeVerdict 与 judge_round(state, *, timeout=8.0) 的签名与字段由接入任务冻结，
LSC 侧与 Jev 侧按同一份实现，**不要改**。state 的键也是冻结的，缺失即视为
unknown —— 本模块不猜任何值。

问题设计（Jev 的三种原语）
--------------------------
* noul   —— 这回合是否值得切成高光，取 P(yes) 作为 p_clip
* score  —— 精彩度 4 档（平淡/略有起伏/明显精彩/极高强度），取期望分作为 excitement；
            其 confidence 作为 confidence（noul 响应**没有** confidence 字段）
* choice —— 主要理由（ace / clutch / comeback / matchpoint / routine）

两条来自真机实测的经验（jev-1.13.0，6 个合成回合）被写死在 state 与 instructions 里：

* **归属必须显式**：实测写成 "Kills this round: ours 1, enemy 5" 时，模型把**敌方的
  5 杀**当成了我方 multikill（「常规局失利」被误判成 multikill，P=0.35）。所以所有
  计数一律拆成 "OUR team's ..." / "The OPPONENT team's ..." 两句独立陈述，并在
  instructions 里明说「由我方选手造成才算」。
* **戏剧性必须写成明文事实**：实测只给 round_time_left_sec=0 时，「0.1 秒拆包」被
  按普通拆包处理（P=0.53）。凡是要它判断的戏剧性要素（剩余 0.1s、1v3、队友全灭、
  赛点）一律在 state 里直述，**不让模型推断**。

⚠️ 措辞即测量口径
------------------
同一个五杀回合，实测「字符串 state + 带归属/戏剧性说明的问法」P(yes)=0.91，
「对象 state + 简短问法」P(yes)=0.66 —— 差异主要来自措辞而非事实。因此
QUESTIONS / _EXCITEMENT_LEVELS / _REASON_CRITERIA 全部提成**模块级常量**，
一旦定稿就不要在实验中途改字；**换措辞等于换测量口径**，与现有管线的 A/B 结果不可比。

安全
----
* key 只从 os.environ.get("TYPESAFE_API_KEY") 读；
* 本模块**不打印** key；所有日志与错误串都过 lsc.platforms.redaction.redact_text，
  且额外把 key 字面量整体替换掉（防 key 出现在异常文本里）。
"""
from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lsc.platforms.redaction import redact_text
from lsc.utils.coerce import try_float, try_int

_log = logging.getLogger(__name__)

# ── 后端与配置常量（冻结）────────────────────────────────────────────────────

JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
"""SystemOne 端点。测试通过 monkeypatch 本常量替换 HTTP 层，绝不发真请求。"""

JEV_MODEL = "jev-latest"
"""模型别名。响应里的 model 是解析后的实际版本号（实测 jev-1.13.0）。"""

JEV_API_KEY_ENV = "TYPESAFE_API_KEY"
"""API key 的唯一来源。禁止写进任何文件、配置或日志。"""

MODE_OFF = "off"
MODE_SHADOW = "shadow"
MODE_LIVE = "live"
JUDGE_MODES: tuple[str, ...] = (MODE_OFF, MODE_SHADOW, MODE_LIVE)

SETTINGS_MODE_KEY = "highlight_judge_mode"
SETTINGS_MIN_CONFIDENCE_KEY = "highlight_judge_min_confidence"
DEFAULT_JUDGE_MODE = MODE_OFF
DEFAULT_MIN_CONFIDENCE = 0.6

DEFAULT_TIMEOUT_SEC = 8.0
"""单次判决的总预算（秒）。实测中位延迟 1229ms，跨境网络占主导，8s 是合理上限。"""

SOURCE_JEV = "jev"
SOURCE_DISABLED = "disabled"
SOURCE_ERROR = "error"

WORTH_CLIPPING_THRESHOLD = 0.5
"""p_clip 达到该值即认为「值得切」。门控下限另由 min_confidence 决定。"""

INPUT_USD_PER_MTOK = 0.042
"""input token 单价（USD / 百万 token）。output token 官方目前免费。"""

SHADOW_LOG_FILENAME = "jev_judge_shadow.jsonl"
"""shadow 模式的结构化日志（相对项目根目录的 data/ 目录）。"""

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
"""lsc/analyzer/jev_judge.py -> 上溯三级 = 项目根。"""

LIVE_GATE_FAIL_OPEN = True
"""Jev 不可用（无 key / 超时 / 网络错）时 live 门控的取向。

True = fail-open：交回原逻辑，不因我们自己的外部依赖故障而改变导出资格
（与 CLAUDE.md 第 7.4 节「fail_open 用于拒绝即丢数据的路径」同一取向）。
无论如何都会把「判决不可用」写进日志与原因串，不做静默降级。
"""

# 内部 HTTP 策略
_MAX_RETRIES = 2
_RETRY_BASE_DELAY_SEC = 0.5
_MAX_BACKOFF_SEC = 4.0
_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504, 520, 522, 524, 529})
_CONFIG_ERROR_STATUS = frozenset({401, 403})
_MAX_RESPONSE_BYTES = 1 << 20
_MAX_ERROR_BYTES = 4096
_MAX_ERROR_CHARS = 300
_HTTP_USER_AGENT = "LSC-JevJudge/1.0"

# state 文本的长度护栏：官方与实测都指出「state 里无关细节越多，准确率越低」。
_MAX_OCR_TEXT_CHARS = 400
_MAX_EVENT_CHARS = 160
_MAX_EVENTS = 8
_MAX_EXTRA_ITEMS = 12

# ── 问题定义（冻结措辞，勿在实验中途改动）───────────────────────────────────

_EXCITEMENT_LEVELS: tuple[str, ...] = ("平淡", "略有起伏", "明显精彩", "极高强度")
"""精彩度 4 档（有序数组，下标即分值 0..3）。"""

_REASON_CRITERIA: dict[str, str] = {
    "ace": (
        "A single player on OUR team eliminated the entire opposing team by himself "
        "(an ace / 5 eliminations in the round)."
    ),
    "clutch": (
        "OUR team won this round from a short-handed situation, for example one of our "
        "players alive against several opponents while our other players were already dead."
    ),
    "comeback": (
        "OUR team was clearly behind (on the round score or heavily outnumbered) and "
        "still won this round."
    ),
    "matchpoint": "This round decided the match, or was a match point round.",
    "routine": (
        "A normal round: OUR team produced no standout moment, or the OPPONENT team won "
        "this round."
    ),
}

_REASON_GLOSS: dict[str, str] = {
    "ace": "我方单人五杀（ACE）",
    "clutch": "我方选手残局以少打多（clutch）",
    "comeback": "落后局面下逆转本回合（comeback）",
    "matchpoint": "赛点 / 决胜回合",
    "routine": "常规回合，无突出我方高光",
}

QUESTIONS: dict[str, dict[str, Any]] = {
    "worth_clipping": {
        "type": "noul",
        "instructions": (
            "Considering ONLY what OUR team did in this round, is this round worth cutting "
            "into a highlight clip for a short-video audience? Answer yes only when OUR "
            "team's players produced the dramatic, memorable play of the round. "
            "Eliminations, aces, clutches or objective plays made by the OPPONENT team "
            "must NOT make this round a highlight."
        ),
        "criteria": {
            "true": "OUR team produced a dramatic, memorable moment worth a highlight clip.",
            "false": "Nothing OUR team did in this round is worth a highlight clip.",
        },
    },
    "excitement": {
        "type": "score",
        "instructions": (
            "Rate how exciting this round is, again considering ONLY the play of OUR team. "
            "The highest level requires OUR team's players to have made the decisive, "
            "dramatic play of the round. A round won by the OPPONENT team, or a routine "
            "round with no standout moment by OUR team, must be rated at the lowest level."
        ),
        "criteria": list(_EXCITEMENT_LEVELS),
    },
    "tag": {
        "type": "choice",
        "instructions": (
            "What is the single main reason this round is noteworthy, judged from OUR "
            "team's perspective? Choose by what OUR team's players did."
        ),
        "criteria": dict(_REASON_CRITERIA),
    },
}
"""冻结的问法。改动这里等于改变测量口径，必须同步更新实验对比结论。"""

_FROZEN_STATE_KEYS: tuple[str, ...] = (
    # ⚠️ round_no 只能由上游**真有**的字段提供。**严禁**从 LSC 的 round_key 反推：
    # round_key 是 10 秒时间桶（lsc/analyzer/valorant_ocr_rounds.py 的
    # _round_key = f"round-{int(round(float(start) / 10.0)):06d}"），
    # round-000105 约是录像 1050 秒（≈17.5 分钟）处，不是第 105 回合。
    # 把它当回合号陈述给模型就是喂一个**错误事实**，比写 unknown 更糟。
    "room_id",
    "platform",
    "game",
    "mode",
    "round_no",
    "phase",
    "score_self",
    "score_enemy",
    "round_time_left_sec",
    "kills_self",
    "kills_enemy",
    "deaths_self",
    "alive_self",
    "alive_enemy",
    "spike_planted",
    "spike_defused",
    "ace",
    "clutch_1vN",
    "ocr_text",
    "recent_events",
    "extra",
)
"""冻结的 state 键。缺失 / None / "unknown" 一律当 unknown 处理，不猜。"""

_UNKNOWN_TOKENS = frozenset(
    {"", "unknown", "n/a", "na", "none", "null", "?", "-", "未知"}
)


# ── 冻结接口 ────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class JudgeVerdict:
    worth_clipping: bool
    p_clip: float
    excitement: float
    reason: str
    confidence: float
    model: str
    latency_ms: int
    usage: dict
    source: str
    error: str = ""

    def as_log_record(self) -> dict[str, Any]:
        """结构化落盘/落日志用的扁平表示（不含任何凭据）。"""
        return {
            "worth_clipping": self.worth_clipping,
            "p_clip": self.p_clip,
            "excitement": self.excitement,
            "reason": self.reason,
            "confidence": self.confidence,
            "model": self.model,
            "latency_ms": self.latency_ms,
            "usage": dict(self.usage or {}),
            "source": self.source,
            "error": self.error,
        }


# ── 配置读取 ────────────────────────────────────────────────────────────────


def judge_mode(settings: Mapping[str, Any] | None = None) -> str:
    """从 settings 解析 highlight_judge_mode。

    未知值一律回退 "off" —— 配置写错时**不能**意外打开 live 门控。
    """
    if not isinstance(settings, Mapping):
        return DEFAULT_JUDGE_MODE
    raw = settings.get(SETTINGS_MODE_KEY)
    if not isinstance(raw, str):
        return DEFAULT_JUDGE_MODE
    mode = raw.strip().lower()
    return mode if mode in JUDGE_MODES else DEFAULT_JUDGE_MODE


def judge_min_confidence(settings: Mapping[str, Any] | None = None) -> float:
    """从 settings 解析 highlight_judge_min_confidence（默认 0.6，夹到 [0, 1]）。"""
    if not isinstance(settings, Mapping):
        return DEFAULT_MIN_CONFIDENCE
    parsed = try_float(settings.get(SETTINGS_MIN_CONFIDENCE_KEY))
    if parsed is None:
        return DEFAULT_MIN_CONFIDENCE
    return min(1.0, max(0.0, parsed))


# ── state 组装（显式归属 + 明文戏剧性）──────────────────────────────────────


def _text(state: Mapping[str, Any], key: str) -> str | None:
    """取一个已知的字符串事实；缺失/哨兵值返回 None。"""
    if key not in state:
        return None
    raw = state[key]
    if raw is None or isinstance(raw, (bool, int, float, Mapping, list, tuple)):
        return None
    text = str(raw).strip()
    return None if text.lower() in _UNKNOWN_TOKENS else text


def _num(state: Mapping[str, Any], key: str) -> float | None:
    """取一个已知的数值事实；缺失/哨兵值返回 None。"""
    if key not in state:
        return None
    raw = state[key]
    if isinstance(raw, str) and raw.strip().lower() in _UNKNOWN_TOKENS:
        return None
    return try_float(raw)


def _int(state: Mapping[str, Any], key: str) -> int | None:
    parsed = _num(state, key)
    return None if parsed is None else int(parsed)


def _flag(state: Mapping[str, Any], key: str) -> bool | None:
    """取一个三态布尔事实：True / False / None(unknown)。"""
    if key not in state:
        return None
    raw = state[key]
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        lowered = raw.strip().lower()
        if lowered in _UNKNOWN_TOKENS:
            return None
        if lowered in {"true", "yes", "1", "是"}:
            return True
        if lowered in {"false", "no", "0", "否"}:
            return False
        return None
    if isinstance(raw, (int, float)):
        return bool(raw)
    return None


def _fmt(value: float) -> str:
    """数值渲染：整数不带小数点，小数最多两位。"""
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def build_state_text(state: Mapping[str, Any] | None = None) -> str:
    """把冻结的 state 字典拼成**明确的自然语言**陈述（含显式归属）。

    设计要点（全部有实测依据）：

    * 每个计数都拆成「我方 / 对方」两句独立陈述，杜绝把对方的高光算成我方的；
    * 戏剧性要素（剩余 <=1s、1vN、队友全灭、赛点）写成**明文事实**，不让模型推断；
    * 缺失的键**不猜**，并显式列出「本回合没有拿到哪些关键事实」，避免模型自行补全；
    * 只写与本判决相关的字段，不塞无关文本（官方明确：state 越杂准确率越低）。
    """
    src = state if isinstance(state, Mapping) else {}
    lines: list[str] = [
        "A single round of a competitive match, described from OUR team's point of view.",
        "Every fact below is stated explicitly. Do not infer anything that is not written here.",
    ]

    # ⚠️ 无法确定的字段**整体省略该行**（统一收口在文件末的 NOT AVAILABLE 清单里），
    # 不得出现「unknown」与一个数字同行 —— 把没把握的数字写成明文事实，
    # 比写 unknown 更糟：模型会当作已知条件去推理。
    context: list[str] = []
    game = _text(src, "game")
    if game:
        context.append(f"- Game: {game}.")
    mode = _text(src, "mode")
    if mode:
        context.append(f"- Game mode: {mode}.")
    platform = _text(src, "platform")
    if platform:
        context.append(f"- Broadcast platform: {platform}.")
    round_no = _int(src, "round_no")
    if round_no is not None:
        context.append(f"- Round number in the match: {round_no}.")
    phase = _text(src, "phase")
    if phase:
        context.append(f"- Current phase of the round: {phase}.")
    if context:
        lines.append("")
        lines.append("MATCH CONTEXT")
        lines.extend(context)

    score_self = _int(src, "score_self")
    score_enemy = _int(src, "score_enemy")
    if score_self is not None or score_enemy is not None:
        lines.append("")
        lines.append("SCORE")
        if score_self is not None:
            lines.append(f"- OUR team's round score: {score_self}.")
        if score_enemy is not None:
            lines.append(f"- The OPPONENT team's round score: {score_enemy}.")

    kills_self = _int(src, "kills_self")
    kills_enemy = _int(src, "kills_enemy")
    deaths_self = _int(src, "deaths_self")
    if kills_self is not None or kills_enemy is not None or deaths_self is not None:
        lines.append("")
        lines.append(
            "ELIMINATIONS IN THIS ROUND (strict attribution -- do not mix these up)"
        )
        if kills_self is not None:
            lines.append(
                f"- OUR team's players took {kills_self} elimination(s) in this round."
            )
        if kills_enemy is not None:
            lines.append(
                f"- The OPPONENT team's players took {kills_enemy} elimination(s) in this round."
            )
        if deaths_self is not None:
            lines.append(
                f"- OUR team lost {deaths_self} player(s) in this round."
            )

    alive_self = _int(src, "alive_self")
    alive_enemy = _int(src, "alive_enemy")
    if alive_self is not None or alive_enemy is not None:
        lines.append("")
        lines.append("SURVIVORS AT THE END OF THE ROUND")
        if alive_self is not None:
            lines.append(f"- OUR team had {alive_self} player(s) alive.")
        if alive_enemy is not None:
            lines.append(f"- The OPPONENT team had {alive_enemy} player(s) alive.")

    left = _num(src, "round_time_left_sec")
    spike_planted = _flag(src, "spike_planted")
    spike_defused = _flag(src, "spike_defused")
    if left is not None or spike_planted is not None or spike_defused is not None:
        lines.append("")
        lines.append("TIMING AND OBJECTIVE")
        if left is not None:
            lines.append(
                f"- Round time remaining when the round ended: {_fmt(left)} seconds."
            )
            if 0.0 <= left <= 1.0:
                lines.append(
                    f"  This is an extremely narrow margin -- only {_fmt(left)} seconds "
                    "remained. A round decided within one second of the timer reaching "
                    "zero is highly dramatic."
                )
        if spike_planted is True:
            lines.append("- OUR team planted the spike (the objective) in this round.")
        elif spike_planted is False:
            lines.append("- The spike was NOT planted in this round.")
        if spike_defused is True:
            lines.append("- The spike was defused in this round.")
        elif spike_defused is False:
            lines.append("- The spike was NOT defused in this round.")

    drama: list[str] = []
    ace = _flag(src, "ace")
    if ace is True:
        drama.append(
            "- OUR team scored an ACE: a single player on OUR team eliminated the entire "
            "opposing team by himself."
        )
    elif ace is False:
        drama.append(
            "- OUR team did NOT score an ace in this round (no single player of ours "
            "eliminated the whole opposing team)."
        )
    clutch = _int(src, "clutch_1vN")
    if clutch is not None and clutch >= 1:
        drama.append(
            f"- OUR team's player won a 1-versus-{clutch} clutch: one of our players was "
            f"alive against {clutch} opponents while all our other players were already "
            "eliminated."
        )
    if drama:
        lines.append("")
        lines.append("DECISIVE FACTS TO WEIGH (stated explicitly, not inferred)")
        lines.extend(drama)

    ocr_text = _text(src, "ocr_text")
    if ocr_text:
        lines.append("")
        lines.append(
            "RAW ON-SCREEN TEXT OF THIS ROUND (OCR; may be noisy and may include "
            "commentary or replay overlays). Use only as supporting evidence; the "
            "explicit facts above take precedence."
        )
        lines.append(f'"{ocr_text[:_MAX_OCR_TEXT_CHARS]}"')

    events = src.get("recent_events")
    if isinstance(events, (list, tuple)):
        rendered = [
            str(item).strip()[:_MAX_EVENT_CHARS]
            for item in list(events)[:_MAX_EVENTS]
            if str(item).strip()
        ]
        if rendered:
            lines.append("")
            lines.append("RECENT EVENT TIMELINE")
            lines.extend(f"- {item}" for item in rendered)

    extra = src.get("extra")
    if isinstance(extra, Mapping):
        scalars = [
            (str(key), str(value).strip())
            for key, value in list(extra.items())[:_MAX_EXTRA_ITEMS]
            if isinstance(value, (str, int, float, bool)) and str(value).strip() != ""
        ]
        if scalars:
            lines.append("")
            lines.append("SUPPLEMENTARY FIELDS")
            lines.extend(f"- {key}: {value}" for key, value in scalars)

    missing = [key for key in _FROZEN_STATE_KEYS if _missing(src, key)]
    if missing:
        lines.append("")
        lines.append(
            "NOT AVAILABLE IN THIS DATA (do not guess these): " + ", ".join(missing) + "."
        )

    return "\n".join(lines)


def _missing(state: Mapping[str, Any], key: str) -> bool:
    """该冻结键在本回合是否「没有拿到值」。"""
    if key == "recent_events":
        events = state.get(key)
        return not (isinstance(events, (list, tuple)) and len(events) > 0)
    if key == "extra":
        extra = state.get(key)
        return not (isinstance(extra, Mapping) and len(extra) > 0)
    if key == "ocr_text":
        return _text(state, key) is None
    if key in {"spike_planted", "spike_defused", "ace"}:
        return _flag(state, key) is None
    if key in {
        "kills_self",
        "kills_enemy",
        "deaths_self",
        "alive_self",
        "alive_enemy",
        "round_no",
        "score_self",
        "score_enemy",
        "clutch_1vN",
        "round_time_left_sec",
    }:
        return _num(state, key) is None
    return _text(state, key) is None


def build_payload(state: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """构造 SystemOne 请求体（state 用字符串形态 —— 实测比对象形态更准）。

    questions 是 **map**（不是数组），候选写在 criteria 里，没有 options 字段。
    """
    return {
        "model": JEV_MODEL,
        "state": build_state_text(state),
        "questions": copy.deepcopy(QUESTIONS),
    }


# ── 判决 ────────────────────────────────────────────────────────────────────


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _safe(text: object) -> str:
    """日志/错误串出口：先过项目统一脱敏，再确保 key 字面量不出现。"""
    redacted = redact_text(text)
    key = os.environ.get(JEV_API_KEY_ENV) or ""
    if len(key) >= 8 and key in redacted:
        redacted = redacted.replace(key, "<redacted>")
    return redacted[:_MAX_ERROR_CHARS]


def _read_api_key() -> str:
    """key 的唯一读取点。"""
    return (os.environ.get(JEV_API_KEY_ENV) or "").strip()


def _failed_verdict(started: float, *, source: str, error: str) -> JudgeVerdict:
    """失败/不可用时的统一返回：worth_clipping=False 且 error 非空。"""
    return JudgeVerdict(
        worth_clipping=False,
        p_clip=0.0,
        excitement=0.0,
        reason="",
        confidence=0.0,
        model=JEV_MODEL,
        latency_ms=_elapsed_ms(started),
        usage={},
        source=source,
        error=error,
    )


class _JevResponseError(Exception):
    """内部：响应体不符合文档契约。对外折叠进 JudgeVerdict.error，不抛出。"""


@dataclass(slots=True)
class _HttpOutcome:
    payload: dict[str, Any] | None
    status: int | None
    error: str


def _http_post(payload: dict[str, Any], *, api_key: str, timeout: float) -> _HttpOutcome:
    """单次 POST。返回 (解析后的 body, 状态码, 错误串)；不抛异常。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        JEV_ENDPOINT,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": _HTTP_USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(_MAX_RESPONSE_BYTES)
            status = int(getattr(response, "status", 200) or 200)
    except urllib.error.HTTPError as exc:
        status = int(getattr(exc, "code", 0) or 0)
        detail = ""
        try:
            detail = _safe(exc.read(_MAX_ERROR_BYTES).decode("utf-8", "replace").strip())
        except Exception:
            detail = ""
        return _HttpOutcome(None, status, f"http {status}{': ' + detail if detail else ''}")
    except TimeoutError:
        return _HttpOutcome(None, None, "timeout")
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        # socket.timeout 自 py3.10 起就是 TimeoutError 的别名（requires-python >=3.10）
        if isinstance(reason, TimeoutError):
            return _HttpOutcome(None, None, "timeout")
        return _HttpOutcome(None, None, f"network: {_safe(reason)}")
    except Exception as exc:
        return _HttpOutcome(None, None, f"{type(exc).__name__}: {_safe(exc)}")

    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        return _HttpOutcome(None, status, f"malformed response: {type(exc).__name__}")
    if not isinstance(decoded, dict):
        return _HttpOutcome(None, status, "malformed response: not a JSON object")
    return _HttpOutcome(decoded, status, "")


def _retry_after_seconds(retry_after: object) -> float | None:
    """解析 Retry-After（可能是秒数，也可能是 HTTP 日期 —— 只支持秒数形态）。"""
    if retry_after is None:
        return None
    if isinstance(retry_after, (int, float)) and not isinstance(retry_after, bool):
        return max(0.0, float(retry_after))
    parsed = try_float(str(retry_after).strip())
    if parsed is None or parsed < 0:
        return None
    return parsed


def _backoff(deadline: float, attempt: int, retry_after: float | None = None) -> bool:
    """在总预算内退避。返回 False 表示预算不够、放弃重试。"""
    delay = min(_MAX_BACKOFF_SEC, _RETRY_BASE_DELAY_SEC * (2 ** (attempt - 1)))
    if retry_after is not None:
        delay = max(delay, min(_MAX_BACKOFF_SEC, retry_after))
    remaining = deadline - time.monotonic()
    if remaining <= 0 or delay >= remaining:
        return False
    time.sleep(delay)
    return True


def call_jev(
    payload: dict[str, Any], *, api_key: str, timeout: float = DEFAULT_TIMEOUT_SEC
) -> _HttpOutcome:
    """带 429/5xx 退避重试的 POST。**总耗时不超过 timeout**；不抛异常。

    401/403 属配置错误（实测语义），**不重试**。

    已知局限：DNS 解析（getaddrinfo）不受 socket 超时约束，极端情况下可能超出预算。
    生产上由 _ai_executor 线程池隔离，超时不会阻塞编排线程。
    """
    deadline = time.monotonic() + max(0.0, timeout)
    attempt = 0
    outcome = _HttpOutcome(None, None, "timeout")
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return outcome if outcome.error else _HttpOutcome(None, None, "timeout")
        outcome = _http_post(payload, api_key=api_key, timeout=remaining)
        if not outcome.error:
            return outcome
        status = outcome.status or 0
        if status in _CONFIG_ERROR_STATUS or status not in _RETRYABLE_STATUS:
            return outcome
        if attempt >= _MAX_RETRIES:
            return outcome
        attempt += 1
        if not _backoff(deadline, attempt):
            return outcome


def _verdict_from_response(parsed: Mapping[str, Any], *, started: float) -> JudgeVerdict:
    """把 SystemOne 响应解析成 JudgeVerdict。契约不符时抛 _JevResponseError。"""
    answers = parsed.get("answers")
    if not isinstance(answers, Mapping):
        raise _JevResponseError("response has no 'answers' map")

    worth = answers.get("worth_clipping")
    if not isinstance(worth, Mapping):
        raise _JevResponseError("missing 'worth_clipping' answer")
    p_clip = try_float(worth.get("noul"))
    if p_clip is None or not 0.0 <= p_clip <= 1.0:
        raise _JevResponseError("invalid 'worth_clipping.noul'")

    excitement_answer = answers.get("excitement")
    if not isinstance(excitement_answer, Mapping):
        raise _JevResponseError("missing 'excitement' answer")
    excitement = try_float(excitement_answer.get("score"))
    if excitement is None:
        raise _JevResponseError("invalid 'excitement.score'")
    # noul 响应**没有** confidence 字段，所以 confidence 取 score 头的（官方契约必填）。
    confidence = try_float(excitement_answer.get("confidence"))
    if confidence is None:
        raise _JevResponseError("invalid 'excitement.confidence'")

    # 第三个原语（理由）是可选的：拿不到就降级成 unknown，不让整条判决失败。
    reason = "unknown"
    tag_answer = answers.get("tag")
    if isinstance(tag_answer, Mapping):
        choice = tag_answer.get("choice")
        if isinstance(choice, str) and choice.strip():
            reason = choice.strip()

    raw_usage = parsed.get("usage")
    usage_map = raw_usage if isinstance(raw_usage, Mapping) else {}
    usage = {
        "input_tokens": try_int(usage_map.get("input_tokens")) or 0,
        "output_tokens": try_int(usage_map.get("output_tokens")) or 0,
    }

    model = parsed.get("model")
    model_name = model.strip() if isinstance(model, str) and model.strip() else JEV_MODEL

    return JudgeVerdict(
        worth_clipping=p_clip >= WORTH_CLIPPING_THRESHOLD,
        p_clip=round(p_clip, 4),
        excitement=round(excitement, 4),
        reason=reason,
        confidence=round(min(1.0, max(0.0, confidence)), 4),
        model=model_name,
        latency_ms=_elapsed_ms(started),
        usage=usage,
        source=SOURCE_JEV,
        error="",
    )


def judge_round(state: dict, *, timeout: float = DEFAULT_TIMEOUT_SEC) -> JudgeVerdict:
    """对单个回合做一次 Jev 判决。

    冻结接口：state 的键见模块文档；timeout 是**总预算**（含重试与退避）。

    契约（必须始终成立）：

    * **绝不抛异常** —— 任何失败都折叠进返回值，避免打断分析循环；
    * 无 key / 超时 / 网络失败 / 畸形响应时 error 非空且 worth_clipping=False；
    * 无 key 时 source == "disabled"（从未发出请求）；
    * 总耗时不超过 timeout（DNS 解析除外，见 call_jev）；
    * key 不出现在任何日志或错误文本里。

    本函数是**同步阻塞**的，调用方必须在 run_in_executor 中执行（见接线层）。
    """
    started = time.monotonic()
    budget = try_float(timeout)
    if budget is None or budget <= 0:
        budget = DEFAULT_TIMEOUT_SEC

    try:
        api_key = _read_api_key()
        if not api_key:
            _log.debug("Jev 判决跳过：未设置 %s", JEV_API_KEY_ENV)
            return _failed_verdict(
                started, source=SOURCE_DISABLED, error=f"missing {JEV_API_KEY_ENV}"
            )

        try:
            payload = build_payload(state)
        except Exception as exc:
            return _failed_verdict(
                started, source=SOURCE_ERROR, error=f"state build failed: {_safe(exc)}"
            )

        outcome = call_jev(payload, api_key=api_key, timeout=budget)
        if outcome.error:
            # ⚠️ 日志出口也必须脱敏：_http_post 会带回服务端/代理回吐的错误体，
            # 里面可能原样包含 Authorization 头（测试 test_api_key_never_appears_in_logs
            # 就是照这条路径钉住的）。
            redacted_error = _safe(outcome.error)
            if outcome.status in _CONFIG_ERROR_STATUS:
                _log.warning(
                    "Jev 鉴权失败（配置错误，未重试；不影响主流程）: %s", redacted_error
                )
                source = SOURCE_DISABLED
            elif outcome.error == "timeout":
                _log.debug("Jev 判决超时（%.1fs，不影响主流程）", budget)
                source = SOURCE_ERROR
            else:
                _log.debug("Jev 判决失败（不影响主流程）: %s", redacted_error)
                source = SOURCE_ERROR
            return _failed_verdict(started, source=source, error=redacted_error)

        try:
            return _verdict_from_response(outcome.payload or {}, started=started)
        except _JevResponseError as exc:
            _log.warning("Jev 响应不符合契约（不影响主流程）: %s", _safe(exc))
            return _failed_verdict(started, source=SOURCE_ERROR, error=_safe(exc))
    except Exception as exc:  # 兜底：绝不让判决层打断分析循环
        escaped = _safe(f"{type(exc).__name__}: {exc}")
        _log.warning("Jev 判决异常（已吞掉，不影响主流程）: %s", escaped)
        return _failed_verdict(started, source=SOURCE_ERROR, error=escaped)


# ── 门控与对照 ──────────────────────────────────────────────────────────────


def verdict_is_usable(verdict: JudgeVerdict) -> bool:
    """判决是否真实可用（来自 Jev 且无错误）。"""
    return verdict is not None and verdict.source == SOURCE_JEV and not verdict.error


def gate_out_reason(
    verdict: JudgeVerdict, min_confidence: float = DEFAULT_MIN_CONFIDENCE
) -> str:
    """live 门控：返回非空串 = **撤销自动导出资格**（降级为待人工确认）的原因。

    判定规则（只用 p_clip 一个信号；旋钮沿用既有 min_confidence，不引入第二个阈值）：

    * p_clip < min_confidence        → 门控掉；
    * 判决不可用（无 key/超时/网络错/畸形）→ 按 LIVE_GATE_FAIL_OPEN 处理。

    为什么**不**用 JudgeVerdict.confidence 门控（2026-09-20 修正，实测依据）：
    noul 应答本身没有 confidence 字段，verdict.confidence 只能取 **score 头（精彩度档位分布）**
    的 confidence —— 它衡量"精彩度分档有多尖"，与"值不值得切"不是同一个问题。
    真 Jev 实测（13 次调用，见 docs/reports/jev-integration.md）它在关键局面上误杀：

    * matchpoint   p_clip=0.87 但 confidence=0.00 → 被门控；
    * clutch_1v3   p_clip=0.92 但 confidence=0.58 → 被门控（< 0.60 阈值）。

    而 p_clip 本身的判别力很干净（ACE 0.96 / clutch 0.92 / 常规局 0.08）。
    confidence 仍然写入日志与 as_log_record()，只是**不再参与门控**。

    注意：门控掉**不等于丢弃**。调用方只把 clip_queued 的 confirm_status
    从 vision_confirmed 降级为 pending（仍入列、仍可见、可人工确认导出）。
    """
    threshold = judge_min_confidence({SETTINGS_MIN_CONFIDENCE_KEY: min_confidence})
    if not verdict_is_usable(verdict):
        if LIVE_GATE_FAIL_OPEN:
            return ""
        error = getattr(verdict, "error", "") or "no verdict"
        return f"jev_unavailable: {error}"
    if verdict.p_clip < threshold:
        return f"jev_p_clip {verdict.p_clip:.3f} < min_confidence {threshold:.2f}"
    return ""


def disagrees_with_original(verdict: JudgeVerdict, *, original_worth_clipping: bool) -> bool:
    """判决与原决策是否**不一致**（shadow 模式打 WARNING 的判据）。

    判决不可用时不算「不一致」——否则 Jev 一断网就会刷满 WARNING。
    """
    if not verdict_is_usable(verdict):
        return False
    return bool(verdict.worth_clipping) != bool(original_worth_clipping)


def reason_text(verdict: JudgeVerdict) -> str:
    """把 reason 的机器可读 id 渲染成「id — 中文说明」，供日志/前端展示。"""
    choice = (verdict.reason or "").strip()
    if not choice or choice == "unknown":
        return "unknown"
    gloss = _REASON_GLOSS.get(choice)
    return f"{choice} — {gloss}" if gloss else choice


def estimate_cost_usd(usage: Mapping[str, Any] | None) -> float:
    """单次判决的美元成本（官方口径：按 input token 计费，output 免费）。"""
    tokens = try_int((usage or {}).get("input_tokens")) or 0
    return tokens / 1_000_000 * INPUT_USD_PER_MTOK


# ── shadow 结构化日志 ───────────────────────────────────────────────────────

_SHADOW_LOG_LOCK = threading.Lock()
"""shadow JSONL 的追加锁。判决可能来自 _ai_executor 的多个工作线程，
必须串行化整行写入，否则同一次判决的字段会与另一次交错。"""


def shadow_log_path() -> Path:
    """shadow 日志路径：<项目根>/data/jev_judge_shadow.jsonl。

    与 python-backend/persistence.py 的 DEFAULT_DATA_DIR 同源：LSC_DATA_DIR 优先
    （打包后 Electron 传入 userData 目录），开发模式回退项目内 data/。
    """
    root = os.environ.get("LSC_DATA_DIR") or str(_PROJECT_ROOT)
    return Path(root) / "data" / SHADOW_LOG_FILENAME


def append_shadow_record(record: Mapping[str, Any]) -> str:
    """把一条判决记录以 JSONL 追加进 shadow 日志；返回写入路径。

    **永不抛异常** —— 影子日志是诊断产物，写不进去只告警，绝不能影响分析循环。
    单行一次 write（含换行）并在锁内完成，保证 JSONL 行不交错。
    """
    path = shadow_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(dict(record), ensure_ascii=False, separators=(",", ":")) + "\n"
        with _SHADOW_LOG_LOCK, open(path, "a", encoding="utf-8") as handle:
            handle.write(line)
    except Exception as exc:
        _log.warning("Jev shadow 日志写入失败（已忽略）: %s", _safe(exc))
    return str(path)


__all__ = [
    "DEFAULT_JUDGE_MODE",
    "DEFAULT_MIN_CONFIDENCE",
    "DEFAULT_TIMEOUT_SEC",
    "INPUT_USD_PER_MTOK",
    "JEV_API_KEY_ENV",
    "JEV_ENDPOINT",
    "JEV_MODEL",
    "JUDGE_MODES",
    "JudgeVerdict",
    "MODE_LIVE",
    "MODE_OFF",
    "MODE_SHADOW",
    "QUESTIONS",
    "SETTINGS_MIN_CONFIDENCE_KEY",
    "SETTINGS_MODE_KEY",
    "SHADOW_LOG_FILENAME",
    "SOURCE_DISABLED",
    "SOURCE_ERROR",
    "SOURCE_JEV",
    "WORTH_CLIPPING_THRESHOLD",
    "append_shadow_record",
    "build_payload",
    "build_state_text",
    "call_jev",
    "disagrees_with_original",
    "estimate_cost_usd",
    "gate_out_reason",
    "judge_min_confidence",
    "judge_mode",
    "judge_round",
    "reason_text",
    "shadow_log_path",
    "verdict_is_usable",
]
