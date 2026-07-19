# 持续分析质量优先档设计

> **日期：** 2026-07-15  
> **状态：** 已确认（方案 1）  
> **前提：** 用户选择「可直接导出的正赛段」优先，不计性能与资源占用

## Goal

在不改动确认门与「仅入列不自动导出」契约的前提下，提高持续分析产物质量：边界更准、漏检更少、误切更少，入列片段更接近正赛。

## 非目标

- 不改 MSE / 录制 / 导出队列架构
- 不放宽「纯音频不得升格可导」（`_is_auto_exportable_valorant_round` 条件不变）
- 不做周期性全文件重扫（方案 2）
- 不改变 `list_only` / 用户确认后再导出

## 决策摘要

| 项 | 选择 |
|----|------|
| 方案 | 质量优先档：密 OCR + 忽略压力关 OCR + 更狠裁尾 |
| 确认门 | 保持 `start_by=ocr_buy_exit` + `end_by∈{ocr_result,next_buy}` |
| BUY/COMBAT OCR | 始终开启（不再默认关闭） |
| 压力降载 | 质量档忽略 `pause_analysis` 关 OCR；删除 critical 奇数 tick 纯音频 |

## 改动明细

### 1. 相位 profile（`lsc/analyzer/phase_scheduler.py`）

| 参数 | pov 旧 → 新 | broadcast 旧 → 新 |
|------|-------------|-------------------|
| `ocr_sparse_interval_sec` | 12 → **1.5** | 7 → **1.5** |
| `ocr_dense_interval_sec` | 2 → **0.8** | 1.5 → **0.8** |
| `intermission_ocr_interval_sec` | 15 → **2** | 10 → **2** |
| `lookback_sec` | 40 → **90** | 55 → **120** |

`buy_sleep_sec` 保留（相位仍休眠），但休眠期也跑稀疏 OCR。

### 2. `scan_budget_for_phase`

- `BUY` / `COMBAT`：`need_ocr` 从 False 改为 **True**（COMBAT 用 sparse 间隔，PRE/POST/UNKNOWN 用 dense）。
- 预测 dense 窗仍可把 COMBAT 提到 dense 间隔。

### 3. Handler 扫描策略（`room_handler.py`）

- `_continuous_valorant_refine_with_ocr`：质量档 **忽略** `pause_analysis`，`valorant_round` 始终返回 True。
- 删除主循环中 critical 奇数 tick 强制 `refine_with_ocr=False` 的分支。
- Worker：`phase_sample_interval=max(0.8, ocr_iv)`（允许低于 1.0）。
- `_VALORANT_INCREMENTAL_LOOKBACK_SEC`：240 → **360**。
- valorant 增量消费最小间隔：30s → **10s**。
- finalize 时 `ocr_sample_interval` 上限：**1.0**（原 min(..., 2.0)）。

### 4. 边界收紧

- `_VALORANT_POST_ROUND_JUNK_SEC`：5 → **8**。
- `_trim_valorant_combat_bounds`：
  - 有 `ocr_start`/`round_start_sec` 时 start **+0.5s**（避开买枪尾帧）；
  - 有 `ocr_end`/`round_end_sec` 时 end **-1.5s**（避开结算字帧）；
  - `next_buy` 仍回推 `_VALORANT_POST_ROUND_JUNK_SEC`。
- `ValorantRoundConfig.tail_pad`：6 → **3**。
- 入列：`pending_only_hl` 与 `ocr_confirmed_hl` **都先** `_trim_valorant_combat_bounds`。

### 5. 契约保持

- `_is_auto_exportable_valorant_round` 不变
- `list_only=True` 不变
- `_refined_round_keys` 冻结用户精修不变

## 测试

- 更新 `test_phase_scheduler`：COMBAT/BUY 期望 `need_ocr=True`
- 新增/调整：trim 砍尾、refine 忽略 pause、无 critical 纯音频降载
- 现有 OCR 可导出门禁测试保持通过

## 验收

- [x] 持续分析 BUY/COMBAT 扫描仍请求 OCR
- [x] 压力 pause 时 valorant 仍可 OCR
- [x] critical 奇数 tick 不再关掉 OCR
- [x] 入列 pending/ocr 边界经 trim，尾部垃圾减少
- [x] 双可信确认门与 list_only 行为不变
- [x] 相关单元测试通过（phase_scheduler / continuous_guards / round_detector 等）
