# CS2 开箱精调与规则编辑器验收增强 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `feat/fps-round-rule-pack` 基线上，修齐试用/相位/关键词契约，把 CS2 选手+观战官方包做到可复现开箱验收，并补上草稿试读/短窗试跑与 checklist 失效。

**Architecture:** 先落地共享 `casefold` 匹配与 phase profile **对象直传**（消除试读绿/正式不中、JSON phase 静默回退 Valorant）；收紧 `validate_rule_pack` 作为 `draft_pack` 信任边界；试用接口收敛为三种互斥模式；试跑与持续分析共享 `_analysis_semaphore` 并双向 busy。产品骨架（复制→编辑→保存→启动）不变。

**Tech Stack:** Python 3.12, pytest, 既有 `fps_round` 解释器 / RapidOCR, React/TypeScript/Ant Design, WebSocket

**Spec:** [`docs/superpowers/specs/2026-07-16-cs2-oob-and-rule-editor-validation-design.md`](../specs/2026-07-16-cs2-oob-and-rule-editor-validation-design.md)

**Worktree / 分支:** 所有实现在 `D:\Project\直播切片多人\.worktrees\fps-round-rule-pack`（分支 `feat/fps-round-rule-pack`）。**禁止**在未对齐的 `main` 工作区当基线改规则包代码。

**已拍板常量（前后端对齐）：**

| 常量 | 值 |
|------|-----|
| `MIN_REGION_W` / `MIN_REGION_H` | `0.02` |
| `DURATION_MAX_SEC`（UI/校验上限） | `600.0` |
| 试跑默认窗 | `180` 或 `300` 秒（UI 可选）；封顶 `300` |
| 试跑服务端 `deadline_sec` | `min(300, 45 + window_sec * 1.5)`（整数秒） |
| 试跑客户端超时 | `deadline_sec * 1000 + 30000` ms |
| 试读客户端超时 | `30000` ms（已有，保持） |

---

## 文件结构

| 文件 | 职责 |
|------|------|
| **Create** `lsc/analyzer/keyword_match.py` | 共享 `text_matches_any(text, keywords) -> bool`（双侧 casefold） |
| **Modify** `lsc/analyzer/round_detector.py` | buy/result 匹配改用共享函数 |
| **Modify** `lsc/analyzer/phase_scheduler.py` | （若需）确保 `profile_from_mapping` 字段校验清晰 |
| **Modify** `lsc/analyzer/rule_pack.py` | C5 完整校验；区域**拒绝**越界/过小，不再 clamp 掩盖 |
| **Modify** `lsc/analyzer/rule_packs/builtin_cs2.json` | 写入 CS2 `phase` + 扩展双语词 + 精调框 |
| **Create** `lsc/analyzer/rule_packs/builtin_cs2_spectator.json` | 观战/赛事官方包 |
| **Modify** `python-backend/handlers/room_handler.py` | phase 直传；试用模式 A/B/C；`trial_scan_rounds`；双向 busy |
| **Modify** `lsc-electron/src/utils/wsRequest.ts` | （可选）导出试跑超时助手；或调用处显式传 timeout |
| **Modify** `lsc-electron/src/components/RulePackEditor/index.tsx` | draft 试用、命中色、试跑 UI、C6 失效 |
| **Create** `lsc-electron/src/components/RulePackEditor/TrialScanTimeline.tsx` | 短窗时间轴预览 |
| **Modify** `lsc-electron/src/pages/Workbench/index.tsx` | 启动前试跑入口；busy 错误文案 |
| **Create** `tests/test_keyword_match.py` | casefold 单测 |
| **Modify** `tests/test_rule_pack.py` | C5 负例 |
| **Create** `tests/test_trial_scan_contracts.py` | 模式/busy/响应字段（可 mock 扫描） |
| **Create** `tests/test_phase_profile_passthrough.py` | lookback 不回退 |
| **Create** `docs/superpowers/fixtures/cs2-samples/README.md` | 样例清单与标注格式 |
| **Create** `scripts/eval_cs2_rule_pack.py` | missed/merged/FP + MAE 报告 |

---

### Task 0: 基线对齐（阻断后续任务）

**Files:** 无代码；git / 文档

- [ ] **Step 1: 确认工作目录与分支**

```powershell
cd "D:\Project\直播切片多人\.worktrees\fps-round-rule-pack"
git status
git rev-parse --abbrev-ref HEAD
```

Expected: 分支名为 `feat/fps-round-rule-pack`。

- [ ] **Step 2: 与 main 对齐**

```powershell
git fetch origin
git merge origin/main
# 若冲突：优先保留本分支规则包实现，合入 main 的其它修复
```

Expected: 合并成功；`lsc/analyzer/rule_pack.py` 与 `builtin_cs2.json` 仍存在。

- [ ] **Step 3: 确认前置规格可引用**

```powershell
Test-Path "docs\superpowers\specs\2026-07-16-fps-round-rule-pack-design.md"
Test-Path "docs\superpowers\specs\2026-07-16-cs2-oob-and-rule-editor-validation-design.md"
```

Expected: 均为 `True`。若 CS2 设计稿只在 main 仓库根，从主仓复制进本 worktree 并提交。

- [ ] **Step 4: 跑既有规则包相关测试基线**

```powershell
$env:QT_QPA_PLATFORM="offscreen"
pytest tests/test_rule_pack.py tests/test_fps_round_interpreter.py tests/test_valorant_rule_pack_parity.py tests/test_analysis_rule_handlers.py -v --tb=short
```

Expected: 全部 PASS（记录数量，后续回归不得无故减少）。

- [ ] **Step 5: Commit（仅合并/同步文档时）**

```powershell
git add -A
git commit -m "chore: align feat/fps-round-rule-pack with main before CS2 plan"
```

若无变更可跳过 commit。

---

### Task 1: C3 — 共享 casefold 关键词匹配

**Files:**
- Create: `lsc/analyzer/keyword_match.py`
- Modify: `lsc/analyzer/round_detector.py`（约 1322–1340 行 buy/result 匹配）
- Create: `tests/test_keyword_match.py`
- Modify: `tests/test_round_detector.py`（若已有 OCR 关键词用例则追加；否则仅新文件）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_keyword_match.py
from lsc.analyzer.keyword_match import text_matches_any, classify_keywords


def test_casefold_matches_uppercase_result_phrase() -> None:
    assert text_matches_any(
        "COUNTER-TERRORISTS WIN",
        ["Counter-Terrorists Win", "victory"],
    )


def test_classify_can_hit_buy_and_result() -> None:
    cats = classify_keywords(
        "BUY TIME — TERRORISTS WIN",
        buy=["buy time"],
        result=["terrorists win"],
    )
    assert set(cats) == {"buy", "result"}


def test_empty_text_matches_nothing() -> None:
    assert text_matches_any("", ["WIN"]) is False
    assert classify_keywords("", buy=["buy"], result=["win"]) == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_keyword_match.py -v`  
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现共享模块**

```python
# lsc/analyzer/keyword_match.py
from __future__ import annotations

from typing import Iterable


def text_matches_any(text: str, keywords: Iterable[str]) -> bool:
    hay = (text or "").casefold()
    if not hay:
        return False
    for kw in keywords:
        needle = str(kw or "").casefold().strip()
        if needle and needle in hay:
            return True
    return False


def classify_keywords(
    text: str,
    *,
    buy: Iterable[str],
    result: Iterable[str],
) -> list[str]:
    cats: list[str] = []
    if text_matches_any(text, buy):
        cats.append("buy")
    if text_matches_any(text, result):
        cats.append("result")
    return cats
```

- [ ] **Step 4: 改 `round_detector.py` 正式扫描匹配**

将 buy / result 判定改为：

```python
from lsc.analyzer.keyword_match import text_matches_any

# buy 分支（有 buy_keywords 时）
is_buy = text_matches_any(current_text, buy_keywords)

# result 分支（end_keywords 已解析）
is_round_end = text_matches_any(current_text, end_keywords)
```

删除「只对 text `.lower()`、词表不转」的不对称逻辑。默认中文硬编码分支可保留，但比较时也走 `text_matches_any(current_text, [...])`。

- [ ] **Step 5: 跑测试确认通过**

```powershell
pytest tests/test_keyword_match.py tests/test_round_detector.py -v --tb=short -k "keyword or phase or ocr or buy or result" 
# 若筛选过窄，改为整文件：
pytest tests/test_keyword_match.py tests/test_round_detector.py -v --tb=short
```

Expected: PASS

- [ ] **Step 6: Commit**

```powershell
git add lsc/analyzer/keyword_match.py lsc/analyzer/round_detector.py tests/test_keyword_match.py
git commit -m "fix: casefold shared keyword matching for OCR buy/result"
```

---

### Task 2: C2 — Phase profile 对象直传

**Files:**
- Modify: `python-backend/handlers/room_handler.py`（`_continuous_scan_window_plan` 约 1710–1733；及所有 `get_profile(valorant_profile)` 扫描预算调用点）
- Create: `tests/test_phase_profile_passthrough.py`

- [ ] **Step 1: 定位所有二次 `get_profile` 扫描预算调用**

```powershell
rg "get_profile\(|_continuous_scan_window_plan|valorant_profile" python-backend/handlers/room_handler.py
```

记下所有在 `mode == "valorant_round"` 路径里用**名字**取 profile 的位置。

- [ ] **Step 2: 写失败测试**

```python
# tests/test_phase_profile_passthrough.py
from lsc.analyzer.phase_scheduler import profile_from_mapping, get_profile
from python_backend_helpers import continuous_scan_window  # 若无可直接测 handler 内纯函数


def test_pack_lookback_not_replaced_by_valorant_default() -> None:
    pack_phase = {
        "lookback_sec": 30.0,
        "buy_sleep_sec": 15.0,
        "buy_duration_sec": 20.0,
        "pre_combat_window_sec": 12.0,
        "post_combat_window_sec": 25.0,
        "ocr_sparse_interval_sec": 1.5,
        "ocr_dense_interval_sec": 0.8,
        "unknown_reanchor_sec": 30.0,
        "max_combat_force_post_sec": 140.0,
        "buy_duration_pistol_sec": 25.0,
        "buy_wake_early_sec": 6.0,
        "post_round_sec": 5.0,
        "intermission_enter_sec": 45.0,
        "intermission_max_sec": 120.0,
        "intermission_ocr_interval_sec": 2.0,
        "rms_trust_high": False,
    }
    profile = profile_from_mapping(pack_phase)
    assert profile.lookback_sec == 30.0
    # 名字二次查找必须证明会漂到 Valorant —— 用于文档化 bug
    assert get_profile(getattr(profile, "name", "pack")).lookback_sec != 30.0 or getattr(profile, "name", "") == "pack"
```

若 `profile.name` 为 `"pack"` 且 `get_profile("pack")` 回退 Valorant（lookback 120），测试应断言：**扫描预算函数在传入 profile 对象时 lookback 仍为 30**。

实现测试时优先 **直接 import** `room_handler` 内被抽出的纯函数。若 `_continuous_scan_window_plan` 签名仍吃 `valorant_profile: str`，本 Task Step 3 改为吃 `phase_profile` 对象（或同时支持对象优先）。

推荐新签名：

```python
def _continuous_scan_window_plan(
    ...,
    phase_profile=None,  # ValorantProfile | None
    valorant_profile: str | None = None,  # 仅无 phase_profile 时兼容
):
    ...
    if mode == "valorant_round" and round_phase is not None:
        from lsc.analyzer.phase_scheduler import RoundPhase, get_profile, scan_budget_for_phase
        cfg = phase_profile if phase_profile is not None else get_profile(valorant_profile)
        ...
```

所有 continuous loop 调用处改为：

```python
profile = _continuous_valorant_phase_profile(state)
scan_range, use_ocr, timeout, full_rescan = _continuous_scan_window_plan(
    ...,
    phase_profile=profile,
)
```

- [ ] **Step 3: 实现直传并跑测试**

```powershell
pytest tests/test_phase_profile_passthrough.py tests/test_phase_scheduler.py -v --tb=short
```

Expected: PASS；构造 lookback=30 的包时预算 lookback=30。

- [ ] **Step 4: Commit**

```powershell
git add python-backend/handlers/room_handler.py tests/test_phase_profile_passthrough.py
git commit -m "fix: pass phase profile object into scan budget (no name fallback)"
```

---

### Task 3: C5 — `validate_rule_pack` 信任边界

**Files:**
- Modify: `lsc/analyzer/rule_pack.py`
- Modify: `tests/test_rule_pack.py`

- [ ] **Step 1: 写失败测试（当前错误地通过则先红）**

```python
# tests/test_rule_pack.py 追加
import math
import pytest
from lsc.analyzer.rule_pack import RulePackError, validate_rule_pack


def _minimal_ok(**overrides):
    base = {
        "id": "user:test",
        "name": "t",
        "template": "fps_round",
        "ocr_regions": {"status": {"x": 0.3, "y": 0.01, "w": 0.4, "h": 0.06}},
        "keywords": {"buy": ["BUY"], "combat": [], "result": ["WIN"]},
        "duration": {"min_sec": 20, "max_sec": 180},
        "trim": {"start_pad_sec": 0.5, "end_pad_sec": 1.5},
        "confirm": {"require_ocr_bounds": True, "end_by": ["ocr_result", "next_buy"]},
    }
    base.update(overrides)
    return base


def test_rejects_zero_area_status_without_clamp_rescue() -> None:
    with pytest.raises(RulePackError):
        validate_rule_pack(_minimal_ok(
            ocr_regions={"status": {"x": 1.0, "y": 1.0, "w": 0.0, "h": 0.0}},
        ))


def test_rejects_negative_duration() -> None:
    with pytest.raises(RulePackError):
        validate_rule_pack(_minimal_ok(duration={"min_sec": -10, "max_sec": -1}))


def test_rejects_nan_duration() -> None:
    with pytest.raises(RulePackError):
        validate_rule_pack(_minimal_ok(duration={"min_sec": math.nan, "max_sec": 100}))


def test_rejects_string_combat_keywords() -> None:
    with pytest.raises(RulePackError, match="列表"):
        validate_rule_pack(_minimal_ok(keywords={"buy": ["BUY"], "combat": "abc", "result": ["WIN"]}))


def test_rejects_string_end_by() -> None:
    with pytest.raises(RulePackError, match="列表"):
        raw = _minimal_ok()
        raw["confirm"] = {"require_ocr_bounds": True, "end_by": "ocr_result"}
        validate_rule_pack(raw)


def test_rejects_non_numeric_phase_lookback() -> None:
    with pytest.raises(RulePackError):
        validate_rule_pack(_minimal_ok(phase={"lookback_sec": "not-a-number"}))
```

- [ ] **Step 2: 跑测试确认失败（至少一个当前会绿→应变红）**

```powershell
pytest tests/test_rule_pack.py::test_rejects_zero_area_status_without_clamp_rescue tests/test_rule_pack.py::test_rejects_string_combat_keywords -v
```

- [ ] **Step 3: 改 `normalize_region` / `validate_rule_pack`**

要点：

1. 新增 `_finite(name, value) -> float`：非有限数则 `RulePackError`。
2. `normalize_region` **改为严格校验**（或拆 `parse_region_strict`）：  
   - 四值有限；`w >= MIN_REGION_W`；`h >= MIN_REGION_H`；`x >= 0`；`y >= 0`；`x + w <= 1 + 1e-9`；`y + h <= 1 + 1e-9`。  
   - **禁止**先 clamp 再返回来「救活」非法框。
3. `keywords.combat` 必须 `isinstance(..., list)`（与 buy/result 一致）；`confirm.end_by` 必须 list。
4. `0 < min_sec < max_sec <= DURATION_MAX_SEC`（`DURATION_MAX_SEC = 600.0`）。
5. trim pads `_finite` 且 `>= 0`。
6. 若有 `phase`：对已知字段 `_finite`，并 `profile_from_mapping(phase)`；构造异常则 `RulePackError`。

```python
MIN_REGION_W = 0.02
MIN_REGION_H = 0.02
DURATION_MAX_SEC = 600.0
```

- [ ] **Step 4: 跑全文件测试**

```powershell
pytest tests/test_rule_pack.py -v --tb=short
```

Expected: PASS；更新任何依赖「clamp 救活」的旧测试。

- [ ] **Step 5: Commit**

```powershell
git add lsc/analyzer/rule_pack.py tests/test_rule_pack.py
git commit -m "fix: harden validate_rule_pack as draft_pack trust boundary"
```

---

### Task 4: CS2 官方包（选手 + 观战）

**Files:**
- Modify: `lsc/analyzer/rule_packs/builtin_cs2.json`
- Create: `lsc/analyzer/rule_packs/builtin_cs2_spectator.json`
- Modify: `lsc/analyzer/rule_packs/builtin_cs2.README.md`（说明选手/观战选用）
- Modify: `tests/test_rule_pack.py`（加载两包通过校验）

- [ ] **Step 1: 写入选手包 phase + 扩展词（初值，样例标定前可调）**

`builtin_cs2.json` 在现有字段上增加与 Valorant 同结构的 `phase`，初值示例（后续 Task 8 用样例再精调）：

```json
"phase": {
  "buy_sleep_sec": 15.0,
  "pre_combat_window_sec": 12.0,
  "post_combat_window_sec": 30.0,
  "rms_trust_high": false,
  "ocr_sparse_interval_sec": 1.5,
  "ocr_dense_interval_sec": 0.8,
  "unknown_reanchor_sec": 30.0,
  "max_combat_force_post_sec": 160.0,
  "lookback_sec": 90.0,
  "buy_duration_sec": 20.0,
  "buy_duration_pistol_sec": 25.0,
  "buy_wake_early_sec": 6.0,
  "post_round_sec": 7.0,
  "intermission_enter_sec": 60.0,
  "intermission_max_sec": 150.0,
  "intermission_ocr_interval_sec": 2.0
}
```

扩展 `keywords.buy` / `result`（中英，大小写随意，匹配靠 C3），例如加入：`Freeze Time`, `FREEZE TIME`, `暂停`, `Counter-Terrorists Win`, `Terrorists Win`, `CT WIN`, `T WIN` 等。

- [ ] **Step 2: 创建 `builtin:cs2_spectator`**

```json
{
  "id": "builtin:cs2_spectator",
  "name": "CS2 观战/赛事",
  "template": "fps_round",
  "ocr_regions": {
    "status": {"x": 0.25, "y": 0.00, "w": 0.50, "h": 0.08},
    "killfeed": null
  },
  "keywords": { "...与选手包共享同一双语列表..." },
  "duration": {"min_sec": 15, "max_sec": 200},
  "trim": {"start_pad_sec": 0.5, "end_pad_sec": 1.5},
  "confirm": {
    "require_ocr_bounds": true,
    "start_by": "ocr_buy_exit",
    "end_by": ["ocr_result", "next_buy"]
  },
  "phase": { "...可与选手相同初值，框不同..." }
}
```

状态框初值可先占位；**必须**能通过 `validate_rule_pack`；真实坐标在 Task 8 用样例改。

- [ ] **Step 3: 测试加载**

```python
def test_load_builtin_cs2_and_spectator() -> None:
    from lsc.analyzer.rule_pack import load_rule_pack
    p = load_rule_pack("builtin:cs2")
    assert "phase" in p
    assert p["phase"]["buy_duration_sec"] == 20.0
    s = load_rule_pack("builtin:cs2_spectator")
    assert s["id"] == "builtin:cs2_spectator"
```

```powershell
pytest tests/test_rule_pack.py::test_load_builtin_cs2_and_spectator -v
```

- [ ] **Step 4: Commit**

```powershell
git add lsc/analyzer/rule_packs/ tests/test_rule_pack.py
git commit -m "feat: add CS2 phase pack and builtin cs2_spectator"
```

---

### Task 5: C1 — 试用三种互斥模式 + 试读分类

**Files:**
- Modify: `python-backend/handlers/room_handler.py`（`_trial_ocr_region_impl`、`handle_trial_ocr_region`）
- Modify: `lsc-electron/src/components/RulePackEditor/index.tsx`
- Modify: `tests/test_analysis_rule_handlers.py`

- [ ] **Step 1: 后端解析辅助（写测试驱动）**

```python
# 可放在 room_handler 或 rule_pack 旁的纯函数，便于单测
def resolve_trial_ocr_mode(data: dict) -> str:
    """返回 'draft' | 'saved' | 'legacy'。"""
    if data.get("draft_pack"):
        return "draft"
    if data.get("rule_pack_id"):
        return "saved"
    if data.get("region") is not None:
        return "legacy"
    raise RulePackError("trial_ocr_region 需要 draft_pack、rule_pack_id 或 region")
```

- [ ] **Step 2: 实现 `_trial_ocr_region_impl` 新语义**

伪代码：

```python
def _trial_ocr_region_impl(...):
    mode = resolve_trial_ocr_mode(payload)
    if mode == "draft":
        pack = validate_rule_pack(payload["draft_pack"])  # 不保存
        region = pack["ocr_regions"]["status"]
        buy, result = pack["keywords"]["buy"], pack["keywords"]["result"]
        classified = True
    elif mode == "saved":
        pack = load_rule_pack(payload["rule_pack_id"])
        region = pack["ocr_regions"]["status"]
        buy, result = pack["keywords"]["buy"], pack["keywords"]["result"]
        classified = True
    else:
        region = normalize_region / parse_region_strict(payload["region"])
        classified = False

    # OCR；依赖缺失 / FFmpeg 失败 → raise → handler 返回 success:false
    texts, raw = ...
    joined = " ".join(texts)
    if not classified:
        return {"texts": texts, "raw": raw, "classified": False}

    from lsc.analyzer.keyword_match import classify_keywords
    cats = classify_keywords(joined, buy=buy, result=result)
    if cats:
        status = "hit"
    elif texts:
        status = "partial"
    else:
        status = "empty"
    return {
        "texts": texts,
        "raw": raw,
        "classified": True,
        "matched_categories": cats,
        "status": status,
    }
```

- [ ] **Step 3: Handler 测试**

覆盖：draft 改词未保存即 hit；legacy 无 `matched_categories`；OCR 依赖失败 → `success: false`（mock）。

```powershell
pytest tests/test_analysis_rule_handlers.py -v --tb=short -k trial
```

- [ ] **Step 4: 前端编辑器**

`handleTrialOcr` 发送：

```typescript
await sendRequest(ws, 'trial_ocr_region', {
  frame_jpeg_base64: frame,
  draft_pack: currentDraftPack, // 含 ocr_regions + keywords + …
}, 30000)
```

根据 `status` 显示绿/黄/红；`success===false` 显示 error toast，不设为 empty。

- [ ] **Step 5: Commit**

```powershell
git add python-backend/handlers/room_handler.py lsc-electron/src/components/RulePackEditor/index.tsx tests/test_analysis_rule_handlers.py
git commit -m "feat: trial OCR modes draft/saved/legacy with hit classification"
```

---

### Task 6: C4 — `trial_scan_rounds` + 双向 busy

**Files:**
- Modify: `python-backend/handlers/room_handler.py`
- Create: `tests/test_trial_scan_contracts.py`
- Create: `lsc-electron/src/components/RulePackEditor/TrialScanTimeline.tsx`
- Modify: `lsc-electron/src/components/RulePackEditor/index.tsx`
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`（启动前入口）

- [ ] **Step 1: 模块级 busy 标志**

在 `room_handler.py` 顶部与 `_analysis_semaphore` 旁：

```python
_trial_scan_active: bool = False
```

辅助：

```python
def _reject_if_analysis_busy():
    # 若已有 continuous task running → error_code analysis_busy
    ...

def _reject_if_trial_scan_busy():
    if _trial_scan_active:
        return {"success": False, "error": "试跑进行中，请稍候", "error_code": "trial_scan_busy"}
```

- [ ] **Step 2: `handle_start_continuous_analysis` 开头**

若 `_trial_scan_active`：立即返回 `trial_scan_busy`，**不要**启动 worker。

- [ ] **Step 3: `handle_trial_scan_rounds`**

```python
async def handle_trial_scan_rounds(ws, data):
    # 1) 拒 analysis_busy / 重复 trial_scan_busy
    # 2) 解析 draft_pack 优先 else rule_pack_id → validate/load
    # 3) 解析 room、last_n_sec in {180,300}，cap 300
    # 4) 检查录制文件可 seek；否则 recording_file_not_seekable
    # 5) window_end = recording_duration; window_start = max(0, end - last_n)
    # 6) deadline = min(300, int(45 + last_n * 1.5))
    # 7) async with _analysis_semaphore:
    #       global _trial_scan_active = True
    #       try:
    #           fut = loop.run_in_executor(_ai_executor, lambda: scan_fps_rounds(...))
    #           rounds = await asyncio.wait_for(fut, timeout=deadline)
    #       finally:
    #           _trial_scan_active = False
    #    注意：wait_for 超时必须取消 fut 并确保 executor 工作结束前不提前清 busy
    #    推荐模式：不在外层 wait_for 释放语义上「假超时」——
    #    使用 asyncio.timeout(deadline) 包住整段 async with，或：
    #    超时后仍 await fut（不释放 semaphore）直到完成，再返回 timeout 错误。
    # 8) 返回 success、rounds、window_start_sec、window_end_sec、recording_duration_sec
```

**Semaphore 生命周期（强制）：** `async with _analysis_semaphore` 的退出只能发生在 executor 内 `scan_fps_rounds` **返回或抛错之后**。若客户端已断开，仍等任务结束再清 `_trial_scan_active`。

- [ ] **Step 4: 后端测试**

```python
@pytest.mark.asyncio
async def test_start_rejected_while_trial_scan_active(monkeypatch):
    # 设 _trial_scan_active True → start_continuous_analysis 失败 trial_scan_busy
    ...

@pytest.mark.asyncio
async def test_trial_scan_rejected_while_analysis_running(monkeypatch):
    ...

def test_trial_scan_response_has_window_fields():
    # mock scan_fps_rounds → 断言响应键
    ...
```

```powershell
pytest tests/test_trial_scan_contracts.py -v --tb=short
```

- [ ] **Step 5: 前端试跑**

```typescript
const lastN = 180 | 300
const deadlineSec = Math.min(300, Math.floor(45 + lastN * 1.5))
const timeoutMs = deadlineSec * 1000 + 30_000
const res = await sendRequest(ws, 'trial_scan_rounds', {
  draft_pack: currentDraftPack,
  room_id,
  last_n_sec: lastN,
}, timeoutMs)
```

`TrialScanTimeline`：用 `window_start_sec` / `window_end_sec` / `recording_duration_sec` 画轴；列出 rounds。  
文案：「预览本窗口可能切出的回合（不等同正式持续分析的全部节奏）」。  
busy / timeout：提示稍候再试。

Workbench 启动前按钮同样调用（已保存包可用 `rule_pack_id` 模式 B）。

- [ ] **Step 6: Commit**

```powershell
git add python-backend/handlers/room_handler.py tests/test_trial_scan_contracts.py lsc-electron/src/components/RulePackEditor/ lsc-electron/src/pages/Workbench/index.tsx
git commit -m "feat: trial_scan_rounds with bidirectional analysis busy gate"
```

---

### Task 7: C6 — Checklist 结果失效

**Files:**
- Modify: `lsc-electron/src/components/RulePackEditor/index.tsx`

- [ ] **Step 1: 状态字段**

```typescript
const [trialOcrResult, setTrialOcrResult] = useState<...>(null)
const [trialScanResult, setTrialScanResult] = useState<...>(null)
```

- [ ] **Step 2: 在 `updateEditing` 内失效**

```typescript
const updateEditing = (patch: Partial<AnalysisRulePack>) => {
  setEditing(prev => {
    const next = { ...prev, ...patch }
    const regionChanged = patch.ocr_regions !== undefined
    const keywordsChanged = patch.keywords !== undefined
    const durationChanged = patch.duration !== undefined
    const trimChanged = patch.trim !== undefined
    if (regionChanged || keywordsChanged) {
      setTrialOcrResult(null)
      setTrialScanResult(null)
    } else if (durationChanged || trimChanged) {
      setTrialScanResult(null)
    }
    return next
  })
}
```

换帧、换包时同样清空两者。改试跑 `last_n_sec` / `room_id` 时清空试跑结果。

Checklist 勾选：仅当对应 result 非 null 且满足条件。

- [ ] **Step 3: 手动或轻量组件测（可选）**

无强制前端单测框架时：在 PR 描述写手动步骤（改词 → 绿勾消失 → 再试读）。

- [ ] **Step 4: Commit**

```powershell
git add lsc-electron/src/components/RulePackEditor/index.tsx
git commit -m "fix: invalidate trial OCR/scan checklist on draft edits"
```

---

### Task 8: 样例清单、评估脚本与开箱精调

**Files:**
- Create: `docs/superpowers/fixtures/cs2-samples/README.md`
- Create: `docs/superpowers/fixtures/cs2-samples/manifest.json`（可先无大视频，只定 schema）
- Create: `scripts/eval_cs2_rule_pack.py`
- Modify: `builtin_cs2.json` / `builtin_cs2_spectator.json`（按样例精调）

- [ ] **Step 1: manifest schema**

```json
{
  "samples": [
    {
      "id": "cs2_player_en_01",
      "pack_id": "builtin:cs2",
      "video": "LOCAL_OR_URL",
      "hud": "player",
      "lang": "en",
      "expected_rounds": [{"start": 12.0, "end": 68.5}]
    },
    {
      "id": "cs2_spec_zh_01",
      "pack_id": "builtin:cs2_spectator",
      "hud": "spectator",
      "lang": "zh",
      "expected_rounds": [{"start": 5.0, "end": 55.0}]
    }
  ]
}
```

README 写清：大文件不进 git 时如何放置路径 / 环境变量 `LSC_CS2_SAMPLES_ROOT`。

- [ ] **Step 2: 评估脚本**

`scripts/eval_cs2_rule_pack.py`：

- 加载 pack + 视频 + expected  
- `scan_fps_rounds`  
- 用 IoU/中点匹配统计 `missed` / `merged` / `false_positive`  
- 对匹配对算起止 MAE  
- 对观战样例额外跑一遍 `builtin:cs2` 对比：断言 spectator 的 `missed+merged+false_positive` **严格更低**（若样例未就绪则脚本 `--dry-schema` 只校验 manifest）

- [ ] **Step 3: 有样例后精调 JSON 框/词/phase，重跑脚本直到选手 MAE≤1.0s 或记录差距**

- [ ] **Step 4: Commit**

```powershell
git add docs/superpowers/fixtures/cs2-samples scripts/eval_cs2_rule_pack.py lsc/analyzer/rule_packs/
git commit -m "test: add CS2 sample manifest schema and eval script"
```

---

### Task 9: 回归与收尾

**Files:** 测试与文档状态

- [ ] **Step 1: 全量相关 pytest**

```powershell
$env:QT_QPA_PLATFORM="offscreen"
pytest tests/test_keyword_match.py tests/test_rule_pack.py tests/test_phase_profile_passthrough.py tests/test_trial_scan_contracts.py tests/test_fps_round_interpreter.py tests/test_valorant_rule_pack_parity.py tests/test_analysis_rule_handlers.py tests/test_continuous_analysis_guards.py tests/test_phase_scheduler.py -v --tb=short
```

Expected: 全部 PASS。

- [ ] **Step 2: 前端类型检查**

```powershell
cd lsc-electron
npx tsc --noEmit
```

Expected: 无 error。

- [ ] **Step 3: 更新设计稿状态行（可选）**

将 spec 状态改为「实施中/已落地」仅在功能合并后；本 Task 可先在 plan 顶部勾选完成项。

- [ ] **Step 4: Commit**

```powershell
git commit --allow-empty -m "chore: CS2 OOB plan regression checkpoint"
```

（若已有未提交修复则正常 commit，勿空提交。）

---

## Spec 覆盖自检

| Spec 项 | Task |
|---------|------|
| 基线 `feat/fps-round-rule-pack` | 0 |
| C3 casefold | 1 |
| C2 profile 直传 + 试跑非状态机回放 | 2, 6 文案 |
| C5 完整 validate | 3 |
| CS2 + spectator 包 | 4, 8 |
| C1 三模式 + matched_categories + empty≠error | 5 |
| C4 双向 busy + semaphore + 超时拍板 + 窗口字段 | 6 |
| C6 checklist 失效 | 7 |
| 样例指标 missed/merged/FP/MAE | 8 |
| 无畏回归 | 9 |
| 不做 audio.* / 不做向导 | 未建对应 Task（刻意） |

## Placeholder 扫描

计划中超时、最小框、duration 上限、试跑窗均已数值化；无 TBD 实施步骤。

---

## 执行说明

完成计划后，在 worktree 内按 Task 0 → 9 顺序执行。推荐 **subagent-driven-development**（每 Task 新代理 + 复盘），或本会话 **executing-plans** 批量推进。
