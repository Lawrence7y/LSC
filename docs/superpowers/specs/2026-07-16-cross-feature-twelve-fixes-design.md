# 功能交叉冲突十二条修复 — 设计说明

> 日期：2026-07-16  
> 来源：功能交叉运行审查（持续分析 / 精修 / 对齐 / 刷新 / 停录 / 多选）  
> 计划：`docs/superpowers/plans/2026-07-16-cross-feature-twelve-fixes.md`

## 目标

消除两个及以上功能同时运行时的状态冲突、静默失败与错误写入；不改变墙钟映射与「预览/录制独立」核心契约。

## 原则

1. **生命周期对称**：启动分析/对齐/精修时占用的房间或轴，在删房、断连、失效时必须有明确收尾。
2. **写标记的目标集必须显式**：精修只写本房（或当前确认目标集），禁止闭包陈旧多选。
3. **破坏性操作要提示或互斥**：刷新预览、对齐中、公共轴失效时，禁止静默继续「以为还精确」。
4. **最小改动**：优先前端门禁 + 少量后端停任务；不重做分析管线。

## 十二条决策

| # | 问题 | 修复决策 |
|---|------|----------|
| 1 | 删房不停持续分析 | `handleRemove`：若房是 main 或在 `target_room_ids` 内 → 先 `stop_continuous_analysis(main)`；后端 `handle_remove_room` 同样兜底取消任务 |
| 2 | 精修误写多房 mark | `applySelectClip`：`targets = new Set([roomId])` only；禁止展开旧 `selectedRoomIds` |
| 3 | 短按刷新打公共轴 | 短按刷新前：若 `timelineContext` ready 或 `continuousAnalyzing`，`Modal.confirm` 警告「将使公共轴失效 / 分析继续」；确认后再关开预览 |
| 4 | 副房断连后静默主房-only | 断连/删副房时：若在持续分析 target 内，toast 明确「副房已退出分析映射」；后端 mapping_fallback 已有则前端强化；断主房已停分析 |
| 5 | 公共轴失效精修不退出 | `timeline_invalidated` 与对齐成功写新 timeline 时：`setRefiningClipId(null)` + 取消 refining 的 cancel_refine（若有） |
| 6 | 对齐中不互斥 | `aligning===true` 时：I/O、加切片、导出、Ctrl+E 均 warning return；按钮已 disabled，快捷键补门禁 |
| 7 | 确认目标集陈旧 | `handleConfirmClip`：优先 `continuousTargetRoomIds` **与当前仍 connected 且同 align_group（若有）的交集**；空则回退当前同组；去掉已断连 id |
| 8 | 批量停录无收尾提示 | `handleBatchStop`：若持续分析 running，content 附加「分析将收尾入列，请勿立刻停止分析」 |
| 9 | AI pending 覆盖手标 | `clip_confirm_status` pending 写 mark：仅当该房当前**没有**有效 mark（in/out 皆空）或正在 refining 同 round；否则跳过并 debug/不覆盖 |
| 10 | 主房关预览改 interval 无说明 | 启动持续分析时若主房无预览，toast info「主房未开预览：分析间隔约 45s」；副房未开预览不拦截（保持可映射） |
| 11 | 多选混 review seek 分裂 | `handleTimelineSeek` / `enterTimelineLive`：若 targets 含 no-DVR，仅对可 DVR 房 goLive；混选 seek 时 warning 一次「部分房间为回看模式，未同步跳转」 |
| 12 | 共享进样改画质失效公共轴 | 设置页或预览画质变更成功回调：若曾对齐，message.warning「预览重启后公共轴已失效，请重新一键对齐」（前端听 timeline_invalidated 已有 toast，补「若刚改预览画质」不必；改为 `handle_set_preview_quality` 前端响应里提示） |

## 非目标

- 不自动把副房重新拉回对齐组。
- 不把短按刷新改成自动停分析（仅确认提示）。
- 不扩大 4 路预览上限。

## 验收

每项有源码守卫测试（`tests/test_cross_feature_guards.py`）或行为断言；收尾跑该文件 + `tsc --noEmit`。
