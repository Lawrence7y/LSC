# Valorant 混合视觉模型目录

运行时五分类 ONNX 模型与契约元数据放置于此：

| 文件 | 说明 |
| :--- | :--- |
| `valorant_phase_v1.onnx` | MobileNetV3-Small 五分类推理图（`non_game` / `buy` / `combat` / `result` / `replay`） |
| `valorant_phase_v1.json` | 契约元数据：`class_names`、`input_size`、`normalize_*`、`thresholds`、`sha256` 等 |

`ValorantFrameClassifier` 启动时校验 json 与 onnx 的 SHA-256 一致。

生产目录只接受 `scripts/valorant_vision/eval_source_dataset.py --mode
broadcast_runtime` 生成且 `gates_passed=true` 的 promotion report。使用
`promote_model.py` 激活；门禁失败、缺少独立来源会话或 SHA 不一致时保持当前
模型不变，并返回非零退出码。元数据中的 `promotion_state`、`promotion_report_path`
和 `rollback_model_sha` 用于追踪发布与回滚。

## 获取模型

1. **训练导出**（需标注数据集）：

   ```bash
   python scripts/valorant_vision/train_export.py \
     --data-dir ~/LSC/datasets/valorant_phase \
     --out-dir ~/LSC/models/valorant_phase_v1
   ```

   将生成的 `.onnx` 与 `.json` 复制到本目录。

2. **自定义路径**：设置环境变量 `LSC_VALORANT_MODEL_DIR` 指向包含上述两个文件的目录。

3. **CI / 单元测试**：使用 `tests/fixtures/valorant_vision/` 下的 stub 模型（`make_stub_onnx.py` 生成），勿将大体积生产 ONNX 提交到 Git。

## 注意

- `pov` 普通直播路径不加载该模型，继续使用纯 OCR 回合检测。
- `broadcast` 官方赛事/二路路径缺失模型、契约不匹配或推理失败时安全拒绝候选，禁止回退为猜测边界。
- `broadcast` 模型只作为 OCR 候选的阶段审计，不直接把单帧分类结果当作回合边界。
