# Valorant Phase 训练清单（JSONL）

每行一条 JSON 记录，描述从完整录像中抽取的单帧样本。原始录像与抽帧产物**不提交 Git**；仓库只保存清单格式、示例与脚本。

## 字段

| 字段 | 类型 | 必填 | 说明 |
| :--- | :--- | :---: | :--- |
| `video_id` | string | ✓ | 录像唯一 ID（同一会话内稳定，用于分组与去重） |
| `video_path` | string | ✓ | 本地绝对或相对路径，指向源 MP4/MKV 等 |
| `timestamp_sec` | number | ✓ | 帧在录像时间轴上的秒位置（浮点，≥ 0） |
| `label` | string | ✓ | 五分类标签，见下表 |
| `split` | string | ✓ | `train` \| `val` \| `test` |
| `source_type` | string | ✓ | `broadcast`（赛事转播）\| `pov`（第一视角） |
| `session_id` | string | ✓ | 独立录制会话 ID（同主播/同赛事的一整场或连续多场） |
| `notes` | string |  | 人工备注（困难样本、边界上下文、OCR 冲突等） |

### `label` 合法值

| 值 | 含义 |
| :--- | :--- |
| `non_game` | 舞台、广告、选人、加载、桌面等非回合画面 |
| `buy` | 买枪 / 准备阶段 |
| `combat` | 正常回合交战 |
| `result` | 回合结算 |
| `replay` | 赛事 Replay 或死亡回放 |

## 划分约束

1. **按完整录像分组**：同一 `video_id` 的所有行必须落在同一 `split`；不得把同一段录像的相邻帧拆到 train/val/test。
2. **按会话覆盖**：`broadcast` 与 `pov` 各自至少 3 个独立 `session_id`；每种 `source_type` 至少保留一整段 `test` 录像作最终盲测。
3. **禁止标签泄漏**：调参与阈值标定只用 `train`/`val`；`test` 标签在发布门前不得用于训练或阈值选择。

## 采样密度

- **边界附近（±3 s）**：买枪结束、结算出现、Replay 切入/切出等状态转换前后 3 秒内，提高抽帧密度（建议 2–4 FPS 或每 0.25–0.5 s 一行）。
- **稳定区**：可稀疏采样（如每 2–5 s 一行），避免大量相邻重复帧压倒边界样本。
- 清单可含多行同一 `(video_id, timestamp_sec)`；`extract_frames.py` 会按唯一键去重后只抽一帧。

## 输出目录（抽帧脚本）

默认写出到仓库外用户目录，避免误提交：

```
~/LSC/datasets/valorant_phase/<split>/<label>/<video_id>_<timestamp_ms>.jpg
```

也可用 `--output-dir` 指定；若放在仓库内，请使用已 gitignore 的路径（如 `datasets/valorant_phase/`）。

## 示例

见同目录 `example_manifest.jsonl`。
