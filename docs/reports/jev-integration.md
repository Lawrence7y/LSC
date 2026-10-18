# Jev 高光判决接入报告（feat/jev-highlight-judge）

> 范围：把「某回合是否值得切成高光」这一步接成一个**可选**的外部结构化判决（TypeSafe Jev / SystemOne）。
> 分支：`feat/jev-highlight-judge`（自 `fix/debt-20260918` 的 HEAD `dbeb707d` 切出，工作区未动）。
> 方法：**事实**＝已在真实运行输出中核验；**推断**＝本报告的解释性假设；**未验证**＝需要真机（真直播流）或用户环境才能定论。

---

## 0. 一句话结论

判决模块已按冻结接口实现并用**真 Jev 报文**验证过解析层、判决逻辑与门控（13 次真调用，原始输出见 §7.1）；
shadow/live 接线落在「确认回合」的既有代码路径上，**默认 off，逐字节零行为变化**（§2、§7.2）。
**但 live 目前不具备生产可用性**：回合级游戏语义字段上游不产出，判决拿到的 state 缺 kills/spike/ace，见 §8.1。
因此本分支的推荐状态是 `shadow`，不是 `live`。

---

## 1. 接入点（为什么是这个位置）

任务要求「在现有『确认回合』的代码路径上加一个影子分支」。实测这条路径如下：

- 持续分析（`_continuous_analysis_loop`，`room_handler.py`）扫描出回合后，用
  `_is_listable_ocr_round(h)` 决定**是否入列**，再用 `_is_auto_exportable_valorant_round(h)`
  决定**是否具备自动导出资格**，然后按两个桶分别调用 `_auto_export_highlights(...)`：
  `confirm_status='vision_confirmed'`（可自动导出）与 `confirm_status='pending'`（待人工确认）。

> **事实**：这条路径上**所有**调用点都传 `list_only=True`（`room_handler.py` 两处：
> 持续分析回合与 scene/generic 回合）。按 `_auto_export_highlights` 的文档串，`list_only=True`
> **不写** `_deferred_export_jobs`，因此持续分析路径当前**只入列、不启 FFmpeg**。

这条事实决定了「不做自动导出（但允许进入待人工确认）」在本仓的**精确语义**：
它等于**撤销 `vision_confirmed` 资格、降级为 `pending`**，而不是取消一个已经在跑的导出。
所以门控钩子放在 `_is_auto_exportable_valorant_round` 里（一处判据，下游收尾统计与待导出重试共用同一口径）：

```python
_JEV_GATED_REASON_FIELD = 'jev_judge_gate_reason'
def _is_auto_exportable_valorant_round(round_data: dict[str, Any]) -> bool:
    if not _is_ocr_round(round_data):
        return False
    # 默认 off 时该字段永不出现，行为与原来逐字节一致
    if round_data.get(_JEV_GATED_REASON_FIELD):
        return False
    ...
```

以及调用点（`room_handler.py` 持续分析入列分支）：

```python
if _jev_cfg and _jev_cfg[0] == 'live':
    confirmed_hl, _jev_gated_hl = await _jev_apply_live_gate(
        _jev_cfg[0], _jev_cfg[1], room_id, main_room_for_map, confirmed_hl, state,
    )
    pending_hl.extend(_jev_gated_hl)
```

**未改动**：`valorant_broadcast.py`、`valorant_ocr_rounds.py` 的内部逻辑一行未动
（`git diff --stat` 见 §9）。

---

## 2. 如何开关

配置在 `python-backend/settings.json`（该文件在 `.gitignore:88` 内，改它不产生新的未跟踪文件）：

```json
{
  "highlight_judge_mode": "off",
  "highlight_judge_min_confidence": 0.6
}
```

| 键 | 取值 | 默认 | 语义 |
| :-- | :-- | :-- | :-- |
| `highlight_judge_mode` | `"off"` / `"shadow"` / `"live"` | `off` | off＝完全跳过；shadow＝只记录对照；live＝用判决门控自动导出资格 |
| `highlight_judge_min_confidence` | float，夹到 [0, 1] | `0.6` | `p_clip` 与 Jev 自报 `confidence` 共用的下限 |

三条**默认安全**的性质，都有守卫钉住：

1. **off 时连模块都不加载**：`_jev_mode_and_threshold()` 先用原始 settings 值做廉价短路，
   配置不是 shadow/live 就直接返回 `None`，不 import `lsc.analyzer.jev_judge`
   （守卫 `test_off_mode_does_not_import_the_judge_module`）。
2. **配置写错＝off**，不是 live：`judge_mode()` 对任何非 `shadow`/`live` 的值
   （含 `True`、`"Live!"`、数字）一律回退 `off`。
3. **live 只能在显式配置下开启**：源码守卫 `test_live_gate_is_gated_by_the_live_flag_in_source`。

### 2.1 时序语义（关键，与直觉不同）

| 模式 | 判决时机 | 对分析循环的影响 |
| :-- | :-- | :-- |
| `shadow` | 列完之后 **fire-and-forget** | **零**。不 await（守卫 `test_shadow_hook_is_fire_and_forget_and_gated_in_source` 断言源码里不存在 `await _jev_spawn_shadow`） |
| `live` | 在**下发** `vision_confirmed` **之前** | 最坏 ≈ **一次**判决的 timeout（默认 8s），不是 N 倍 —— 判决并发跑在 `_ai_executor`，并由 `_JEV_TIMEOUT_SEC` 与 `_JEV_MAX_GATED_ROUNDS=4` 双重封顶 |

**为什么 live 不能也做成异步「事后降级」**：实测中位延迟 1225ms（父级直连 api.typesafe.ai 的 6 次调用）
/ 405ms（本会话经本地转发通道的 13 次调用），跨境网络占主导，判决必然晚于入列。
纯异步版本需要在下发之后重建 `clip_queued` 的 upsert 载荷（映射后的 start/end、
`_listed_clip_ids`/`_listed_clip_bounds` 簿记）才能撤资格，改动面远大于本次「最小 diff」的预算，
且那条路径需要前端配合验证。因此 live 选择用**有界的等待**换正确性，并把代价写在上面这张表里。
判决**绝不在事件循环上执行**（`run_in_executor(_ai_executor, ...)`），编排线程不被占用。

### 2.2 判决不可用时 live 怎么办

`LIVE_GATE_FAIL_OPEN = True`：**放行**，交回原逻辑。理由与 CLAUDE.md §7.4
「`fail_open=True` 用于『拒绝即丢数据』的路径」同源 —— 不因我们自己的外部依赖故障而改变导出资格。
无论放行与否，判决不可用都会进 shadow 日志与（live 下的）`jev_judge_gate_reason`，不做静默降级。
把该常量改成 `False` 即切换为 fail-closed，有单测覆盖两种取值。

### 2.3 三种日志/记录出口

| 出口 | 何时 | 内容 |
| :-- | :-- | :-- |
| `data/jev_judge_shadow.jsonl` | shadow 每回合一行 | 判决全字段 + `original_worth_clipping` + `agree` + `round_key` |
| `WARNING` 日志 | shadow 且与原决策不一致 | 原值 / Jev 值 / `p_clip` / `confidence` / 理由 / 日志路径 |
| `clip_queued` 载荷 | live 且被门控掉 | 回合降级为 `pending`，并带 `jev_judge_gate_reason` 与 `jev_judge` |

shadow 日志路径尊重 `LSC_DATA_DIR`（与 `persistence.py` 的 `DEFAULT_DATA_DIR` 同源），
开发模式回退到 `<项目根>/data/`。

---

## 3. 判决模块：接口与问题设计

冻结接口（未改动，与任务给定逐字一致）：

```python
@dataclass(slots=True)
class JudgeVerdict:
    worth_clipping: bool; p_clip: float; excitement: float; reason: str; confidence: float
    model: str; latency_ms: int; usage: dict; source: str; error: str = ""

def judge_round(state: dict, *, timeout: float = 8.0) -> JudgeVerdict
```

字段映射照官方契约（取自 `GET https://api.typesafe.ai/openapi.json` 的
`components.schemas`，在本会话内**实测抓取**，不是猜的）：

| JudgeVerdict | SystemOne 响应路径 |
| :-- | :-- |
| `p_clip` | `answers.worth_clipping.noul`（noul 原语，P(yes)） |
| `excitement` | `answers.excitement.score`（0..3 期望分） |
| `confidence` | `answers.excitement.confidence` —— **noul 响应没有 confidence 字段** |
| `reason` | `answers.tag.choice`（缺失降级为 `"unknown"`，不让整条判决失败） |
| `usage` | `usage.input_tokens` / `usage.output_tokens` |
| `model` | 顶层 `model`（实测 `"jev-1.13.0"`，别名解析后的真实版本号） |

`worth_clipping` ＝ `p_clip >= 0.5`（模型自身的结论）；**门控**是另一层，用
`highlight_judge_min_confidence`（§6）。

请求体形状（官方契约，`questions` 是 **map** 不是数组，候选写在 `criteria` 里）：

```json
{"model": "jev-latest",
 "state": "<字符串>",
 "questions": {
   "worth_clipping": {"type": "noul",  "instructions": "...", "criteria": {"true": "...", "false": "..."}},
   "excitement":     {"type": "score", "instructions": "...", "criteria": ["平淡","略有起伏","明显精彩","极高强度"]},
   "tag":            {"type": "choice","instructions": "...", "criteria": {"ace": "...", "clutch": "...", ...}}
 }}
```

### 3.1 state 设计：两条来自实测的修正被写死进代码

**① 归属必须显式。** 父级实测：写成 `"Kills this round: ours 1, enemy 5"` 时，模型把
**敌方的 5 杀**当成我方高光（「常规局失利」被判成 `multikill`，P=0.35）。
本实现把每个计数拆成两句独立陈述，并在 instructions 里明说「由我方选手造成才算」：

```text
ELIMINATIONS IN THIS ROUND (strict attribution -- do not mix these up)
- OUR team's players took 1 elimination(s) in this round.
- The OPPONENT team's players took 5 elimination(s) in this round.
```

**② 戏剧性必须写成明文事实，不让模型推断。** 父级实测：只给 `round_time_left_sec=0`
（而非 0.1）时，「0.1 秒拆包」被按普通拆包处理，P=0.53。本实现对剩余 ≤1s、
`clutch_1vN`、`ace` 都写明文：

```text
TIMING AND OBJECTIVE
- Round time remaining when the round ended: 0.1 seconds.
  This is an extremely narrow margin -- only 0.1 seconds remained. A round decided
  within one second of the timer reaching zero is highly dramatic.

DECISIVE FACTS TO WEIGH (stated explicitly, not inferred)
- OUR team's player won a 1-versus-3 clutch: one of our players was alive against
  3 opponents while all our other players were already eliminated.
```

**③ 缺失即 unknown，不猜。** 没拿到的键会出现在显式的 UNKNOWN 清单里，模型不会被诱导补全：

```text
NOT AVAILABLE IN THIS DATA (do not guess these): room_id, ocr_text, recent_events, extra.
```

同时**不塞无关文本**：边界/审计元数据（`start_by`/`boundary_quality`/`broadcast_audit`…）
是「切得准不准」的证据，与「值不值得切」无关，一律不进 state（官方明确 state 越杂准确率越低）。
唯一进入 state 的补充口径是显式 opt-in 的 `round_data['jev_extra']`。

**④ 无法确定的字段**整体省略该行**，绝不以 "unknown" 之名带一个数字。**
`build_state_text` 的每一行都只在**有值**时出现（`MATCH CONTEXT` 小节标题也只在至少一行有值时出现），
缺失的键统一收口到文件末的 UNKNOWN 清单。

> ⚠️ 这条是被**基线审计打回来**的，初版是错的：初版用 `re.search(r'\d+', round_key)`
> 把 `round-000105` 当成「第 105 回合」陈述给模型。但 `round_key` 不是回合号，而是
> **10 秒时间桶**（`lsc/analyzer/valorant_ocr_rounds.py:708-710`：
> `_round_key = f"round-{int(round(float(start) / 10.0)):06d}"`，仓库文档
> `docs/runtime-investigation-20260908.md:48` 同口径）。`round-000105` 实际约是录像
> **1050 秒（≈17.5 分钟）**处；28 分钟的录像会报到 1600+。后果比缺字段更糟：缺字段会安全地写 unknown，
> 而这个是**把错误数字当明文事实**陈述，模型会据此推理出「这是一个超长的第 105 回合」这种完全错误的局面。
>
> **修法**：`_jev_round_no()` 整个删除（现在 grep 该名字为 0 处）；`round_no` 只经
> 「上游**真有**该字段才透传」的路径进入 state（`_JEV_PASSTHROUGH_KEYS`），当前管线不产出它就
> **永久缺席**（宁缺勿错）。时间桶本身也不再进 prompt —— 与判决无关的文本只会稀释准确率；
> 它仍保留在 shadow JSONL 的 `round_key` 字段里供对账。
> 守卫：`test_round_key_is_never_interpreted_as_a_round_number`（断言 `"105" not in text`）、
`test_a_real_upstream_round_no_is_passed_through_when_it_exists`、
`test_state_omits_the_line_entirely_when_a_field_is_undetermined`。

### 3.2 措辞即测量口径

同一个五杀回合，父级实测：**字符串 state + 带归属/戏剧性说明**得 P(yes)=0.91，
**对象 state + 简短问法**得 P(yes)=0.66 —— 差异主要来自措辞而非事实。
因此 `QUESTIONS` / `_EXCITEMENT_LEVELS` / `_REASON_CRITERIA` 全部提成模块级常量，
**一旦定稿就不要在实验中途改字**：换了措辞等于换了测量口径，与现有管线的 A/B 结果不再可比。
本次实现一律用**字符串 state**（§7.1 的真跑也用字符串）。

---

## 4. 错误处理与安全

| 情况 | 行为 | 依据 |
| :-- | :-- | :-- |
| 无 `TYPESAFE_API_KEY` | `source="disabled"`，`error` 非空，**不发请求** | 单测 + 实测§7.1 |
| 401 / 403 | 视为**配置错误，不重试**，记 WARNING，`source="disabled"` | 父级实测（401 + `authentication_error` 错误体） |
| 429 / 5xx | 指数退避重试 ≤2 次（`_backoff` 尊重 `Retry-After`），仍失败则 `source="error"` | 单测；**真机未遇到 429**（见 §7.4） |
| 超时 | `error="timeout"`，不重试 | 单测 |
| 畸形响应 | 逐字段契约校验（`answers`/noul 范围/score/confidence），失败即 `source="error"` | 单测 10 例 |
| 任何其它异常 | 兜底 `except Exception` 吞掉，只返回错误判决 | 单测 4 例 |

**永不抛异常**：任何路径都折叠进返回值（单测 `test_judge_round_never_raises`）。

**key 不落日志**：key 只有一个读取点 `_read_api_key()`；所有日志/错误串出口过
`_safe()`（项目统一脱敏 `redact_text` + key 字面量整体替换）。
> 编写过程中单测**真的抓到了一条泄漏**：日志行当初直接传 `outcome.error` 而只对返回值脱敏，
> 代理回吐 `Authorization` 时 key 会进日志（`test_api_key_never_appears_in_logs_or_error_text`
> 的失败输出见 §7.2）。已修为一处加 `_safe()`。

**静态检查**：`python -m ruff check ...` → `All checks passed!`。
ruff 是 CI 阻塞项，编写过程中它抓到 4 处（UP041 / SIM117 / B905 / I001），**已全部修掉** ——
没有把红门禁留给下一个人。

**依赖**：只用**标准库** `urllib`。仓库依赖表（`requirements.txt` / `pyproject.toml`）
里没有 `requests`/`httpx`，虽然机器上装了，本项目不引入新依赖。

---

## 5. 成本估算公式

官方口径：**按 input token 计费，output token 免费**（官方 openapi 的 `Usage.output_tokens` 描述）。

```text
单次成本(USD) = input_tokens / 1_000_000 × 0.042
场次成本(USD) = 单次成本 × 回合数
回合数     ≈ 12 路 × 8 小时 × 每回合一次判决
```

代码里就是 `estimate_cost_usd()`，单价常量 `INPUT_USD_PER_MTOK = 0.042`。

| 口径 | 单次 input tokens | 单次成本 | 3840 次（12 路×8h） |
| :-- | :-- | :-- | :-- |
| 父级直连实测的 6 次均值 | ≈ 656 | ≈ $0.0000275 | ≈ **$0.11 / 场** |
| **本实现**的 13 次实测均值 | ≈ 1050 | ≈ $0.0000441 | ≈ **$0.17 / 场** |

本实现的 state 更长（显式归属 + 明文戏剧性 + UNKNOWN 清单，§3.1），单次贵约 1.6 倍，
换来的是「常规局失利」从误判 `multikill`（P=0.35）变成正确 `routine`（P=0.08，§7.1）。
**每场 $0.17 量级不构成决策约束**，但这个换算是真实测量值，不是估算。

---

## 6. 门控规则（live）

```python
def gate_out_reason(verdict, min_confidence=0.6) -> str:
    # 返回非空串 = 撤销自动导出资格（降级为 pending 待人工确认）
    if not verdict_is_usable(verdict):      # 无 key/超时/网络错/畸形
        return "" if LIVE_GATE_FAIL_OPEN else "jev_unavailable: ..."
    if verdict.p_clip < min_confidence:     return "jev_p_clip ..."
    if verdict.confidence < min_confidence: return "jev_confidence ..."
    return ""
```

两处共用**同一个** `min_confidence` 旋钮 —— 因为冻结配置里只有这一个键，
引入第二个未定义的阈值会让「为什么被门控」不再可解释。
**代价是真实的**：见 §8.2 的实测假阴性。原因串会自解释（例如
`"jev_confidence 0.080 < min_confidence 0.60"`），运营侧据此调键即可。

---

## 7. 实测证据：哪些真跑过，哪些只是单测覆盖

### 7.1 【真跑过】真 Jev 报文往返（13 次调用）

**通道说明（重要）**：本会话进程环境里**没有** `TYPESAFE_API_KEY`
（实测 `KEY_PRESENT=False`；父级是逐条命令内联注入的，子进程读不到）。
因此真调用经父级提供的**本地转发服务** `POST http://127.0.0.1:8795/decide` ——
该服务把同一份报文原样转给 `api.typesafe.ai`，返回体与官方逐字一致。
验证方式是只 monkeypatch `JEV_ENDPOINT` 常量，**解析层/判决逻辑/门控一行没改**。
**生产的交付路径仍然是直连** `https://api.typesafe.ai/v1/systemone`，`/decide` 未进入任何生产代码。

探活：

```text
PROBE: 200 {"model": "jev-1.13.0", "answers": {"q": {"type": "noul", "noul": 0.69}}, "usage": {"input_tokens": 272, "output_tokens": 20}}
```

**第一轮**（`_jev_round_no` 修掉之前，7 个用例）：

```text
case              P(clip)  excitereason        conf    ms  in_tok
----------------------------------------------------------------
ACE五杀                0.95    2.98  ace           0.98  1422    1028  src=jev
1v3残局翻盘              0.91    2.67  clutch        0.67   469    1068  src=jev
常规eco局               0.41    1.80  routine       0.44   391    1030  src=jev
常规局失利                0.07    0.01  routine       0.99   405    1030  src=jev
赛点决胜                 0.87    2.08  clutch        0.08   436    1070  src=jev
0.1秒拆包               0.27    0.48  routine       0.52   360    1066  src=jev
赛点+extra显式           0.90    2.23  clutch        0.23   375    1088  src=jev

median latency_ms = 405 | total input tokens = 7380
total cost USD = 0.00030996
```

**第二轮**（全部修完之后**复跑同一脚本**，即当前交付代码的真实输出）：

```text
case            P(clip)  excite  reason       conf    ms  in_tok
--------------------------------------------------------------
ACE_ace            0.96    2.98  ace          0.98  1202    1021
clutch_1v3         0.92    2.58  clutch       0.58   375    1061
routine_eco        0.41    1.78  routine      0.38   390    1023
we_lost_1v5        0.08    0.01  routine      0.99   407    1023
matchpoint         0.87    1.85  clutch       0.00   405    1062
defuse_0s1         0.31    0.74  routine      0.26   390    1058

median latency_ms = 405 | total input tokens = 6248
total cost USD = 0.00026242
avg input tokens/call = 1041

=== round_no sanity: time bucket must NOT leak ===
lines containing 105: (none)      # 输入 state 里带 round_key="round-000105"
```

最后那两行是**修正的直接证据**：同一个 `round_key="round-000105"` 现在**一个含 105 的行都不产生**
（修前会生成 `Round number in the match: 105.`）。

**归属修正再次确证**：`we_lost_1v5`（我方 1 杀 / 敌方 5 杀 / 我方全灭）两轮都判
`routine`、P≈0.07~0.08、confidence 0.99。
**假阴性也再次复现**：`clutch_1v3` 这次是 `confidence 0.58 < 0.60` 被门控掉
（P=0.92 明显值得切），说明 §8.2 那条不是偶发。

一条**原样**响应体（ACE 五杀，逐字照抄）：

```json
{"model": "jev-1.13.0", "answers": {"worth_clipping": {"type": "noul", "noul": 0.96}, "excitement": {"type": "score", "score": 2.98, "confidence": 0.98, "legend": {"0": "平淡", "1": "略有起伏", "2": "明显精彩", "3": "极高强度"}, "probabilities": {"0": 0.0, "1": 0.0, "2": 0.01, "3": 0.99}}, "tag": {"type": "choice", "choice": "ace", "confidence": 1.0, "probabilities": {"ace": 1.0, "matchpoint": 0.0, "routine": 0.0, "clutch": 0.0, "comeback": 0.0}}}, "usage": {"input_tokens": 1028, "output_tokens": 92}}
```

**这三条结论由此确证（不再是推断）**：

1. **中文 score 档位被正确使用**：`legend` 原样回显
   `{"0":"平淡","1":"略有起伏","2":"明显精彩","3":"极高强度"}`，ACE 得 score=2.98、P(档位3)=0.99。
   任务给定的中文 4 档可以直接用，不需要换英文标签。
2. **归属修正生效**：「常规局失利」（我方 1 杀 / 敌方 5 杀 / 我方全灭）判为
   `routine`、P=0.07、confidence=0.99。同一用例修前被误判 `multikill` P=0.35。
3. **耗时**：本通道中位 405ms（n=7），父级**直连** api.typesafe.ai 的 6 次中位 **1229ms**。
   两者都远小于 `timeout=8s`，但**以直连的 1229ms 为准**做容量规划（本地转发更快）。

门控实测（同一批判决，min_confidence=0.6）：

```text
ACE五杀            gated_out=False reason=''
1v3残局翻盘          gated_out=False reason=''
常规eco局           gated_out=True  reason='jev_p_clip 0.410 < min_confidence 0.60'
常规局失利           gated_out=True  reason='jev_p_clip 0.070 < min_confidence 0.60'
赛点决胜            gated_out=True  reason='jev_confidence 0.080 < min_confidence 0.60'
0.1秒拆包           gated_out=True  reason='jev_p_clip 0.270 < min_confidence 0.60'
```

**运行间波动（诚实披露）**：同样 6 个用例连跑两次，ACE 的 P 是 0.98 → 0.95 → 0.96，
「常规 eco 局」的 P 是 0.08 → 0.38 → 0.41。**判决不是确定性的**，
所以 shadow 与 A/B 必须用同一个 `QUESTIONS` 口径、并且样本量要够才有意义。

### 7.2 【真跑过】单测与既有守卫回归

```text
$ python -m pytest tests/test_jev_judge.py -q
105 passed in 0.52s

$ python -m pytest tests/test_continuous_analysis_guards.py -q
111 passed in 0.81s

$ python -m pytest tests/test_config.py tests/test_settings_cache_poisoning.py \
    tests/test_broadcast_export_gate_parity.py tests/test_clip_list_upsert.py \
    tests/test_finalize_decision_guards.py tests/test_continuous_finalization.py \
    tests/test_valorant_ocr_rounds.py tests/test_broadcast_mode_shadow.py -q
171 passed in 3.75s

$ python -m pytest tests/ -q          # 全仓
3 failed, 2349 passed, 3 warnings in 68.33s (0:01:08)
```

**那 3 个失败与本接入无关，是既有的 Windows/GBK 缺陷**（且没有任何一个在我改动的文件里）：
3 条都落在 `tests/test_verify_live_session_selfcheck.py`，根因是该测试的
`subprocess.run(..., capture_output=True, text=True)` **没有传 `encoding=`**，
于是 Python 用 locale 编码解码子进程的中文输出。独立复现（不碰我的任何代码）：

```text
preferred encoding: cp936
REPRODUCED FAILURE: TypeError 'NoneType' object is not subscriptable
--- explicit utf-8 encoding (the fix the test lacks) ---
returncode: 0
stdout tail: ['[self-test] 7/7 通过（合成场景已清理；L3_SELFTEST_KEEP=1 可保留）']

（同时 Python 自己报了）
UnicodeDecodeError: 'gbk' codec can't decode byte 0xa1 in position 113
  File "...\subprocess.py", line 1599, in _readerthread
```

即：reader 线程解码失败 ⇒ `proc.stdout` 为 `None` ⇒ 断言里的 `proc.stdout` 抛
`TypeError`。**修法是给那 3 个 `subprocess.run` 加 `encoding="utf-8"`**（一行），
但这不在本次接入的 diff 范围内，我**没有改**，只在此记录。
注意它正是任务环境约束里点名的「项目已踩过 GBK 坑」。

**「与本次接入无关」的判据（全部实测，不含推断之外的成分）**：
`git ls-tree -r --name-only HEAD` 实测该测试文件与被它调用的
`scripts/verify_live_session.py` **都在 HEAD 就存在**，而本接入的 3 个文件
（`lsc/analyzer/jev_judge.py`、`tests/test_jev_judge.py`、本报告）**都不在 HEAD**；
再加上上面那次不涉及任何本接入代码的独立复现（同一命令、同一报错、加了
`encoding="utf-8"` 就 7/7 通过），可以排除本次改动的因果关系。
（我**没有**去跑「HEAD 干净树上的这 3 条测试」——`git archive HEAD` 那一次尝试超时未完成，
所以不把它当成证据。）

`tests/test_jev_judge.py` 里**没有任何真实网络请求**：HTTP 出口 `_http_post` 全部被替身替换，
桩的返回值就是 §3 所列的官方契约形状，覆盖：无 key、超时、401/403、429（重试到上限 / 重试后成功）、
5xx 可重试与不可重试、预算内放弃重试、10 组畸形响应、tag 缺失降级、usage 缺失降级、
4 类异常不抛出、key 不入日志、confidence 门控、shadow 不改生效对象、
live 只降级不丢弃、live 单轮上限、源码级「影子不得 await」与「live 必须在 live 标志下」。

**单测抓到的真实缺陷（不是构造出来的）**：`test_api_key_never_appears_in_logs_or_error_text` 首次运行失败：

```text
>           assert REAL_KEY not in str(record.args)
E           assert 'ts_live_SUP...E_1234567890' not in '('http 500...567890"}',)'
E              'ts_live_SUPERSECRET_KEY_VALUE_1234567890' is contained here:
E                ('http 500: {"echo": "Bearer ts_live_SUPERSECRET_KEY_VALUE_1234567890"}',)
```

原因：日志行直接传了未脱敏的错误串。已修（日志出口也过 `_safe()`）。

### 7.3 【仅单测覆盖】**没有**真跑过的部分

以下全部只在 monkeypatch 下验证，**未**用真 Jev 复现：

- **shadow 与 live 的全部接线行为**（写 JSONL、不一致 WARNING、降级为 pending、
  单轮上限、判决异常 fail-open）—— 需要一条真直播流 + 真分析循环才能端到端跑。
- **429 / 5xx 退避重试与 `Retry-After`** —— 父级 200+ 次真调用**全部 200**，
  双方都没打到限流，所以状态码语义与 `Retry-After` 处理**没有实测依据**，是按官方文档的防御性实现。
- **401 语义**：由父级实测（401 + `authentication_error`），我这边只做了单测复核。
- **`_JEV_MAX_GATED_ROUNDS` 这条上限**是我加的保护性常量，无量测依据。

### 7.4 【完全未执行】

- **live 模式从未在真机开启过**，也没有任何真实录制会话跑过判决。
- **未做 A/B 对照**：没有「现有 OCR+规则 vs Jev」在同一批真实回合上的对照数据。
  §7.1 的 7 个用例是我构造的合成回合，**不是**真实录像里的回合。
- **未验证** `clip_queued` 降级载荷在前端的呈现（live 未开，该分支未走过）。

---

## 8. 已知问题与实测边界

### 8.1 【最重要】上游不产出回合级游戏语义字段 ⇒ live 目前不可用

`_jev_round_state()` 只能映射到 `room_id` / `platform` / `game` /
`mode` / `round_no`，以及「上游恰好有就透传」的字段。
**事实**：在 `lsc/analyzer/` 全目录 grep `kills_self|spike_planted|ace|clutch_1vN|alive_self`
**零命中**（唯一命中是 `valorant_ocr_rounds.py:110` 的注释，说明
`"clutch"/"ace"/"triple"` 是**解说高光回放的叠加字样**，明确被排除在判据之外）。

也就是说：**当前管线产不出**判决需要的 kills / spike / ace / clutch / 剩余时间。
判决模块会把它们如实列进 UNKNOWN 清单（§3.1②），于是模型只能从
`round_no` 和极少的上下文里猜 —— 这种判决**不足以驱动 live 门控**。

这是一条**上游数据缺口**，不是判决层缺陷：接线层写成了「有就透传」，
上游一旦补上这些字段，判决与门控**无需改动**即生效。
建议的落地路径：先在 OCR/FSM 产出回合时补齐
`kills_self/kills_enemy/deaths_self/round_time_left_sec/spike_planted`（这些在顶部条/回合结算画面上都有），
再谈开 live。

**在此之前的推荐状态是 `shadow`**，用来积累「有/无游戏语义字段时判决差多少」的对照。

### 8.2 【实测】confidence 门控会造成假阴性

实测（§7.1）：「赛点决胜」回合 `p_clip=0.87`（明显值得切）、但 score 档位的
`confidence=0.08`，于是在 `min_confidence=0.6` 下**被门控掉**；即使降到 0.4 仍被门控。
7 个用例里 score-confidence 的分布是 0.98 / 0.67 / 0.44 / 0.99 / 0.08 / 0.52 / 0.23 ——
中低值很常见，而 `p_clip` 的判别力明显更干净（0.95/0.91 vs 0.41/0.27/0.07）。

**后果是可控的，因为门控退化的是「待人工确认」而不是丢弃**（回合仍入列、仍可见）。
但如果要开 live，这一条必须先决策。三个选项：

1. 维持现状（保守，接受一定假阴性）；
2. 把 `highlight_judge_min_confidence` 调到 0.4~0.5 之间（仍会漏掉上面的赛点回合）；
3. 新增一个独立键（如 `highlight_judge_min_score_confidence`）把两个阈值拆开 ——
   本次**没有做**，因为冻结配置只定义了一个键，我不改接口。

### 8.3 【实测】`matchpoint` / `comeback` 理由标签实际上选中不了

因为冻结的 state 键里**没有**赛点/逆转标志，模型无从判断。我试过用 `extra` 显式喂
`{"match_point": "yes", "decides_the_match": "yes"}`：「赛点+extra显式」仍然选
`clutch`（choice 概率里 `matchpoint` 是 0.0）。
**推断**（未定论）：`tag` 的 instructions 写的是「由我方选手做了什么」，把模型引向动作类理由。
因此 `matchpoint`/`comeback` 目前是**装饰性标签**，
下结论时不要把它们当有效分类。要修得改措辞，而改措辞就是改测量口径（§3.2），需要重新做 A/B。

### 8.4 【重要】shadow 的对照基准本身很弱：`vision_confirmed` 不是「高光判定」

基线审计实测出一条对**解读 shadow 结果**很关键的事实：
`confirm_status == "vision_confirmed"` 与「视觉模型认定这是高光」**无关** ——
它只等价于 `end_by == "next_prep"`（回合被下一个准备阶段闭合），且回合的
`score` 是**硬编码 0.9**（`valorant_ocr_rounds.py:669`）。

因此：

- shadow 日志里的 `original_worth_clipping` 是**管线的自动导出资格**，
  不是人工标注、也不是任何模型的精彩度判断。它作为对照基准的正确解读是
  「当前管线愿不愿意自动导出」，而不是「这一回合到底值不值得切成高光」。
- `agree` 字段（判决与它是否一致）**不能**当作判决的准确率指标 ——
  一致不代表对，不一致也不代表错。要评估判决质量必须另做**人工标注集**，
  这也是 §7.4「未做 A/B」那条之所以是缺口的原因。
- 基线审计同时确认**现状管线 0 次网络调用**（纯本地 ONNX 5 分类 + OCR + FSM），
  本接入因此是**新增一层语义判断**，不是替换掉任何已有 ML 判定。

### 8.5 DNS 解析不受 socket 超时约束

`urllib` 的 `getaddrinfo` 不遵守 `urlopen(timeout=...)`，
极端情况下 `judge_round` 可能超出 `timeout` 预算。生产上由
`_ai_executor` 线程池隔离，不会阻塞事件循环；仅在 live 的**有界等待**路径上会体现为
比 `timeout` 略长的延迟。已在 `call_jev` 文档串里标注。

### 8.6 门控的时序窗口（live）

live 下先算出裁决、再入列，因此不存在「先放行后撤销」的窗口。
但若将来有路径改成 `defer_export=False`（立即启 FFmpeg），
**必须把判决放在入队之前**，否则会出现「已导出才发现该门控」。
当前的两条 `list_only=True` 路径没有这个问题。

---

## 9. 交付清单与工作区复核

| 文件 | 状态 | 说明 |
| :-- | :-- | :-- |
| `lsc/analyzer/jev_judge.py` | 新增 963 行 | 判决模块（纯新增，未改任何既有文件） |
| `python-backend/handlers/room_handler.py` | 改 | 接线块 + 门控钩子 + 调用点（最小 diff） |
| `python-backend/settings.json` | 改 | 两个新键（**该文件在 .gitignore 内**，不产生新的未跟踪文件） |
| `tests/test_jev_judge.py` | 新增 826 行 | 105 个用例，全绿 |
| `docs/reports/jev-integration.md` | 新增 | 本报告 |

**未改动**：`valorant_broadcast.py`、`valorant_ocr_rounds.py`、`valorant_plugin.py`、
`pipeline.py`、任何前端文件、任何 `.gitignore` 之外的仓库配置。

### 9.1 脏文件复核（189 个在途改动未被触碰）

安全约束要求复核脏文件数不变。**结论要说清口径**：

- 基线 `fix/debt-20260918` @ `dbeb707d`：`git status --porcelain` = **189** 行，
  已冻结快照到 `.runtime/jev-baseline-porcelain.txt`。
- 本次交付多出 **4 个新增未跟踪文件**，所以原始计数从 189 变成 **193**：
  其中 3 个是本接入的（`lsc/analyzer/jev_judge.py`、`tests/test_jev_judge.py`、本报告），
  第 4 个 `docs/reports/jev-baseline-audit.md` 属于**并行的基线审计子代理**，不是本接入产出。
  （"新文件必然改变 `--porcelain` 行数"这件事本身无法避免 —— 交付物就是新文件。）
- 真正要守住的是「**那 189 条一条没丢、一条没被改动**」。逐条复核方式：

```text
git status --porcelain | Sort-Object | Out-File .runtime/jev-final-porcelain.txt
Compare-Object (Get-Content .runtime/jev-baseline-porcelain.txt) ^
               (Get-Content .runtime/jev-final-porcelain.txt) ^
               -PassThru | Where-Object { $_ -notmatch 'jev_judge|lsc/analyzer/jev_judge.py|docs/reports/jev-integration.md' }
```

**实跑输出（收尾时执行）**：

```text
BASELINE_COUNT=189
FINAL_COUNT=193
--- 只在基线里出现（应为空 = 189 条一条没丢/没变）---
(none)
--- 只在当前里出现（应恰好是本次新增的 3 个文件）---
?? docs/reports/jev-baseline-audit.md
?? docs/reports/jev-integration.md
?? lsc/analyzer/jev_judge.py
?? tests/test_jev_judge.py
```

第一段 `(none)` 是**最关键的那条**：基线 189 条**一条没丢、一条状态没变**
（`Compare-Object` 的 `<=` 侧为空）。
多出来的 4 条全部是 `??`（新增未跟踪），其中 3 条是本接入的，
第 4 条 `docs/reports/jev-baseline-audit.md` 来自并行的基线审计子代理。

**允许范围内的两处既有脏文件改动**：`room_handler.py` 与 `settings.json`
本就在在途改动集合里；前者是任务要求的接线点（纯增量），后者被 gitignore 覆盖。
**未执行**任何 `git stash` / `git checkout --` / `git reset` / `git clean` / `git commit`。

---

## 10. 回滚方式

三层，任选其一即可，**都不需要回滚数据**：

1. **关开关（零代码改动，秒级）**：把 `"highlight_judge_mode"` 改回 `"off"`（或直接删掉这个键）。
   行为立刻回到接入前：不 import 判决模块、不写日志、不改任何决策。
   这是本接入的主回滚路径 —— 全部新增都是**默认关闭**的旁路。
2. **丢弃 live 的降级效果**：把 `_JEV_GATED_REASON_FIELD` 从回合数据里去掉即可让
   `_is_auto_exportable_valorant_round` 恢复原判据（该字段只在 live 下写入）。
3. **整支回滚**：`git checkout fix/debt-20260918` 并删除本分支。
   本分支只新增 2 个文件（`lsc/analyzer/jev_judge.py`、`tests/test_jev_judge.py`）
   + 1 份报告，`room_handler.py` 与 `settings.json` 的改动是纯增量的，
   可用 `git diff` 逐个还原。

**没有**数据库/持久化迁移，**没有**外部状态残留（除 shadow 日志这一个文件，
删掉 `data/jev_judge_shadow.jsonl` 即净）。

---

## §9 父 agent 裁定（2026-09-20，对 §8.2 三个选项的选择）

**决定：live 门控只使用 `p_clip` 一个信号，`confidence` 退出门控、降为遥测。**

依据（本轮 13 次真调用实测）：
- `noul` 应答**没有** confidence 字段，因此 `verdict.confidence` 只能取 **score 头（精彩度档位分布）** 的 confidence ——
  它衡量"精彩度分档有多尖"，与"值不值得切"**不是同一个问题**，拿它门控属类别错误。
- 该错误的实测后果是**误杀高价值局面**：`matchpoint` p_clip=0.87 但 confidence=0.00 → 被门控；
  `clutch_1v3` p_clip=0.92 但 confidence=0.58 → 被门控（< 0.60）。
- 而 `p_clip` 自身判别力干净（ACE 0.96 / clutch 0.92 / 常规局 0.08），不需要第二个旋钮。

改动：
- `lsc/analyzer/jev_judge.py` `gate_out_reason()`：删除 `confidence < threshold` 分支，文档写明理由与实测数据。
- `tests/test_jev_judge.py`：原 `test_gate_passes_only_when_both_signals_clear_the_threshold` 更名并改判为
  `test_gate_uses_p_clip_only`（低 confidence 不再门控）；新增回归
  `test_gate_does_not_kill_the_two_measured_high_p_clip_cases`。
- 复跑：`pytest tests/test_jev_judge.py` → **106 passed**；`pytest tests/test_continuous_analysis_guards.py` → **111 passed**；`ruff` 干净。

**接受你的 live 时序取舍**：你选的"下发 `vision_confirmed` 之前门控"（并发跑在 `_ai_executor`，最坏等 ≈1 次 timeout，
由 `_JEV_TIMEOUT_SEC` + `_JEV_MAX_GATED_ROUNDS=4` 封顶，不占事件循环/编排线程）比父级先前提议的"事后撤销"更合适——
后者需要重建 `clip_queued` 的 upsert 载荷与簿记，改动面远超最小 diff，且需前端配合验证。**按你的实现为准。**

**两条保持现状但必须留档的边界**（不阻塞交付，阻塞 live 上生产）：
1. `matchpoint` / `comeback` 标签实测选不中（即使 `extra` 显式喂 `{"match_point":"yes"}`，choice 概率里 matchpoint=0.0）
   → 二者是**装饰性标签**，不得当有效分类使用。
2. 判决**非确定性**：同输入连跑 ACE 的 P 为 0.98→0.95→0.96、eco 局 0.08→0.38→0.41 → 任何 A/B 必须同口径、足样本量。
3. live 上生产的真正前提仍是**数据**：当前 state 21 键只有 5–6 键有真值（`ocr_text` 从不落盘），
   实测 20 条真实回合 Jev 全部判 False、P 区间仅 0.150–0.190（跨度 0.04）——**它在盲判。**
   在补上回合级内容信号之前，推荐状态是 `shadow`，不是 `live`。
