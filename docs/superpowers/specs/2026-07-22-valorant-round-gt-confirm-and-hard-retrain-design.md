# Valorant 回合真值确认与 Hard Retrain 设计

## 目标

在上一轮边界模型优化之后：帧分类准确率提升，但回合召回仍为 0.625，候选模型 listed precision 与结束点 P95 退化，发布门禁未通过。

本轮分两阶段：

1. 人工确认回合真值（`rounds_confirmed.json`）；
2. 按漏检与弱终点挖掘 hard samples，重训候选 v2 并复评。

不改变导出门禁语义，不放宽 `pending` 自动导出，盲测集不回流训练，门禁未通过不替换 `lsc/analyzer/models/valorant_phase_v1.*`。

## 背景基线（2026-07-22）

- 标注集：保安头子尼克完整录制边界队列，约 1641 帧已标。
- 旧模型帧准确率 ≈ 0.49；候选 v1 ≈ 0.88。
- 回合召回旧/新均为 0.625（5/8）；候选 listed precision 0.625、end P95 ≈ 36s（退化）。
- 上一轮 `round_manifest` 真值来自分析草稿（`human_confirmed: false`），不足以支撑发布判断。

## 方案边界

### 阶段 A：回合确认 UI

扩展现有 `serve_label_ui.py`（方案 A）：

- 同 `--root`、同端口增加 `/rounds` 页面；帧标注首页可跳转。
- 默认加载 `round_manifest.draft.json` 中的候选回合；支持增删改。
- 字段：`start_sec`、`end_sec`、`end_reason`（`result` | `next_buy` | `score` | `unknown`）、稳定 `round_key`。
- 起止各一帧预览：在队列已抽帧中选时间戳最接近的 JPEG；修改秒数后刷新。
- 保存写出 `--root/rounds_confirmed.json`；未保存不覆盖。

校验：

- `0 ≤ start < end ≤ duration`；
- 同视频回合不重叠；
- 解码/缺帧/非法字段失败时列出具体行，禁止静默跳过。

`rounds_confirmed.json` 形状（与 `merge_round_boundary_labels` / `eval_blind --round-manifest` 兼容）：

```json
{
  "videos": [
    {
      "video_id": "...",
      "session_id": "...",
      "video_path": "...",
      "ground_truth": [
        {
          "start": 93.8,
          "end": 137.4,
          "end_reason": "result",
          "round_key": "R01"
        }
      ]
    }
  ]
}
```

Merge：若存在 `rounds_confirmed.json`，优先作为 `ground_truth`，并标记 `human_confirmed: true`。

### 阶段 B：漏检 hard sample 与重训

1. **复评**：在确认真值上分别评估生产模型与候选 v1，输出漏回合、弱/错终点、起止误差超限配对。
2. **Hard 抽帧**：对问题窗口密集抽帧（默认边界 ±8s、2fps；间隔漏检整段覆盖）。优先覆盖：
   - 漏回合窗口的 buy/combat/result 边界；
   - 终点误差窗口的 result 及其后 buy；
   - 已知分类硬例邻域（combat→buy、combat→result）。
3. **补标**：hard 帧写入独立 annotate 子目录，可用现有帧标注 UI；已有相位标签可按时间对齐复用。
4. **合并**：写入新数据集目录；仍按连续时间块切 train/val；不创建/不修改 blind/test。对 result 与边界 hard 提高采样权重或复制过采样；`non_game`/`replay` 不足时仅从原标注集只读借样。
5. **训练**：独立目录 `valorant_phase_boundary_20260722_v2`；不改 MobileNetV3-Small / 五分类 / ONNX 契约；`--seed` 可复现。
6. **门禁**：同确认真值 + 原 blind/test 对照。须满足召回 ≥ 0.90、起止 P95 ≤ 0.8s，且 listed precision 等既有门禁不退化。失败只保留候选。

## 数据流

```text
帧标注 + round_manifest.draft.json
  -> /rounds 人工确认起止与 end_reason
  -> rounds_confirmed.json
  -> 旧/候选 v1 复评（漏检与弱终点清单）
  -> hard 抽帧 + 补标
  -> 时间块合并数据集（含过采样）
  -> 训练候选 v2（独立目录）
  -> 确认真值 + blind/test 门禁
  -> 通过后才可提议替换生产模型
```

## 错误处理与可追溯性

- 回合保存与 hard 抽帧均不得静默跳过坏样本。
- 交付物需包含：`rounds_confirmed.json`、漏检清单、hard 队列摘要、v2 模型 SHA-256、新旧对照报告、门禁结论。
- 任何替换前核对数据集版本与盲测报告；未通过则明确记录「未部署」。

## 验证

- UI/API：保存合法回合、拒绝重叠/越界、预览帧选择最近时间戳。
- Merge：`rounds_confirmed.json` 存在时 `human_confirmed=true`。
- 复评：确认真值下旧/v1/v2 报告可对比。
- 回归：弱 `model_result` 仍为 `pending`；既有 FSM / 连续分析 / hybrid 测试通过。

## 非目标

- 不重写回合 FSM，不引入新模型依赖；
- 不把单场录制硬编码进检测逻辑；
- 不改变 `pending` / `vision_confirmed` / `export_deferred` 产品语义；
- 不在门禁失败时覆盖生产 ONNX。
