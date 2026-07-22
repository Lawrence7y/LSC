# Valorant 终点密扫收口设计（next_buy 锚 + 过渡校验）

## 目标

在不重训、不加 `result` 过采样的前提下，修复 hybrid 终点 refine 导致的大 end 误差（确认真值上 v2 end P95 ≈ 55s）。

手段：向后锚到下一 `buy`/`non_game`，取最后一段稳定 `result`，并要求其后出现过渡帧；不合格则回退 `coarse_end` 且不强确认。

保留已有 `result_high_prob` 与 listed 合并；不改变 `pending`/导出门禁语义；不替换生产模型除非门禁通过。

## 背景

当前 `_refine_hybrid_end` 在 `coarse_end` 附近 ±5s 窗口取**第一段**稳定 `result`。粗终点若偏差很大，或窗口内存在过早 result 闪烁，会锁错终点。55s 级 P95 说明问题在粗终点/错锁，而非 ±5s 微调。

## 方案

### 参数

| 常量 | 默认 | 含义 |
|---|---|---|
| `END_LOOKAHEAD_SEC` | 45 | 从 coarse_end 向后找下一 buy/non_game 上限 |
| `END_LOOKBACK_SEC` | 8 | 密扫下界：`max(round_start, coarse_end - 8)` |
| `min_stable` | 3 | 连续 result 帧数 |
| `_DENSE_FPS` | 现有 | 密扫帧率 |

### 算法（替换「窗口内第一段 result」）

1. 在 `(coarse_end, coarse_end + LOOKAHEAD]` 找下一 `buy` 或 `non_game` 作为 `anchor`（优先 buy）；找不到则 `anchor = coarse_end + LOOKAHEAD`。
2. 密扫 `[lookback, anchor]`，取**最后一段**连续 ≥`min_stable` 的 `result` 起点 `result_ts`。
3. 过渡校验：`result_ts` 之后、`anchor` 之前至少一帧属于 `{buy, non_game, replay}`；否则不合格。
4. 合格：`end = compute_clip_end(result_ts, seq)`，并返回该处 probs。
5. 不合格：返回 `coarse_end` 等价行为——refine 记失败（`None` probs 或显式 flag），调用方保持 `end = coarse_end` 且 `end_strong=False`。

### 非目标

- 不重训、不过采样；
- 不改 FSM 主状态机结构（只改 hybrid end refine）；
- 不放宽导出门禁。

## 验证

- 单元：合成序列覆盖「早 result + 后 result→buy」「无过渡回退」「lookahead 内 next_buy」。
- 复评：v2 权重 + 本收口，在 `rounds_confirmed.json` 上 `eval_blind`；关注 end P95、召回、listed precision。
- 回归：弱 `model_result` pending；既有 hybrid/FSM 测试。

## 门禁（本轮复评目标）

相对收口前 v2：end P95 显著下降；召回尽量 ≥0.90。未达发布门禁（起止 P95≤0.8 等）则只保留代码候选行为，不覆盖生产 ONNX。
