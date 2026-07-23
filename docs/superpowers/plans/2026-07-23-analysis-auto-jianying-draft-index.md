# 分析结束自动剪映草稿 — 计划索引

> **Spec:** [`docs/superpowers/specs/2026-07-23-analysis-auto-jianying-draft-design.md`](../specs/2026-07-23-analysis-auto-jianying-draft-design.md)  
> **前置映射/WS:** [`docs/spec-jianying-draft-export.md`](../../spec-jianying-draft-export.md)（builder 仍有效；UI 三选一被本系列取代）

**执行约束：** 工作目录 `D:\Project\直播切片多人`；**不要 git commit**（除非用户明确要求）。实现时用 `subagent-driven-development` 或 `executing-plans`，按 D1→D2→D3 顺序。

---

## 里程碑

| ID | Plan | 可独立验收 |
|----|------|------------|
| **D1** | [2026-07-23-analysis-draft-d1-ui-cleanup.md](./2026-07-23-analysis-draft-d1-ui-cleanup.md) | 无草稿 Segmented / 无导航草稿；列表无横向滚动；MP4 导出正常 |
| **D2** | [2026-07-23-analysis-draft-d2-session-auto.md](./2026-07-23-analysis-draft-d2-session-auto.md) | Modal 开关；结束自动生成；失败可重试 |
| **D3** | [2026-07-23-analysis-draft-d3-include-pending.md](./2026-07-23-analysis-draft-d3-include-pending.md) | `include_pending`；自动路径含 pending；默认路径旧行为不变 |

**依赖：** D1 → D2（D2 复用 D1 清掉的入口空位与 `requestJianyingDraft`）；D3 可与 D2 并行起步，但 D2 联调需 D3 的 `include_pending=true` 才能让 pending 真进草稿。**推荐顺序 D1 → D3 → D2**（先后端策略再接线），或 D1 → D2（先用 confirmed 冒烟）→ D3 补 pending。本索引约定执行顺序：**D1 → D3 → D2**。

---

## 总验收

- [ ] 切片列表只有 MP4 导出，无「草稿/两者」
- [ ] 导航无「生成剪映草稿」
- [ ] 分析 Modal 有「完成后生成剪映草稿」（默认关）
- [ ] 勾选后持续/一次性分析结束自动出一份草稿；多房未对齐失败不降级
- [ ] pending 切片进入自动草稿；approximate 仍跳过
- [ ] 切片列表无左右滑条，信息完整可见
- [ ] `list_only` 5s 门槛回归仍绿
