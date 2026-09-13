# 出点偏早 / 切片丢失修复落地记录（2026-09-12）

> 触发：实盘「列表 7 条、草稿只有 3 条」——多条真实交战回合出点被提前 ~45s，或判
> `pending_lookahead` 后在收尾被扫成 `manual_review` / `NEVER_AUDITED`；另有真实回合被
> 起点门禁误杀后从前端列表删除。

## 1. 根因链（逐条实测可复现）

1. **OCR 把「交战尾段最后 45 秒」读成「下回合买枪/准备」**
   `_is_prep_timer(t) = 0 < t ≤ 45`；只要顶部锚点失效，帧标签就退回计时器相位。
   - 抽帧证据：买枪/装备界面顶中显示 `ROUND 5 0:03`（30s 买枪倒计时）⇒ 买枪阶段确实 ≤45s；
     但**同一录像 402s 的交战尾段读数 0:46、415s 0:33** ⇒ 回合时钟走到最后 45 秒同样是 ≤45s。
   - 后果：`351.312` 的回合被 `next_prep` 切在 **403.125**（= 计时器穿越 45s 处），
     真实结束 **449.875**（452s 帧 ROUND WIN，比分 4-2→5-2）⇒ 偏差 **46.75s ≈ 45s 判据**。
2. **视觉审计发现了却修不回来**
   `broadcast_next_prep_invalidated=True` 只把后视窗口从 +45s 拉到 +90s；而收尾/离线时
   `effective_lookahead` 被 **钉死在 45s**（`finalize or available_end is None` 短路）⇒
   `scan_end ≡ end+45` 且"继续扩展"条件恒成立 ⇒ **无限 pending**（离线复现：连跑 8 轮
   `scanned_end` 恒 448.125，真出点 449.875 在窗外一格）。
3. **起点门禁 1 帧抖动误杀**
   `new_start <= start + 1e-6` 要求候选起点那一帧正好是 combat；首帧若是低置信 `unknown`，
   onset 晚一个采样间隔 ⇒ 整条真实回合 `rejected_no_stable_combat_start` ⇒ 从前端列表删除。
   A/B：`start=230.187`（首帧 unknown 0.3565）→ 拒；`start=230.200`（首帧 combat 0.6391）→ 过。
4. **FrameProvider 覆盖率虚报（live 专用）**
   请求区间超出已写入媒体时，解出的帧少于请求区间，旧实现仍把**整段**登记为已覆盖 ⇒ 该区间
   永不再解码（现场 448–486 成永久空洞，真出点 449.875 永远看不见）。
5. **L1 内部边界自检被 2 帧低置信抖动骗过**（改动的连带问题）
   旧判据「终态游程后出现**单帧** combat = 重新开战」把回合结束转场里的
   `result 1081-1083 → combat 1084(0.554)/1085(0.648) → replay 1086+` 误判为跨回合 ⇒
   整条真实回合（990–1078）被 `rejected_interior_boundary` 丢掉。

## 2. 改动

| # | 文件 | 改动 | 性质 |
|---|---|---|---|
| A | `lsc/analyzer/valorant_ocr_rounds.py` | **删除「≤45s ⇒ 买枪/准备」的无条件推断**；新增 `_is_buy_phase_onset(prev_raw, value)`：只有**读数 ≤45s 且相对上一原始读数上跳 ≥20s**（新买枪阶段首帧）才判 prep，并在该买枪阶段内维持（`buy_phase_until`）。交战尾段是同一回合时钟连续下降（无上跳）⇒ 不再被误判 | 治本（用户要求删除的判据 + 保留买枪阶段识别） |
| A2 | 同上 | `_refine_boundary_ts(target="prep")` 只认中央准备横幅（旧实现还会拿 ≤45s 读数当命中，把假边界"确认"成 `end_confidence=0.95`） | 密扫不再加固假出点 |
| A3 | 同上 | WAIT/中段切入兜底：有效计时器被跳变保护置空时，用「原始交战钟游程（`cand_ts`）+ `_MIDSTREAM_STREAK` 帧」开局 | 补 A 的能力缺口 |
| A4 | 同上 | `post_settle_hold` 的两条 `timer_phase == "prep"` 释放分支保留（现在只在真买枪相位触发）；删除已死的 `strong_prep_signal` | 收敛 |
| B | `lsc/analyzer/valorant_broadcast.py` | 抽出 `_effective_lookahead_sec()`：**视觉已否决 OCR 出点时，收尾/离线也必须给足 90s** | 关键修复：收尾算得出真出点 |
| B2 | 同上 | `interior_round_boundary` 的**重新开战必须是稳定游程**（新增 `min_resume_frames`，默认 4 帧），单帧/2 帧低置信抖动不算跨回合 | 消除 L1 误杀 |
| C | 同上 | `START_GATE_ONSET_TOLERANCE_SEC = 2.5`：入点容差吸收首帧抖动；纯回放/非游戏窗口（无 combat 游程）与"先回放后交战"（偏差远超容差）仍拒 | 不再误杀真实回合 |
| D | `lsc/analyzer/frame_provider.py` | 覆盖率只登记**真正解出帧**的范围（空解不登记），缺口下次补抽 | 消除 live 永久空洞 |
| E | `valorant_broadcast.py` + `python-backend/handlers/room_handler.py` | 记录并转发 `broadcast_next_prep_invalidated(_reason)` / `broadcast_ocr_end_invalidated` | 可观测性 |

未改动：`_broadcast_gate_passed` / `_BROADCAST_VALID_END_BY` **不放宽**；未定稿/无证据切片照旧不入草稿。

## 3. 验证

### 3.1 单测
`python -m pytest -q` → **2012 passed**（新增/改写见下），ruff 仅剩既有 B007。

- `tests/test_valorant_ocr_rounds.py`
  - 新增 `test_buy_phase_onset_requires_upward_reset`（含 46→45→44 反例）
  - 新增 `test_round_closes_at_buy_onset_not_at_live_tail`（锚点 stale 后尾段 ≤45s 连续下降不得闭合）
  - 新增 `test_no_prep_phase_is_inferred_from_low_timer`
  - 改写 4 个用例：出点改由**中央准备横幅**驱动（旧用例依赖 ≤45s 计时器）
  - `test_refine_boundary_ts_respects_min_start`：新增"只有 ≤45s 读数（无横幅）必须返回 None"
  - 删除 `test_post_settle_recovers_prep_without_gap_after_settle_period`（被测分支已按用户要求删除），
    替换为 `test_post_settle_recovers_on_fresh_clock_without_gap`
- `tests/test_valorant_broadcast.py`
  - `test_start_gate_tolerates_one_sample_onset_jitter`（含两条"仍拒"反例）
  - `test_effective_lookahead_keeps_full_budget_when_ocr_end_vetoed` + 调用点接线守门
  - `test_interior_round_boundary_ignores_low_confidence_blip_after_result`、
    `test_interior_round_boundary_requires_stable_resume_run`
- `tests/test_frame_provider.py`
  - `test_frame_provider_partial_decode_is_not_marked_covered`（反向验证：恢复旧实现即红 ✓）

反向验证（临时还原旧实现后新用例变红）：起点门禁容差、后视预算、provider 覆盖率、L1 抖动 ✓

### 3.2 实盘回归（同一录像 `2026-09-12_11-27-02_至_11-46-56.mp4`，1199.7s）

改前 finalization：定稿 4（000009 / 000085 / 000099 / 000109）；3 条真实回合卡 `pending_lookahead`
（000035 `403.125`、000049 `539.828`、000062 `677.312`，均 `next_prep` + veto）；1 条被起点门禁误杀
（000023 `230.187`）。

改后（OCR + finalize 全片复算，`accept_v2`）：**7 条定稿 + 1 条 manual_review，0 条卡 pending_lookahead**。

| 候选（新 OCR） | 审计出点 | 状态 | 真实回合（独立验证：2s 相位采样 + 抽帧） |
|---|---|---|---|
| 000009 `93.2–134.6` | **122.85** | precise / passed | E2 94–118（end ≈123）✅ |
| 000014-s1 `284.6–434.6` | **312.85** | precise / passed | E3 的**尾段**（回合 230–330）⚠️ 起点偏晚 |
| 000014-s2 `434.6–485.8` | **452.75** | precise / passed | E4 的**尾段**（回合 348–450，真实 end 449.875）⚠️ 起点偏晚 |
| 000049 `485.8–624.0` | **589.25** | precise / passed | E5 486–586 ✅ |
| 000063-s0 `624–739.6` | **739.25** | precise / passed | E6 624–736 ✅ |
| 000085 `852–989.6` | **957.25** | precise / passed | E7 852–952（真实 end 954.75）✅ |
| 000099 `989.6–1085.0` | **1085.25** | precise / passed | E8 990–1076（真实 end ≈1078）✅ |
| 000112 `1113.8–1199.6` | 1199.6 | open_tail / coarse / manual_review | E9：停录时回合未完，末尾缺「出点之后」的证据 —— 结构性，非缺陷 |
| 000014-s0 `134.6–284.6` | — | rejected `long_or_invalid` | 回放+技术暂停段（正确拒绝） |
| 000063-s1 `774–852` | — | rejected `no_stable_combat_start` | 赛前非游戏段（正确拒绝） |

对比改前：**定稿 4（其中 000109 亦为 pending）→ 7**；卡 `pending_lookahead` **3 → 0**；
起点误杀 **1 → 0**。三条原本整条丢失的真实回合（E3/E4/E8）现在都进草稿。
出点误差全部落在审计既定口径内（真实 end +0.5~2.5s 的「ROUND WIN 首帧 + 结算尾巴」）。

## 4. 遗留（已知、未在本次修复内）

1. **合并候选导致 2 条切片起点偏晚**：OCR 在 `134.6` 开出一个「假回合」（回合间回放里
   读数 ≥85 被当成新回合满钟），该假回合直到 486.0（下一回合时钟）才闭合 ⇒
   `134.6–485.8`（351s）把 E3+E4 一起吞掉；`_expand_oversize_candidates` 按固定 150s 切块，
   于是 E3/E4 只以**尾段**（284.6–312.85 / 434.6–452.75）入稿，起点落在回合中段。
   - 机制：`post_settle_hold` 里「原始读数 ≥85 即视为新回合」无法区分「回放里重放的满钟」
     与「真新回合」；旧实现靠 ≤45s 的早出点“顺手”切开了这个假回合，删除后暴露。
   - 建议方向：① 合并候选改**内容感知切块**（在审计已有 samples 上按「稳定终态游程 →
     稳定 combat 游程」切，而非固定 150s）；② 或给 OCR 增加「回放段内的读数不作开局依据」
     （OCR 已有 A5 回放段标注可复用）。
2. L3 实盘校验器 `scripts/verify_live_session.py` 建议在下次真实会话后跑一遍，确认
   `no_finalized_clip_dropped` / `all_listed_have_terminal` 全绿。
3. 前端文案：可对 `broadcast_next_prep_invalidated` 显示"出点证据不足，正在按视觉证据重算"。

## 5. 具体改动文件

```
lsc/analyzer/valorant_ocr_rounds.py     # A/A2/A3/A4：买枪相位判据、密扫 prep、WAIT 兜底
lsc/analyzer/valorant_broadcast.py      # B/B2/C/E：后视预算、内部边界稳定游程、入点容差、可观测
lsc/analyzer/frame_provider.py          # D：覆盖率只记真解出的范围
python-backend/handlers/room_handler.py # E：转发否决标记（可观测性）
tests/test_valorant_ocr_rounds.py       # 新增 3 / 改写 5 / 删除 1
tests/test_valorant_broadcast.py        # 新增 5
tests/test_frame_provider.py            # 新增 1（反向验证）
docs/reports/early-end-fix-20260912.md  # 本文
```

- L3 实盘校验器 `scripts/verify_live_session.py` 建议在下次真实会话后跑一遍，确认
  `no_finalized_clip_dropped` / `all_listed_have_terminal` 全绿。
- 前端文案：可对 `broadcast_next_prep_invalidated` 显示"出点证据不足，正在按视觉证据重算"。
