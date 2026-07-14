# 持续分析：pending 切片边界自动同步 + 状态轮询对齐

> **日期：** 2026-07-14  
> **状态：** 待用户审阅后进入实施计划  
> **前置：**  
> - [2026-07-14-pending-clip-refine-timeline-design.md](./2026-07-14-pending-clip-refine-timeline-design.md)  
> - [2026-07-14-valorant-phase-scheduler-continuous-analysis-design.md](./2026-07-14-valorant-phase-scheduler-continuous-analysis-design.md)

## Goal

让持续分析在 OCR/相位精修后，**同 `round_key` 的 pending 切片入出点自动更新到列表**；同时修复进度轮询与 OCR 状态显示不一致，使 UI「确认数 / OCR 校正 / 相位」可信。

## 决策摘要（已确认）

| 项 | 选择 |
|----|------|
| 优先级 | A 切片质量 + C 状态体验（本轮不做整窗 OCR 减载 / PCM 缓存） |
| 边界更新 | **自动 upsert**：同回合 pending 切片 start/end 跟着变 |
| 架构 | 方案 A：拆分「已入列 / 已导出」集合 + 允许 pending 再广播 `clip_queued` |
| 冻结策略 | `refining` / `user_confirmed` / 已真正导出 → 禁止自动改边界 |
| 边界变化阈值 | ≥ **0.3s**（入或出任一侧）才再广播，避免抖动刷屏 |

## 非目标

- 不改 MSE 预览硬解、导出滤镜、录制架构
- 不做「仅边界窗 OCR」性能优化（下一轮）
- 不改用户确认后自动导出（仍手动导出）
- 不新增 UI 组件；复用现有 `clip_queued` upsert 与 `AnalysisProgress`

---

## 问题背景

### 切片边界不更新

1. 前端 `Workbench` 已对同 `room_id + round_key` 的 `clip_queued` 做 upsert（更新 start/end/status）。
2. 后端 `_auto_export_highlights(..., list_only=True)` 用 `_exported_clip_ids` 兼做「已入列」去重；首次入列即 `add`，后续同 key 直接 `continue`，精修后的边界无法再次广播。
3. 因此 pending 回合被 OCR/合并改窗后，列表仍显示旧入出点。

### 状态不可信

1. `get_continuous_analysis_status` 字段少于 WS `continuous_analysis_status`（缺 `round_phase`、`scan_running`、`refine_with_ocr`、`valorant_profile` 等）。
2. `confirmed_rounds` 缺省回退为 `len(highlights)`，会把未确认回合显示成已确认。
3. Kick 广播的 OCR 意图与 worker 实际 `refine_with_ocr` 可能不一致（critical 奇数 tick 覆盖）。
4. `scan_range` 未变时 `continue` 跳过 kick，相位切到需 OCR 时可能吞掉加密 OCR 窗。

---

## 1. 后端：入列 / 导出集合拆分

### 1.1 集合语义

| 集合 | 含义 | 何时 add |
|------|------|----------|
| `_listed_clip_ids` | 已向列表广播过的 `room_id:round_key` | `list_only` / deferred 首次 `clip_queued` |
| `_exported_clip_ids` | 已真正入导出队列或 FFmpeg 导出成功 | `queue_export` 成功 / deferred flush 成功 |
| `_refined_round_keys` | 用户精修中或已确认（及既有 OCR 冻结用途） | `begin_refine_clip` / `confirm_highlight_clip`（保持现状） |

### 1.2 `_auto_export_highlights` 行为（list_only）

对每个目标房 `listed_key = f"{rid}:{round_key}"`：

1. 若 `round_key in _refined_round_keys` → **跳过**（用户冻结）。
2. 若 `listed_key in _exported_clip_ids` → **跳过**（已导出，禁止改边界）。
3. 若 `listed_key not in _listed_clip_ids` → 首次入列：广播 `clip_queued`，`_listed_clip_ids.add`。
4. 若已在 `_listed_clip_ids` 且 `confirm_status` 仍为可自动改边界的状态（`pending`；升格为 `ocr_confirmed` 时亦允许更新边界+状态）：
   - 计算与「上次广播边界」差值；若 `|Δstart|≥0.3` 或 `|Δend|≥0.3` 或 `confirm_status` 变化 → 再广播 `clip_queued`（同 `round_key`，新 start/end/status）。
   - 维护 `_listed_clip_bounds[listed_key] = (start, end, status)` 供阈值比较。
5. **不要**在 `list_only` 路径写入 `_exported_clip_ids`。

### 1.3 连续分析主循环清理

- 入列成功后的二次 `_exported_clip_ids.add(...)`（约 `5424`）改为 `_listed_clip_ids` / 边界缓存更新。
- `ocr_confirmed` 升格：可继续用 `clip_confirm_status` **或** 统一走 `clip_queued` upsert（推荐后者与 pending 路径一致）；若保留 `clip_confirm_status`，须带更新后的 start/end（现已带）。

---

## 2. 后端：re-kick 条件

当前（约 `5605-5606`）：

```python
if state.get('scan_range') == scan_range and state.get('scan_phase') == ...:
    continue
```

改为同时比较 OCR 意图：

```python
if (
    state.get('scan_range') == scan_range
    and state.get('scan_phase') == ('full' if full_rescan else 'incremental')
    and state.get('refine_with_ocr') == use_ocr_this_tick
    and not (_finalize_started or _finalize_pending)
):
    continue
```

这样 `need_ocr` 从 false→true（同数值窗）仍会 kick worker。

---

## 3. 后端：状态轮询对齐

`handle_get_continuous_analysis_status` 返回字段与近期 WS `continuous_analysis_status` 对齐，至少包括：

| 字段 | 说明 |
|------|------|
| `running` / `room_id` / `target_room_ids` / `mode` | 已有 |
| `analyzed_duration` / `recorded_duration` | 已有 |
| `total_highlights` | 已有 |
| `confirmed_rounds` | **缺省 0**，禁止 `len(highlights)` |
| `pending_rounds` | 已有语义保留 |
| `analysis_stage` / `phase` / `updated_at` | 已有 |
| `round_phase` | 自 task |
| `scan_running` | worker 是否 `scan_requested` 或未完成 |
| `refine_with_ocr` | **worker 实际** `state['refine_with_ocr']`，非 kick 意图 |
| `valorant_profile` | 自 task |
| `finalizing` / `completed` | 若 task 有则透出 |

抽取 `_build_continuous_status_payload(task, room_id, manager)` 供广播与 GET 共用，避免再漂移。

---

## 4. 前端

- **无需改 upsert 核心逻辑**（已支持同 round_key 更新）。
- 确认 `get_continuous_analysis_status` 轮询合并时写入新增字段（`Workbench` / `AnalysisProgress` 已读的字段名对齐）。
- Toast：同 round_key 边界 upsert **不**再弹「新切片」；仅首次入列或状态升格可 toast（沿用 `toastBatch`）。

---

## 5. 测试

| 用例 | 期望 |
|------|------|
| `list_only` 首次入列 | `_listed` 有、`_exported` 无；广播一次 `clip_queued` |
| 同 key 边界 +0.5s 再入列 | 再广播；前端 clips start/end 更新 |
| 同 key 边界 +0.1s | 不广播（阈值） |
| `begin_refine` 后同 key | 不自动改边界 |
| `confirmed_rounds` 缺省 | GET 返回 0 而非 highlights 长度 |
| GET 与广播 | 共享 builder，关键字段齐全 |
| re-kick | `refine_with_ocr` False→True 且 range 相同 → 仍 kick |

优先落在 `tests/test_continuous_analysis_guards.py` / 新建 `tests/test_clip_list_upsert.py`（mock bridge）。

---

## 6. 风险与回滚

| 风险 | 缓解 |
|------|------|
| 频繁 upsert 刷列表 | 0.3s 阈值 + toast 合并 |
| 误改用户精修中边界 | `_refined_round_keys` 硬门禁 |
| 旧前端忽略 upsert | 仍兼容；最差效果与今日相同 |

回滚：恢复单一 `_exported_clip_ids` 去重即可。

---

## 7. 验收

1. 持续分析中 pending 切片入出点随 OCR/合并变化自动刷新。  
2. 进入精修或用户确认后，边界不再被自动覆盖。  
3. 5s 轮询时确认数/相位/OCR 指示与实时广播一致，不出现「全员已确认」误报。  
4. 相位进入需 OCR 窗时，即使 scan_range 数值不变也会触发扫描。
