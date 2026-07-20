# Valorant 混合视觉模型目录

运行时五分类 ONNX 模型与契约元数据放置于此：

| 文件 | 说明 |
| :--- | :--- |
| `valorant_phase_v1.onnx` | MobileNetV3-Small 五分类推理图（`non_game` / `buy` / `combat` / `result` / `replay`） |
| `valorant_phase_v1.json` | 契约元数据：`class_names`、`input_size`、`normalize_*`、`thresholds`、`sha256` 等 |

`ValorantFrameClassifier` 启动时校验 json 与 onnx 的 SHA-256 一致。

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

- 仓库内**不**包含真实生产 ONNX（体积大、需标注数据训练）。
- 模型缺失时 Valorant 持续分析会报 `ModelContractError` 并禁用自动切片，不会回退旧音频/OCR 边界算法。
