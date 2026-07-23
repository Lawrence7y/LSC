# De-Qt Orchestrator + Analyzer Plugin — Plan Index (M1→M5)

> 总览索引。执行时按里程碑顺序打开对应 plan；每阶段独立可交付、可回滚。

**Spec:** `docs/spec-deqt-orchestrator-analyzer-plugin.md`  
**日期:** 2026-07-23

---

## Plans

| 里程碑 | Plan 文件 | 内容 |
|--------|-----------|------|
| M1 / A1 | [`2026-07-23-deqt-m1-orchestrator.md`](./2026-07-23-deqt-m1-orchestrator.md) | EventBus + RoomOrchestrator（纯新增） |
| M2 / A2 | [`2026-07-23-deqt-m2-manager-shell.md`](./2026-07-23-deqt-m2-manager-shell.md) | MultiRoomManager 薄 Qt 壳 |
| M3 / A3 | [`2026-07-23-deqt-m3-backend-no-qt.md`](./2026-07-23-deqt-m3-backend-no-qt.md) | backend 去 Qt / BroadcastHub |
| M4 / B1 | [`2026-07-23-analyzer-m4-plugin-protocol.md`](./2026-07-23-analyzer-m4-plugin-protocol.md) | AnalyzerPlugin + Registry |
| M5 / B2 | [`2026-07-23-analyzer-m5-plugin-migrate.md`](./2026-07-23-analyzer-m5-plugin-migrate.md) | 插件迁入 + handler 瘦身 |

**不做（spec B3 / 明确排除）：** `lsc/analyzer/valorant/` 物理拆包、`room_handler` 按域五拆、shared ingest 实时帧分析、改前端 WS 形状。

---

## Spec → 代码校正（所有 plan 已吸收）

1. **心跳频率：** 代码是 3s base / stagger-3 / low 每 4 ticks（≈12s），**不是** spec 正文 1s/5s/10s。
2. **扫描窗口函数名：** `_continuous_valorant_scan_budget`，不是 `_compute_continuous_scan_range`。
3. **Scene 分析：** `_run_scene_analysis` 在 `room_handler`，不在 `pipeline.py`。
4. **Valorant 生产路径：** one-shot/continuous 用 `detect_valorant_rounds_hybrid`；`HighlightAnalyzer` 音频路径勿当作生产对等金标准。

---

## 依赖顺序

```
M1 → M2 → M3
M4 → M5
```

M4 可与 M1–M2 并行；**不要**与 M3 同改 `room_handler` 大块（合并冲突）。建议顺序：**M1→M2→M3，再 M4→M5**。

---

## 执行方式

每个 plan 头部要求：`subagent-driven-development`（推荐）或 `executing-plans`。  
默认 **不要 git commit**，除非用户明确要求。
