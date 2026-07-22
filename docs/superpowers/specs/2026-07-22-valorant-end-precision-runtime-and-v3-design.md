# Valorant 终点精度：运行时收紧 + Hard Retrain v3 设计

## 目标

在候选 v2 已将确认真值回合召回做到 1.0 的基础上，压低结束点误差 P95、提高 listed precision，并保持召回 ≥ 0.90。

手段分两阶段：

1. 运行时：`result` 结束强置信更严 + listed 相邻回合去重/合并；
2. 对补丁后仍失败的终点/FP 窗口挖 hard samples，重训候选 v3 并复评。

不改变 `pending` / 导出门禁产品语义；盲测不回流；门禁未过不替换 `lsc/analyzer/models/valorant_phase_v1.*`。

## 背景基线（确认真值，2026-07-22）

| 模型 | 召回 | listed precision | start P95 | end P95 |
|---|---|---|---|---|
| 生产 | 0.625 | 1.000 | 79.0s | 4.8s |
| 候选 v1 | 0.875 | 0.875 | 8.6s | 56.9s |
| 候选 v2 | 1.000 | 0.889 | 6.8s | 55.4s |

v2 帧分类准确率约 0.95；主要剩余问题是终点漂移与 listed 误检。

## 方案边界

### 阶段 A：运行时

**A1. `result` 结束强置信**

- 保留 `stable_prob` / `high_prob`。
- 新增可选 `result_high_prob`（默认建议 0.88；未设置回退 `high_prob`）。
- 仅 `end_by == model_result` 的 `end_strong` 使用该阈值；`next_buy` / `score` 不变。
- `grade_round_confirmation` 不变：弱终点仍为 `pending`。

**A2. listed 相邻去重/合并**

- hybrid 产出 closed listed 后做纯函数后处理：
  - IoU ≥ 0.5，或间隙 < 3s 且重叠/嵌套 → 合并（更早 start、更晚 end；`confirm_status` 取更强，`vision_confirmed` 优先）。
  - 合并后时长 > 150s 仅写审计字段，不强制截断。
- 评估 `listed` 以去重后列表为准。

### 阶段 B：Hard → v3

1. 在「v2 权重 + 阶段 A 运行时」上复评确认真值，列出 |end_err|>0.8、listed FP、合并审计；若召回 <0.90 优先微调去重而非放宽导出。
2. Hard 抽帧：错终点 ±8s、FP 全段、合并冲突区；2fps；优先 `result`/后接 `buy` 与 combat→result 邻域。
3. 合并到 `valorant_phase_boundary_20260722_v3`（时间块切分；blind/test 只读）；`result`/`buy` 过采样。
4. 训练到独立目录 `valorant_phase_boundary_20260722_v3`；结构/五分类/ONNX 契约不变。

### 门禁

须同时：召回 ≥0.90；起止 P95 ≤0.8s；listed precision ≥0.97；既有门禁不退化；弱 `model_result` 不得单独 `vision_confirmed`。失败只留候选。

## 数据流

```text
v2 模型
  -> result_high_prob + listed merge
  -> 确认真值复评
  -> 剩余 end/FP hard 抽帧与补标
  -> 数据集 v3 + 训练候选 v3
  -> 确认真值 + blind/test 门禁
  -> 通过后才可提议替换生产
```

## 验证

- 单测：0.80–0.87 的 `model_result` 结束仍 `pending`；≥ `result_high_prob` 且起点强 → `vision_confirmed`。
- 单测：重叠/近邻回合合并为并集边界。
- 复评报告与交付摘要记录运行时参数、SHA-256、门禁结论。
- 回归：FSM / 连续分析 / hybrid / 既有弱终点测试。

## 非目标

- 不重写 FSM；不改导出/`pending` 产品语义；
- 不把单场录制硬编码进检测逻辑；
- 门禁失败不覆盖生产 ONNX。
