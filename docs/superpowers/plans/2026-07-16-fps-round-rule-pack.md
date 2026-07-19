# FPS 回合规则包 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把无畏契约持续分析策略抽成声明式 `fps_round` 规则包 + 解释器；Valorant 对等迁入后，支持 CS2 内置包与用户「复制→拖框→改词」本地包。

**Architecture:** 新增纯数据/校验模块 `rule_pack.py` 与薄解释器 `fps_round_interpreter.py`；OCR 状态区 crop/关键词改为可注入参数；continuous worker 按 `rule_pack_id` 加载包。前端在持续分析启动前选包，并提供规则编辑（拖框 + OCR 试读）。产品链路（pending→确认→手动导出）不变。

**Tech Stack:** Python 3.12, pytest, RapidOCR（既有）, React/TypeScript/Ant Design, WebSocket, Canvas 拖框

**Spec:** `docs/superpowers/specs/2026-07-16-fps-round-rule-pack-design.md`

---

## 文件结构

| 文件 | 职责 |
|------|------|
| **Create** `lsc/analyzer/rule_pack.py` | Schema 校验、加载官方/用户包、复制、原子保存 |
| **Create** `lsc/analyzer/rule_packs/builtin_valorant.json` | 从现网常量导出的官方包 |
| **Create** `lsc/analyzer/rule_packs/builtin_cs2.json` | CS2 默认框+词 |
| **Create** `lsc/analyzer/fps_round_interpreter.py` | 包 → `ValorantRoundConfig` / OCR 覆盖 / `ValorantProfile`；调用 `detect_valorant_rounds` |
| **Create** `tests/test_rule_pack.py` | 加载/校验/复制/缺状态区拒绝 |
| **Create** `tests/test_fps_round_interpreter.py` | 包映射与扫描参数注入 |
| **Create** `tests/test_valorant_rule_pack_parity.py` | 无畏对等（合成/既有 fixture） |
| **Modify** `lsc/analyzer/round_detector.py` | OCR 状态路径接受 `status_crop` / `buy_keywords` / `result_keywords` |
| **Modify** `lsc/analyzer/phase_scheduler.py` | 增加 `profile_from_mapping(dict) -> ValorantProfile` |
| **Modify** `python-backend/handlers/room_handler.py` | `rule_pack_id` 启动/状态；`round_key` 加前缀；规则 CRUD + OCR 试读；worker 走解释器 |
| **Modify** `python-backend/persistence.py` | 可选：导出 `ANALYSIS_RULES_DIR = DEFAULT_DATA_DIR / "analysis_rules"`（或放 `rule_pack.py` 内） |
| **Modify** `lsc-electron/src/types/index.ts` | RulePack 类型与 continuous 状态字段 |
| **Modify** `lsc-electron/src/pages/Workbench/index.tsx` | 启动选包；管理规则入口 |
| **Create** `lsc-electron/src/components/RulePackEditor/` | 列表、编辑表单、拖框画布、试读结果 |
| **Modify** `lsc-electron/src/components/AnalysisProgress.tsx` | 显示规则包名 |
| **Modify** `tests/test_continuous_analysis_guards.py` | 兼容映射与 status 字段 |

**命名约定：**

- 官方 id：`builtin:valorant`、`builtin:cs2`
- 用户 id：`user:<uuid>`（复制时生成）
- 模板：仅 `"fps_round"`
- `round_key`：`{rule_pack_id}|round-{quantized_start}`（`|` 替换包 id 内非法字符时用 `_` 规范化，见 Task 7）

**执行分期（可独立合并）：**

- Phase A：Task 1–6（后端包 + 解释器 + parity，尚不切流）
- Phase B：Task 7–8（handler 切流）
- Phase C：Task 9–12（CRUD、试读、前端、CS2）
- Phase D：Task 13（清理）

---

### Task 1: Rule Pack 模型与校验

**Files:**
- Create: `lsc/analyzer/rule_pack.py`
- Create: `tests/test_rule_pack.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_rule_pack.py
import pytest
from lsc.analyzer.rule_pack import (
    RulePackError,
    validate_rule_pack,
    normalize_region,
)


def test_validate_rejects_missing_status_region() -> None:
    raw = {
        "id": "user:x",
        "name": "坏包",
        "template": "fps_round",
        "ocr_regions": {},
        "keywords": {"buy": ["买入"], "combat": [], "result": ["胜利"]},
        "duration": {"min_sec": 20, "max_sec": 180},
        "trim": {"start_pad_sec": 0.5, "end_pad_sec": 1.5},
        "confirm": {"require_ocr_bounds": True},
    }
    with pytest.raises(RulePackError, match="status"):
        validate_rule_pack(raw)


def test_normalize_region_clamps_to_unit_square() -> None:
    r = normalize_region({"x": -0.1, "y": 0.2, "w": 1.5, "h": 0.1})
    assert 0.0 <= r["x"] <= 1.0
    assert 0.0 <= r["y"] <= 1.0
    assert 0.0 < r["w"] <= 1.0
    assert r["x"] + r["w"] <= 1.0 + 1e-6
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_rule_pack.py -v`  
Expected: FAIL（模块不存在）

- [ ] **Step 3: 最小实现**

```python
# lsc/analyzer/rule_pack.py
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

_PACKAGE_DIR = Path(__file__).resolve().parent / "rule_packs"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
USER_RULES_DIR = _PROJECT_ROOT / "data" / "analysis_rules"

REQUIRED_TEMPLATE = "fps_round"


class RulePackError(ValueError):
    pass


def normalize_region(raw: dict[str, Any]) -> dict[str, float]:
    try:
        x = float(raw["x"])
        y = float(raw["y"])
        w = float(raw["w"])
        h = float(raw["h"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RulePackError(f"无效 OCR 区域: {exc}") from exc
    x = min(max(x, 0.0), 1.0)
    y = min(max(y, 0.0), 1.0)
    w = min(max(w, 0.01), 1.0 - x)
    h = min(max(h, 0.01), 1.0 - y)
    return {"x": x, "y": y, "w": w, "h": h}


def validate_rule_pack(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RulePackError("规则包必须是 JSON 对象")
    if raw.get("template") != REQUIRED_TEMPLATE:
        raise RulePackError("仅支持 template=fps_round")
    pack_id = str(raw.get("id") or "").strip()
    if not pack_id:
        raise RulePackError("缺少 id")
    name = str(raw.get("name") or "").strip() or pack_id
    regions = raw.get("ocr_regions") or {}
    if not isinstance(regions, dict) or "status" not in regions:
        raise RulePackError("缺少 ocr_regions.status")
    status = normalize_region(regions["status"])
    killfeed = None
    if regions.get("killfeed"):
        killfeed = normalize_region(regions["killfeed"])
    keywords = raw.get("keywords") or {}
    buy = [str(x) for x in (keywords.get("buy") or []) if str(x).strip()]
    combat = [str(x) for x in (keywords.get("combat") or []) if str(x).strip()]
    result = [str(x) for x in (keywords.get("result") or []) if str(x).strip()]
    if not buy:
        raise RulePackError("keywords.buy 不能为空")
    if not result:
        raise RulePackError("keywords.result 不能为空")
    duration = raw.get("duration") or {}
    trim = raw.get("trim") or {}
    confirm = raw.get("confirm") or {}
    out: dict[str, Any] = {
        "id": pack_id,
        "name": name,
        "template": REQUIRED_TEMPLATE,
        "ocr_regions": {"status": status, "killfeed": killfeed},
        "keywords": {"buy": buy, "combat": combat, "result": result},
        "duration": {
            "min_sec": float(duration.get("min_sec", 20)),
            "max_sec": float(duration.get("max_sec", 180)),
        },
        "trim": {
            "start_pad_sec": float(trim.get("start_pad_sec", 0.5)),
            "end_pad_sec": float(trim.get("end_pad_sec", 1.5)),
        },
        "confirm": {
            "require_ocr_bounds": bool(confirm.get("require_ocr_bounds", True)),
            "start_by": str(confirm.get("start_by", "ocr_buy_exit")),
            "end_by": list(confirm.get("end_by") or ["ocr_result", "next_buy"]),
        },
    }
    # 内置扩展字段原样保留（phase/audio）
    if isinstance(raw.get("phase"), dict):
        out["phase"] = dict(raw["phase"])
    if isinstance(raw.get("audio"), dict):
        out["audio"] = dict(raw["audio"])
    return out
```

（同文件后续 Task 2 再补 `load_rule_pack` / `list_rule_packs` / `save_user_pack` / `duplicate_pack`。）

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_rule_pack.py::test_validate_rejects_missing_status_region tests/test_rule_pack.py::test_normalize_region_clamps_to_unit_square -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add lsc/analyzer/rule_pack.py tests/test_rule_pack.py
git commit -m "feat(analyzer): add fps_round rule pack validation"
```

---

### Task 2: 官方包 JSON + 加载/列表/复制/保存

**Files:**
- Create: `lsc/analyzer/rule_packs/builtin_valorant.json`
- Create: `lsc/analyzer/rule_packs/builtin_cs2.json`（可先占位，Task 12 再精调词）
- Modify: `lsc/analyzer/rule_pack.py`
- Modify: `tests/test_rule_pack.py`

- [ ] **Step 1: 写失败测试**

```python
def test_load_builtin_valorant() -> None:
    from lsc.analyzer.rule_pack import load_rule_pack
    pack = load_rule_pack("builtin:valorant")
    assert pack["id"] == "builtin:valorant"
    assert pack["ocr_regions"]["status"]["w"] > 0
    assert "购买" in "".join(pack["keywords"]["buy"]) or "买入" in "".join(pack["keywords"]["buy"])


def test_duplicate_creates_user_pack(tmp_path, monkeypatch) -> None:
    from lsc.analyzer import rule_pack as rp
    monkeypatch.setattr(rp, "USER_RULES_DIR", tmp_path)
    src = rp.load_rule_pack("builtin:valorant")
    dup = rp.duplicate_pack(src["id"], new_name="我的无畏")
    assert dup["id"].startswith("user:")
    assert dup["name"] == "我的无畏"
    assert (tmp_path / f"{dup['id'].replace(':', '_')}.json").exists()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_rule_pack.py::test_load_builtin_valorant -v`  
Expected: FAIL

- [ ] **Step 3: 写入 builtin_valorant.json（数值对齐现网）**

关键字段（必须与当前硬编码一致）：

```json
{
  "id": "builtin:valorant",
  "name": "无畏契约",
  "template": "fps_round",
  "ocr_regions": {
    "status": {"x": 0.40625, "y": 0.118, "w": 0.203, "h": 0.16},
    "killfeed": {"x": 0.75, "y": 0.04, "w": 0.23, "h": 0.20}
  },
  "keywords": {
    "buy": ["购买阶段", "准备阶段", "购买", "准备"],
    "combat": [],
    "result": ["获胜", "胜利", "败北", "失败", "队伍已淘", "队伍已被淘", "victory", "defeat", "eliminated"]
  },
  "duration": {"min_sec": 35, "max_sec": 155},
  "trim": {"start_pad_sec": 0.5, "end_pad_sec": 1.5},
  "confirm": {
    "require_ocr_bounds": true,
    "start_by": "ocr_buy_exit",
    "end_by": ["ocr_result", "next_buy"]
  },
  "phase": {
    "buy_sleep_sec": 22.0,
    "pre_combat_window_sec": 18.0,
    "post_combat_window_sec": 35.0,
    "rms_trust_high": false,
    "ocr_sparse_interval_sec": 1.5,
    "ocr_dense_interval_sec": 0.8,
    "unknown_reanchor_sec": 30.0,
    "max_combat_force_post_sec": 130.0,
    "lookback_sec": 120.0,
    "buy_duration_sec": 30.0,
    "buy_duration_pistol_sec": 45.0,
    "buy_wake_early_sec": 8.0,
    "post_round_sec": 5.0,
    "intermission_enter_sec": 45.0,
    "intermission_max_sec": 120.0,
    "intermission_ocr_interval_sec": 2.0
  }
}
```

`builtin_cs2.json`：同结构；status 先用 `(0.30, 0.01, 0.40, 0.06)`（对齐现 `round_marker_crop`）；buy/result 用 `["BUY", "Buy", "买入"]` / `["WON", "WIN", "胜利", "胜利"]` 等占位，Task 12 用实机样例精调。

- [ ] **Step 4: 实现 load / list / save / duplicate**

```python
def _builtin_path(pack_id: str) -> Path:
    # builtin:valorant -> builtin_valorant.json
    name = pack_id.replace(":", "_") + ".json"
    return _PACKAGE_DIR / name


def _user_path(pack_id: str) -> Path:
    USER_RULES_DIR.mkdir(parents=True, exist_ok=True)
    safe = pack_id.replace(":", "_")
    return USER_RULES_DIR / f"{safe}.json"


def load_rule_pack(pack_id: str) -> dict[str, Any]:
    pack_id = str(pack_id or "").strip()
    if pack_id.startswith("builtin:"):
        path = _builtin_path(pack_id)
    else:
        path = _user_path(pack_id)
    if not path.is_file():
        raise RulePackError(f"规则包不存在: {pack_id}")
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return validate_rule_pack(raw)


def list_rule_packs() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in sorted(_PACKAGE_DIR.glob("builtin_*.json")):
        with open(path, encoding="utf-8") as f:
            items.append(validate_rule_pack(json.load(f)))
    if USER_RULES_DIR.is_dir():
        for path in sorted(USER_RULES_DIR.glob("*.json")):
            try:
                with open(path, encoding="utf-8") as f:
                    items.append(validate_rule_pack(json.load(f)))
            except (OSError, json.JSONDecodeError, RulePackError):
                continue
    return items


def save_user_pack(pack: dict[str, Any]) -> dict[str, Any]:
    validated = validate_rule_pack(pack)
    if validated["id"].startswith("builtin:"):
        raise RulePackError("不能覆盖官方规则包")
    path = _user_path(validated["id"])
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(validated, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)
    return validated


def duplicate_pack(source_id: str, new_name: str | None = None) -> dict[str, Any]:
    src = load_rule_pack(source_id)
    new_id = f"user:{uuid.uuid4().hex[:12]}"
    src["id"] = new_id
    src["name"] = (new_name or f"{src['name']} 副本").strip()
    # 用户包不强制带 phase；复制官方时保留 phase 以便行为接近
    return save_user_pack(src)
```

- [ ] **Step 5: 跑测试通过并 Commit**

```bash
pytest tests/test_rule_pack.py -v
git add lsc/analyzer/rule_pack.py lsc/analyzer/rule_packs tests/test_rule_pack.py
git commit -m "feat(analyzer): load builtin/user fps_round rule packs"
```

---

### Task 3: `profile_from_mapping` + 检测配置映射

**Files:**
- Modify: `lsc/analyzer/phase_scheduler.py`
- Create: `lsc/analyzer/fps_round_interpreter.py`（先放映射函数）
- Create: `tests/test_fps_round_interpreter.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_fps_round_interpreter.py
from lsc.analyzer.phase_scheduler import get_profile, profile_from_mapping
from lsc.analyzer.rule_pack import load_rule_pack
from lsc.analyzer.fps_round_interpreter import pack_to_round_config, pack_to_ocr_overrides


def test_profile_from_valorant_pack_matches_unified() -> None:
    pack = load_rule_pack("builtin:valorant")
    built = profile_from_mapping(pack["phase"])
    base = get_profile("valorant")
    assert built.ocr_dense_interval_sec == base.ocr_dense_interval_sec
    assert built.lookback_sec == base.lookback_sec


def test_pack_to_ocr_overrides_uses_status_region() -> None:
    pack = load_rule_pack("builtin:valorant")
    ov = pack_to_ocr_overrides(pack)
    assert ov["status_crop"] == (
        pack["ocr_regions"]["status"]["x"],
        pack["ocr_regions"]["status"]["y"],
        pack["ocr_regions"]["status"]["w"],
        pack["ocr_regions"]["status"]["h"],
    )
    assert "获胜" in ov["result_keywords"]
```

- [ ] **Step 2: 实现**

```python
# phase_scheduler.py 增加：
def profile_from_mapping(data: dict[str, Any] | None) -> ValorantProfile:
    base = _UNIFIED_PROFILE
    if not data:
        return base
    kwargs = {f.name: getattr(base, f.name) for f in ValorantProfile.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    kwargs["name"] = str(data.get("name") or "pack")
    for key in kwargs:
        if key == "name":
            continue
        if key in data:
            kwargs[key] = type(getattr(base, key))(data[key])
    return ValorantProfile(**kwargs)
```

```python
# fps_round_interpreter.py
from __future__ import annotations
from typing import Any
from lsc.analyzer.round_detector import ValorantRoundConfig


def pack_to_round_config(pack: dict[str, Any]) -> ValorantRoundConfig:
    cfg = ValorantRoundConfig()
    dur = pack.get("duration") or {}
    cfg.min_ocr_round_duration = float(dur.get("min_sec", cfg.min_ocr_round_duration))
    cfg.max_round_total = float(dur.get("max_sec", cfg.max_round_total))
    # full_round 路径仍由调用方决定；默认保持现网 continuous 行为
    return cfg


def pack_to_ocr_overrides(pack: dict[str, Any]) -> dict[str, Any]:
    st = pack["ocr_regions"]["status"]
    kw = pack["keywords"]
    return {
        "status_crop": (st["x"], st["y"], st["w"], st["h"]),
        "buy_keywords": list(kw.get("buy") or []),
        "combat_keywords": list(kw.get("combat") or []),
        "result_keywords": list(kw.get("result") or []),
    }


def pack_trim_pads(pack: dict[str, Any]) -> tuple[float, float]:
    tr = pack.get("trim") or {}
    return float(tr.get("start_pad_sec", 0.5)), float(tr.get("end_pad_sec", 1.5))
```

- [ ] **Step 3: 测试通过并 Commit**

```bash
pytest tests/test_fps_round_interpreter.py -v
git add lsc/analyzer/phase_scheduler.py lsc/analyzer/fps_round_interpreter.py tests/test_fps_round_interpreter.py
git commit -m "feat(analyzer): map rule pack to profile and OCR overrides"
```

---

### Task 4: `round_detector` OCR 路径接受覆盖参数

**Files:**
- Modify: `lsc/analyzer/round_detector.py`（`_extract_round_phase_markers` 或等价私有函数，约 1175–1330 行；以及调用链 `detect_valorant_rounds`）
- Modify: `tests/test_round_detector.py`（若已有 OCR 单测则扩；否则加轻量纯函数测关键词匹配）

- [ ] **Step 1: 扩展函数签名（保持默认值 = 现网硬编码）**

在提取状态标记的函数上增加：

```python
status_crop: tuple[float, float, float, float] | None = None,
buy_keywords: list[str] | None = None,
result_keywords: list[str] | None = None,
```

默认：

```python
crop_ratio = status_crop or (0.40625, 0.118, 0.203, 0.16)
# buy 判定：若 buy_keywords 提供则 any(k in text for k in buy_keywords)
# 否则保持现有 "购买阶段"/"准备阶段"/"购买"/"准备" 逻辑
# end_keywords = tuple(result_keywords) if result_keywords else (现有元组)
```

`detect_valorant_rounds(..., ocr_overrides: dict | None = None)`：若提供则传入上述参数。

- [ ] **Step 2: 加回归测试——不传 overrides 时行为与旧默认一致（至少关键词列表与 crop 常量不变）**

```python
def test_default_ocr_overrides_match_legacy_constants() -> None:
    from lsc.analyzer.fps_round_interpreter import pack_to_ocr_overrides
    from lsc.analyzer.rule_pack import load_rule_pack
    ov = pack_to_ocr_overrides(load_rule_pack("builtin:valorant"))
    assert ov["status_crop"] == (0.40625, 0.118, 0.203, 0.16)
```

- [ ] **Step 3: Commit**

```bash
git add lsc/analyzer/round_detector.py tests/test_round_detector.py tests/test_fps_round_interpreter.py
git commit -m "feat(analyzer): inject OCR crop/keywords into round phase detection"
```

---

### Task 5: 解释器 `scan()` 封装

**Files:**
- Modify: `lsc/analyzer/fps_round_interpreter.py`
- Modify: `tests/test_fps_round_interpreter.py`

- [ ] **Step 1: 实现**

```python
def scan_fps_rounds(
    video_path: str,
    pack: dict[str, Any],
    *,
    ffmpeg_path: str = "ffmpeg",
    refine_with_ocr: bool = True,
    time_range: tuple[float, float] | None = None,
    progress_callback=None,
    cancel_check=None,
    phase_sample_interval: float | None = None,
) -> list[dict[str, Any]]:
    from lsc.analyzer.round_detector import detect_valorant_rounds
    cfg = pack_to_round_config(pack)
    if phase_sample_interval is not None:
        cfg.phase_sample_interval = float(phase_sample_interval)
    overrides = pack_to_ocr_overrides(pack)
    rounds = detect_valorant_rounds(
        video_path,
        ffmpeg_path=ffmpeg_path,
        progress_callback=progress_callback,
        cancel_check=cancel_check,
        config=cfg,
        refine_with_ocr=refine_with_ocr,
        time_range=time_range,
        ocr_overrides=overrides,
    )
    # 附带 pack id 便于 round_key
    for r in rounds:
        r.setdefault("rule_pack_id", pack["id"])
    return rounds
```

- [ ] **Step 2: 单测用 monkeypatch `detect_valorant_rounds` 断言传入的 `ocr_overrides` / config**

- [ ] **Step 3: Commit**

```bash
git commit -am "feat(analyzer): add scan_fps_rounds interpreter entry"
```

---

### Task 6: Valorant parity 护栏

**Files:**
- Create: `tests/test_valorant_rule_pack_parity.py`
- 若仓库已有短样例视频/fixture，优先复用；否则用「参数对等」+「mock 回合输出键集合」两级护栏

- [ ] **Step 1: 参数对等测试（必做）**

```python
def test_builtin_valorant_phase_equals_get_profile() -> None:
    from lsc.analyzer.rule_pack import load_rule_pack
    from lsc.analyzer.phase_scheduler import get_profile, profile_from_mapping
    pack = load_rule_pack("builtin:valorant")
    a = profile_from_mapping(pack["phase"])
    b = get_profile("valorant")
    for field in b.__dataclass_fields__:
        if field == "name":
            continue
        assert getattr(a, field) == getattr(b, field), field
```

- [ ] **Step 2: 若存在可跑的短视频 fixture，增加端到端：旧 `detect_valorant_rounds` vs `scan_fps_rounds(builtin:valorant)` 的 round 数量与起止差 ≤ 1.0s**

无 fixture 时在测试文件顶部注明 `pytest.mark.skip` 条件，勿假装通过。

- [ ] **Step 3: Commit**

```bash
git add tests/test_valorant_rule_pack_parity.py
git commit -m "test: valorant rule pack parity guards"
```

---

### Task 7: Handler 接入 `rule_pack_id` / status / round_key（仍可双轨）

**Files:**
- Modify: `python-backend/handlers/room_handler.py`
- Modify: `tests/test_continuous_analysis_guards.py`

- [ ] **Step 1: 解析启动参数**

在 `handle_start_continuous_analysis`：

```python
rule_pack_id = data.get("rule_pack_id")
if not rule_pack_id:
    if mode == "valorant_round" or game == "valorant":
        rule_pack_id = "builtin:valorant"
    else:
        rule_pack_id = None  # scene/generic 不走 fps_round

if rule_pack_id:
    try:
        from lsc.analyzer.rule_pack import load_rule_pack
        pack = load_rule_pack(rule_pack_id)
    except Exception as exc:
        return {"success": False, "error": f"规则包无效: {exc}"}
else:
    pack = None
```

将 `rule_pack_id` / `rule_pack_name` 写入 `_continuous_tasks[...]` 与 `_continuous_analysis_status_payload`。

- [ ] **Step 2: 改 `_valorant_round_key`**

```python
def _valorant_round_key(round_data: dict[str, Any], rule_pack_id: str | None = None) -> str:
    existing = str(round_data.get("round_key") or "").strip()
    if existing:
        return existing
    try:
        start = float(round_data.get("start", 0.0))
    except (TypeError, ValueError):
        start = 0.0
    base = f"round-{int(round(start / 10.0)):06d}"
    pack = rule_pack_id or round_data.get("rule_pack_id") or "builtin:valorant"
    safe = str(pack).replace("|", "_")
    return f"{safe}|{base}"
```

所有构造 `round_key` / `listed_key` 处传入 task 的 `rule_pack_id`。

- [ ] **Step 3: trim 使用包内 pad**

在入列前 `_trim_valorant_combat_bounds` 调用处，若 task 有 pack，用 `pack_trim_pads(pack)` 覆盖模块常量（或给 trim 函数增加可选 pad 参数，默认仍为现网 0.5/1.5）。

- [ ] **Step 4: guards 测试更新 + Commit**

```bash
pytest tests/test_continuous_analysis_guards.py -v
git commit -am "feat(backend): accept rule_pack_id for continuous analysis"
```

---

### Task 8: Worker 切到 `scan_fps_rounds`（去掉 valorant 特殊扫描分支）

**Files:**
- Modify: `python-backend/handlers/room_handler.py`（`_continuous_valorant_worker` / `_continuous_analysis_loop` 内调用 `detect_valorant_rounds` 处）

- [ ] **Step 1: 当 task 有 pack 且 template=fps_round 时**

```python
from lsc.analyzer.fps_round_interpreter import scan_fps_rounds
from lsc.analyzer.phase_scheduler import profile_from_mapping, get_profile

profile = profile_from_mapping(pack.get("phase")) if pack.get("phase") else get_profile("valorant")
# 原 detect_valorant_rounds(...) 替换为：
rounds = scan_fps_rounds(
    video_path,
    pack,
    ffmpeg_path=ffmpeg_path,
    refine_with_ocr=refine_with_ocr,
    time_range=time_range,
    phase_sample_interval=...,  # 仍由相位预算算出
)
```

相位预算继续用 `profile`；`confirm.require_ocr_bounds` 为 True 时保持现有 `_is_auto_exportable_valorant_round` 逻辑（start_by/end_by 与包 confirm 字段对齐）。

- [ ] **Step 2: 保留 `mode=scene` / `game=generic` 旧路径不变**

- [ ] **Step 3: 跑 continuous 相关测试**

```bash
pytest tests/test_continuous_analysis_guards.py tests/test_synced_continuous_analysis.py tests/test_valorant_rule_pack_parity.py -v
```

- [ ] **Step 4: Commit**

```bash
git commit -am "refactor(backend): continuous valorant path uses fps_round interpreter"
```

---

### Task 9: 规则 CRUD + OCR 试读 WebSocket API

**Files:**
- Modify: `python-backend/handlers/room_handler.py`（在 `register_room_handlers` 内注册）

- [ ] **Step 1: 注册消息**

| type | 行为 |
|------|------|
| `list_analysis_rule_packs` | 返回 `list_rule_packs()` 摘要（id/name/template/builtin） |
| `get_analysis_rule_pack` | `{rule_pack_id}` → 完整包 |
| `duplicate_analysis_rule_pack` | `{source_id, name?}` → 新用户包 |
| `save_analysis_rule_pack` | `{pack}` → 校验后保存（拒绝 builtin id） |
| `delete_analysis_rule_pack` | `{rule_pack_id}` → 仅 user |
| `trial_ocr_region` | 见下 |

`trial_ocr_region` 参数：

```python
{
  "room_id": "...",          # 可选，用于找预览/录制
  "video_path": "...",       # 可选兜底
  "source": "preview_frame" | "recording",
  "timestamp_sec": 0.0,      # recording 时
  "region": {"x","y","w","h"},
  "frame_jpeg_base64": "..." # 若前端直接传预览截帧，优先用这个，免服务端抓预览
}
```

实现优先路径：**前端把 video 当前帧 canvas → JPEG base64 传来**，后端裁剪 region 后跑 `_get_ocr()`，返回 `{"texts": ["购买阶段", ...], "raw": [...]}`。  
若无 base64，则对 `video_path` 用 FFmpeg 抽一帧再裁。

错误用 `humanize_error` / 明确中文；禁止 `except: pass`。

- [ ] **Step 2: 源码形状或轻量 handler 单测（可 mock OCR）**

- [ ] **Step 3: Commit**

```bash
git commit -am "feat(backend): analysis rule pack CRUD and OCR trial"
```

---

### Task 10: 前端类型 + 启动选包

**Files:**
- Modify: `lsc-electron/src/types/index.ts`
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`
- Modify: `lsc-electron/src/components/AnalysisProgress.tsx`

- [ ] **Step 1: 类型**

```typescript
export interface OcrRegion {
  x: number
  y: number
  w: number
  h: number
}

export interface AnalysisRulePack {
  id: string
  name: string
  template: 'fps_round'
  ocr_regions: { status: OcrRegion; killfeed?: OcrRegion | null }
  keywords: { buy: string[]; combat: string[]; result: string[] }
  duration: { min_sec: number; max_sec: number }
  trim: { start_pad_sec: number; end_pad_sec: number }
  confirm: { require_ocr_bounds: boolean }
}

// ContinuousAnalysisStatus 增加：
rule_pack_id?: string
rule_pack_name?: string
```

- [ ] **Step 2: 启动 Modal**

将「无畏契约 / 通用」改为：

- 分析类型：`回合切割（规则包）` | `场景`
- 若回合切割：Select 列出 `list_analysis_rule_packs`（默认 `builtin:valorant`）
- `send('start_continuous_analysis', { ..., rule_pack_id, mode: 'valorant_round', game: 'valorant' })`
- 场景模式不传 `rule_pack_id`（或显式 null）

旁路按钮「管理规则」打开 Task 11 编辑器。

- [ ] **Step 3: AnalysisProgress 显示 `rule_pack_name`**

- [ ] **Step 4: `npx tsc --noEmit` 通过后 Commit**

```bash
git commit -am "feat(ui): select analysis rule pack when starting continuous analysis"
```

---

### Task 11: 规则编辑器 + 拖框 + 试读

**Files:**
- Create: `lsc-electron/src/components/RulePackEditor/index.tsx`
- Create: `lsc-electron/src/components/RulePackEditor/RegionCanvas.tsx`
- Create: `lsc-electron/src/components/RulePackEditor/RulePackEditor.css`（若需；优先复用 tokens）

- [ ] **Step 1: RegionCanvas**

- props: `imageUrl`（blob URL）、`region`、`onChange(region)`
- 在 **内容区**坐标系计算（`object-fit: contain` 时用 letterbox 偏移）
- 拖拽移动 / 边角缩放；输出 0–1 比例

- [ ] **Step 2: 编辑器流程**

1. 列表：官方只读 + 我的包；复制 / 删除（仅 user）
2. 编辑：名称、buy/result 关键词（Tag 输入）、duration/trim 数字、状态区拖框
3. 「抓取当前预览帧」：从工作台传入的 `<video>` ref 或回调 `capturePreviewFrame(): string`（JPEG base64）
4. 「试读 OCR」→ `trial_ocr_region` → 展示 texts
5. 保存 → `save_analysis_rule_pack`；缺 status 时按钮 disabled

- [ ] **Step 3: 无预览时提示改用录制截帧（可选第二按钮，调后端 recording 抽帧）**

- [ ] **Step 4: tsc + 手动点检清单写入 PR 描述；Commit**

```bash
git commit -am "feat(ui): rule pack editor with OCR region crop and trial"
```

---

### Task 12: 精调 `builtin:cs2` + 复制引导

**Files:**
- Modify: `lsc/analyzer/rule_packs/builtin_cs2.json`
- Modify: 编辑器文案 / 空状态：「从 CS2 官方包复制后拖框校准」

- [ ] **Step 1:** 用至少一段 CS2 录制（开发者自备，不入库大文件）校准 status 框与关键词；把最终 JSON 提交

- [ ] **Step 2:** 文档注释写明「官方 CS2 为起点，用户须按自己 HUD 校准」

- [ ] **Step 3: Commit**

```bash
git commit -am "feat(analyzer): ship tuned builtin CS2 fps_round pack"
```

---

### Task 13: 清理与文档

**Files:**
- Modify: `python-backend/handlers/room_handler.py`（删除确认无引用的 valorant 硬编码扫描旁路）
- Modify: `docs/superpowers/specs/2026-07-16-fps-round-rule-pack-design.md` 状态 → 已实施（或实施中）
- 可选：`CLAUDE.md` 持续分析小节补一句「规则包 `rule_pack_id`」——仅当团队惯例要更新权威文档时

- [ ] **Step 1:** `rg "detect_valorant_rounds"` 确认 continuous 路径只经解释器；同步分析若仍直调可保留

- [ ] **Step 2:** 全量相关测试

```bash
pytest tests/test_rule_pack.py tests/test_fps_round_interpreter.py tests/test_valorant_rule_pack_parity.py tests/test_continuous_analysis_guards.py tests/test_synced_continuous_analysis.py tests/test_phase_scheduler.py -v
cd lsc-electron && npx tsc --noEmit
```

- [ ] **Step 3: Commit**

```bash
git commit -am "chore: finalize fps_round rule pack cutover"
```

---

## Spec 覆盖自检

| Spec 项 | Task |
|---------|------|
| Schema / 用户可编辑字段 | 1–2 |
| 官方/用户存储路径 | 2 |
| 拖框 UX / 试读 | 9, 11 |
| 解释器 + worker | 5, 8 |
| 无畏 parity 再切流 | 6 → 8 |
| `rule_pack_id` 兼容旧 game | 7, 10 |
| `round_key` 含 pack id | 7 |
| confirm / pending 链路不变 | 7–8（不改 export_deferred 语义） |
| CS2 内置包 | 2 占位 + 12 精调 |
| 非目标（自由 DSL/热更新等） | 未排任务 |

## 风险与注意

- `round_detector` OCR 函数体大：只加可选参数，**不要**顺手大重构
- 切流前 Task 6 必须绿；否则停在双轨（Task 7）不要进 Task 8
- 前端拖框坐标系必须处理 letterbox，否则框永久偏
- 用户包复制官方时保留 `phase`，避免自定义包默默落到过稀的 OCR 间隔
