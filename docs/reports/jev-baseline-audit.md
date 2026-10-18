# Jev 接入前基线摸底审计（只读）

- 审计对象：`D:\Project\直播切片多人`（LSC 直播切片系统），分支 `fix/debt-20260918`
- 审计日期：2026-09-20
- 审计性质：**纯只读**。未修改任何既有仓库文件；未执行 `git stash/checkout/reset/clean/commit`；未切换分支。仅新建两个交付物（本报告 + `baseline_rounds.json`）。
- 交付物：
  - `docs/reports/jev-baseline-audit.md`（本文件）
  - `D:\Project\browser-control-plugin\_lsc_ab\baseline_rounds.json`（UTF-8 无 BOM，20 条回合）

---

## 0. 一句话结论（先给答案）

> 现有数据**只够回答"Jev 与现行规则的一致率、分歧点、成本与延迟差"**，**不足以回答"Jev 质量更高"**——因为仓库里没有任何"这条回合值不值得切片/内容够不够精彩"的独立人工标注，唯一的"真值"是开发者手写的**导出门禁回归口径**；
> 更严重的是：**离线基线能填进冻结 `state` 的 21 个键里，只有 3 个（`room_id`/`mode`/`phase`）有真值**，`ocr_text` 与全部游戏内数值（比分/击杀/存活/爆能器/ace/clutch）**从不落盘**。因此按当前产物去喂 Jev，Jev 实际只能看到"模式=valorant_round、阶段=combat、时长≈63 秒"，**它没有可推理的素材**。

---

## 1. 证据分级（必须区分的两类事实）

| 标记 | 含义 | 本文覆盖 |
|---|---|---|
| **【读码】** | 我只读了源码/文档/数据文件得出的结论 | 全部决策路径与阈值 |
| **【实测】** | 我**本次实际运行** Python 脚本解析仓库数据得到的结果 | 数据清单统计、20 条回合抽取、规则与 `expected.json` 的 17/17 一致性 |
| **【他测】** | 仓库里**既有**的实测记录（不是我跑的，我读出来的） | 吞吐/滞后/单回合审计耗时 |

**我没有做的事**（不要当成做过）：没有运行 LSC 应用、没有跑 pytest、没有调用任何 OCR/ONNX 模型、没有发起任何网络请求、没有实测 Jev 端点、没有读 `settings.json` 之外的用户运行态数据。

---

## 2. 当前"高光/回合判定"决策路径

### 2.1 链路总览

```
视频帧(FFmpeg 抽帧 640×360)
   │
   ├─ 顶部条 OCR ──► (timer_seconds, left_score, right_score)   ← 仅运行期
   ├─ 中央横幅 OCR ──► prep/end/replay 横幅关键词
   ▼
OcrRoundFSM 状态机(WAIT/PREP/COMBAT/SETTLE)  ← 纯规则，无学习
   ▼ 产出候选 {start,end,phase=combat,confirm_status,start_by,end_by,round_key}
赛事视觉审计 audit_broadcast_rounds  ← 本地 ONNX 五分类模型 + 规则游程门
   ▼ 盖章 broadcast_audit / end_by / end_quality / confirm_status
room_handler._is_listable_ocr_round   ← 入列门禁(白名单+时长+去重)
   ▼ 广播 clip_queued（只入列，不导出）
前端 ClipList / canExportClip         ← 导出门禁
   ▼
剪映草稿 / FFmpeg 导出
```

关键文件与行号（【读码】）：

| 环节 | 位置 |
|---|---|
| 纯 OCR 回合状态机 | `lsc/analyzer/valorant_ocr_rounds.py`（`OcrRoundFSM` :394-673，`detect_valorant_rounds_ocr` :1160+） |
| 赛事视觉审计 | `lsc/analyzer/valorant_broadcast.py`（`audit_broadcast_rounds` :1630-3012，盖章 `_stamp_broadcast_decision` :652-768） |
| 编排入口 | `lsc/analyzer/pipeline.py`（`HighlightAnalyzer.analyze` :112-135） |
| 通用/回退路径 | `lsc/analyzer/scene_analysis.py` |
| 本地视觉模型 | `lsc/analyzer/valorant_frame_classifier.py`（onnxruntime） |
| 入列/广播/自动导出门禁 | `python-backend/handlers/room_handler.py`（`_is_listable_ocr_round` :1585，`_auto_exportable_valorant_round` :1651） |
| 服务端导出入口 | `python-backend/handlers/export_handlers.py` :696/:566；`timeline_handlers.py` :129/:175 |
| 前端导出门禁 | `lsc-electron/src/utils/clipExportPolicy.ts`（`canExportClip` :33-67） |
| 剪映草稿门禁 | `lsc/exporter/jianying_draft.py`（`clip_allowed_for_draft` :206，`_broadcast_gate_passed` :109） |

### 2.2 一次"回合判定"的输入信号是什么

**【读码】输入 = 图像帧 + OCR 文本，不含音频，回合主路径不含全帧语义模型。**

- 帧：FFmpeg 抽帧到内存管道，统一缩放到 **640×360**（`_HYBRID_EXTRACT_MAX_WIDTH = 640`，valorant_ocr_rounds.py:1799；:1834-1846）。
- 只裁 3 类 HUD 区域：顶部条（计时器+比分）、中央横幅（买枪/结算/回放）、赛事档宽 ROI 候选。
- **OCR 结构化字段**（`_parse_top_anchor_lines` :175-217）：
  - `timer_seconds`：正则 `(\d{1,2})\s*:\s*(\d{2})`（:187）
  - `left_score` / `right_score`：`\b(\d{1,2})\b` 且 0≤v≤30（:188,:199-202），按 OCR box 的 x 中心排序取最左/最右（:204-214）
  - 行级置信度过滤 **≥ 0.40**（:255, :317）
- 横幅关键词表：prep（:97-101）、end（:103-115）、replay（:120-123，**命中即否决** prep/end 判定）。
- **音频不参与**：`needs_audio=False`（valorant_plugin.py:419）。音频 RMS 包络只服务通用 scene 回退路径（scene_analysis.py:68-150）。
- **击杀信息不参与回合判定**：Kill Feed OCR 是独立支路，仅服务通用/回退路径（ocr_detector.py:24-25, :179 → scene_analysis.py:171-221）。
- **ace / clutch 已被显式删除**：`_END_BANNER_KEYWORDS` 里原本含 `clutch/ace/triple`，代码注释（valorant_ocr_rounds.py:110-112）说明它们是**解说高光回放的叠加字样**，会把回放误判成回合结束，已移除。

### 2.3 判据是什么

**【读码】主判据是确定性状态机 + 阈值比较，不是打分模型。**

`OcrRoundFSM`（:394-673）逐帧推进 WAIT→PREP→COMBAT→SETTLE：

- 入点 = 交战首帧（PREP→COMBAT :500-503；中段切入需连续 `_MIDSTREAM_STREAK=3` 帧计时器递减 :464-495）
- 出点优先级：`next_prep`（下回合准备首帧，:538/:549/:566）> `next_combat`（降级，:516-522/:585/:617-629）> `open_tail`（收尾强制闭合，:639/:1678-1681）

主要阈值（全部【读码】，名+值+行号）：

| 常量 | 值 | 行号 |
|---|---|---|
| `BUY_TIMER_MAX_SEC` | 45.0（交战钟下界） | valorant_ocr_rounds.py:42 |
| `_OCR_TIMER_MAX_PLAUSIBLE_SEC` | 105.0 | :43 |
| `_OCR_TIMER_JUMP_TOL_SEC` | 8.0 | :44 |
| `_OCR_TIMER_STALE_SEC` | 35.0 | :45 |
| `_MIN_ROUND_SEC` | 10.0（过短丢弃，:656） | :46 |
| `_PREP_AFTER_RESULT_SEC` | 6.0 | :47 |
| `_MIN_PREP_AFTER_COMBAT_SEC` | 30.0 | :48 |
| `_PREP_RUN_FRAMES` | 4 | :49 |
| `_SUB_WINDOW_SEC` | 60.0（分窗扫描） | :52 |
| `_REFINE_FPS` / `_REFINE_WINDOW_SEC` / `_REFINE_MAX_FRAMES_PER_BOUNDARY` | 5.0 / 3.0 / 80 | :58 / :56 / :60 |
| `_NEW_ROUND_CLOCK_MIN` | 85.0 | :63 |
| `_ROI_DIFF_THRESHOLD` | 2.0（ROI 帧差缓存） | :83 |
| `MAX_BROADCAST_ROUND_SEC` | 150.0（超长切块） | valorant_broadcast.py:24 |
| `END_LOOKAHEAD_SEC` | 90.0 | :31 |
| `BROADCAST_TIMER_SAMPLE_INTERVAL_SEC` | 4.0 | :34 |
| `START_GATE_SCAN_LIMIT_SEC` / `_EXTENDED_` | 15.0 / 35.0 | :80 / :81 |
| `GAP_SWEEP_SAMPLE_FPS` | 0.5（缺口补扫） | :3142 |
| 模型阈值 `thresholds.stable_prob` / `high_prob` | 0.55 / 0.8（来自模型 JSON 元数据，非硬编码） | `lsc/analyzer/models/*/valorant_phase_v1.json` |

**⚠️ 两个极易被误读的字段：**

1. **`confirm_status="vision_confirmed"` 与"视觉模型认定这是高光"无关。** 它的产出规则在 valorant_ocr_rounds.py:666：
   `confirm_status = "vision_confirmed" if end_by == "next_prep" else "pending"`
   即**出点是否落在"下回合准备首帧"**。赛事审计可在有截断证据时把它升级（valorant_broadcast.py:694-751），失败则降级回 `pending`（:753-768、:2879-2888、:2899-2903）。
2. **`score` 恒为 0.9，是硬编码常量**（valorant_ocr_rounds.py:669），不是任何模型置信度。夹具里 20 条回合的 `score` 全是 0.9 或 null。

### 2.4 现在有没有"云端大模型"参与判断？——**没有（本链路零云端 LLM）**

**【读码 + 我做了全仓 grep 验证】** 证据：

1. `lsc/analyzer/` 五个核心文件的全部 import 只有标准库 + `numpy`/`cv2`/`PIL`/`onnxruntime`/`rapidocr_onnxruntime` + 仓内模块，**没有任何 HTTP 客户端**。
2. 我对 `lsc/` 与 `python-backend/` grep `openai|anthropic|dashscope|chat/completions|api_key|TYPESAFE|systemone`：**除下文 §6 那个默认 `off` 的判决模块外，0 命中**（`TYPESAFE|systemone` 只命中 `lsc/analyzer/jev_judge.py` 自身）。
3. OCR 引擎 = RapidOCR（`rapidocr_onnxruntime`，PP-OCRv4）+ onnxruntime，provider ∈ `DmlExecutionProvider`(DirectML) / `CUDAExecutionProvider` / `CPUExecutionProvider`（ocr_accel.py:76-80）。
4. 视觉分类 = 本地 ONNX（`valorant_phase_v1.onnx`，五分类 `non_game/buy/combat/result/replay`），`ort.InferenceSession`（valorant_frame_classifier.py:318-320），**不联网下载**（模型由离线训练脚本导出后手工拷贝，`lsc/analyzer/models/README.md`）。
5. 唯一的外网调用在**平台流解析**（如 `api.live.bilibili.com`）与**依赖安装**，与判断无关。
6. 夹具里 `broadcast_model_provider="DmlExecutionProvider"` 正是 ONNX provider 名，不是云服务商。

> **口径结论（对 Jev 对比至关重要）**：现行判断 = **OCR 规则状态机 + 本地 ONNX 视觉分类 + 确定性门禁规则**。
> 所以"接入 Jev"**不是替换一个已有的 LLM**，而是**新增一层语义判断**——它无法与"被替换的旧 LLM 输出"逐条比准确率，只能与"现行规则判定"比**一致率/分歧率**，并比**成本与延迟增量**。

### 2.5 判定结果如何变成 clip

**【读码】** 状态机与门禁：

- 候选产生（valorant_ocr_rounds.py:654-673）→ 精修密扫（`refine_valorant_round_boundaries` :981-1094，**只有 `vision_confirmed + end_by==next_prep` 才密扫出点** :1047-1071）→ 赛事审计盖章 → 产出 `BroadcastAuditOutcome{accepted|pending|manual_review|rejected}`（valorant_broadcast.py:200-230）。
- 入列门禁 `room_handler._is_listable_ocr_round`（:1585）：
  - `boundary_source ∈ {valorant_ocr_v1, valorant_vision_sweep_v1}`（:488, :1480）
  - `confirm_status ∈ ("vision_confirmed","pending")`（:1589）；broadcast 档额外要求 `audit=="passed"`（`vision_confirmed` 分支 :1621-1628）
  - `start_by ∈ {ocr_combat, refined_combat, vision_gap_sweep}`（:489）；`end_by ∈ _OCR_VALID_END_BY ∪ _BROADCAST_VALID_END_BY`（:490-491, :1650）
  - 时长：入列下限 `_VALORANT_MIN_LIST_DURATION_SEC = 5.0`（:483）；**超长（>150s）只标 `duration_anomaly` 仍允许入列**（:1630-1639）
  - 去重键 `f"{recording_id}:{round_key}"`（:7252），抖动阈值 `_CLIP_BOUNDS_UPSERT_THRESHOLD = 0.3`（:2440）
- **自动导出当前不可达**：`_is_auto_exportable_valorant_round`（:1651）存在，但四个调用点全部显式传 `list_only=True`（room_handler.py:9370, :9379, :10635；analysis_handlers.py:737）⇒ `clip_queued` 只入列、不导 FFmpeg。这与 CLAUDE.md §8.4 的表述（"直接自动导出"）**不一致，以代码为准**。
- 导出门禁在前端 `canExportClip`（clipExportPolicy.ts:33-67），broadcast 档：`rejected` 一票否（:40）→ `user_confirmed` 直通（:41）→ `hasAuthoritativeBroadcastEnd` 直通（:18-24, :48-53）→ 否则要求 `vision_confirmed + audit==passed + 无复核标记`（:55-58）。
- **服务端导出入口不校验 `confirm_status`**：`export_handlers.py` 内 `confirm_status/pending/rejected` **0 命中**，只校验时间与房间（:576-579, :646-647, :665-669）。门禁 100% 在前端与剪映草稿侧。

---

## 3. 延迟 / 算力 / 成本基线

### 3.1 采样与算力配置【读码】

| 项 | 值 | 行号 |
|---|---|---|
| OCR 粗扫频率 | 默认 **1 fps**（`ocr_sample_interval=1.0`；下限 0.25 fps） | valorant_ocr_rounds.py:1170, :1216 |
| 分窗 | 60 s 一窗，重叠 2 s | :52, :1264-1276 |
| 边界密扫 | ±3 s @ **5 fps**，单边界 ≤80 帧 | :56-60 |
| 抽帧分辨率 | 640×360 | :1799, :1834 |
| 顶部条 OCR | 每帧 1 次（赛事最多 2 ROI），被 ROI 帧差缓存跳过 | :1312-1344 |
| 中央横幅 OCR | **非每帧**：4 s 哨兵 / 计时器跳变 / 比分变化 / post_settle_hold / SETTLE / finalize 才触发（1 fps 下 ≈ ≤0.25 次/帧） | :1369-1378 |
| 赛事计时器 OCR | 每 4 s 一次 | valorant_broadcast.py:34, :2373 |
| 缺口补扫 | 0.5 fps，120 s 分块 | :3142, :3229 |
| 并发上限 | `_ai_executor = ThreadPoolExecutor(max_workers=2)` | room_handler.py:1174 |
| 加速选择 | `ocr_accel` ∈ auto/dml/cuda/cpu；auto 用微基准探针，缓存 `data/ocr_accel_probe.json` TTL 7 天；失败回退 CPU | ocr_accel.py:25, :84-112, :125-179, :326-333 |
| FFmpeg 硬解 | dml/auto+Windows → `-hwaccel d3d11va`；cuda → `-hwaccel cuda`；四级 fallback 到软解/mjpeg | ocr_accel.py:346-410；valorant_ocr_rounds.py:1864-1874 |

### 3.2 实测数据【他测】（仓库既有记录，非本次运行）

**批量分析（sidecar 自带 `analysis_time_sec`）：**

| 用例 | 视频时长 | `analysis_time_sec` | 实时倍率 |
|---|---|---|---|
| 2045 | 1698.7 s | 1629.38 | **0.959×** |
| 0901 | 969.6 s | 1020.47 | **1.052×** |

**持续分析（`docs/reports/live-verify-*.json`）：**

| 用例 | 平均吞吐 | 最低吞吐 | 最大滞后 | 结束滞后 (P90) |
|---|---|---|---|---|
| 2045 | **0.838×** | **0.426×** | 311.5 s | 109.68 s (199.68 s) |
| 0901 | **1.55×** | **0.716×** | 43.3 s | — |

> 2045 的平均吞吐 **0.838× < 1**，即**分析追不上直播**；这正是该夹具场景里"出点未定稿/从未审计"的直接成因（`pending_lookahead`）。CLAUDE.md 与 `docs/reports/valorant-broadcast-runtime-investigation-20260908.md` 记录了同形现场（平均 1.262×，首窗最低 0.347×，`analysis_lag_sec=242`）。

**单回合赛事审计耗时（`docs/reports/reaudit-2045-20260911.json`）：** 2.344 s / 12.703 s / 15.375 s（3 条回合）。

**抽帧+分类吞吐（`reaudit-2045-caps-experiment-20260912.json`）：** 249 帧 / 20.41 s ≈ **12.2 帧/s**。

### 3.3 成本

**【读码】** 现行链路**零网络调用 ⇒ 云端成本恒为 0 美元**。主要成本是本地 GPU/CPU 时间 + FFmpeg 进程。仓库**没有**任何能量/机器成本折算记录。
【读码】`lsc/analyzer/jev_judge.py` 内有 `estimate_cost_usd(usage)`（:871）与单价常量 `INPUT_USD_PER_MTOK = 0.042`（:100），但**从未被调用**（见 §6）。

---

## 4. 可用数据清单（全部离线评估数据源）

统计方式：我实际遍历仓库并与 `*.analysis.json` / `*.finalization.json` / `*.jsonl` / `clips.json` / `expected.json` 模式匹配后逐文件解析。【实测】

### 4.1 回合级（**唯一可用**）

| 路径 | 大小 | 顶层结构 | 记录数 |
|---|---|---|---|
| `tests/fixtures/broadcast_export_case_20260911_2045/` | — | 见下 | — |
| ├ `….analysis.json` | 10 855 B | `{schema_version,room_id,video_path,mode,analyzed_at,analysis_time_sec,weights,highlights[]}` | **8 条 highlight** |
| ├ `….finalization.json` | 6 440 B | `{…,coverage_ranges,accepted_candidates[],rejected_candidates[],pending_candidates[]}` | accepted 3 / rejected 1 |
| ├ `clips.json` | 5 106 B | `{_note,request{...},clips[]}`（**`backend.log` WS 报文原文**） | **8 条** |
| ├ `authority_snapshot.json` | 5 687 B | `{_note,room_id,recording_id,saved_at,listed_clips{key→clip}}` | **8 条** |
| ├ `expected.json` | 894 B | `{_note,counts,included[],skipped{round_key→{reason_code,note}}}` | included 4 / skipped 4 |
| └ `README.md` + `.mp4` | 3 299 B / 0 B | 现场说明 / 0 字节占位 | — |
| `tests/fixtures/broadcast_export_case_20260912_0901/` | — | 同上（**无 authority_snapshot.json**） | analysis 8 / finalization accepted 7 + rejected 4 / clips 9 / expected included 4 + skipped 5 |

### 4.2 会话/扫描级（仅延迟基线可用）

| 路径 | 大小 | 结构 |
|---|---|---|
| `docs/reports/live-verify-2045-20260911.json` | 313 453 B | `{summary{吞吐,滞后,…}, scans[]{kick_at,range,wall_sec,media_sec,throughput}, status 快照序列}` |
| `docs/reports/live-verify-0901-20260912.json` | 77 152 B | 同上 |
| `docs/reports/reaudit-2045-20260911.json` | 3 217 B | `results[]{round_key,input,elapsed_sec,outcomes[]{status,reason}}` |
| `docs/reports/reaudit-2045-caps-experiment-20260912.json` | 49 549 B | `experiment_1_label_probe{probed_range,n_frames,extract_sec,label_counts,timeline[…{t,label,probs}]}` |
| `docs/reports/valorant-broadcast-runtime-investigation-20260908.json/.md` | 463 613 B / 5 717 B | 现场事故调查（含强停分析） |

### 4.3 帧级 phase 标签（**有真人标注，但不可对齐到回合**）

| 路径 | 大小 | 记录数 | 其中人工标注 |
|---|---|---|---|
| `scripts/valorant_vision/manifest_phase_combined.jsonl` | 2 855 279 B | **7 875** | 0 |
| `scripts/valorant_vision/manifest_broadcast.jsonl` | 1 654 795 B | 3 655 | 0 |
| `scripts/valorant_vision/manifest_pov.jsonl` | 1 345 597 B | 3 376 | 0 |
| `scripts/valorant_vision/manifest_broadcast_retrain_20260910.jsonl` | 1 224 511 B | 3 706 | 0 |
| `scripts/valorant_vision/manifest_ns_g2_20260907_human_validated.jsonl` | 517 072 B | **897** | **897（100%，`label_source=human`/`human_reviewed`）** |
| `scripts/valorant_vision/manifest_replay_verified_20260910.jsonl` | 27 912 B | 51 | 51（人工确认 replay） |
| `datasets/valorant_phase_broadcast/incoming_ns_g2_high_conf_review/manifest_labeled.jsonl` | 247 974 B | 789 | 789（`notes="用户逐帧复核"…`） |
| `datasets/valorant_phase_broadcast/incoming_edg_review/manifest_labeled.jsonl` | 170 524 B | 527 | 527 |
| `datasets/valorant_phase_broadcast/incoming_edg_review_round2/manifest_labeled.jsonl` | 15 481 B | 48 | 48 |
| `datasets/valorant_phase_broadcast/incoming_ns_g2_low_conf_validation/manifest_labeled.jsonl` | 30 546 B | 108 | 108 |
| `scripts/valorant_vision/example_manifest.jsonl` | 1 258 B | 5 | 0（示例） |

统一结构：`{video_id, video_path, timestamp_sec, label, split, source_type, session_id, [frame_path, notes, label_source, coarse_*]}`；`label ∈ {buy, combat, result, replay, non_game}`。

> **这是仓库里唯一规模可观的真人标注（phase），但对本任务不可用**：① 它是**帧级 phase**，不是回合级游戏状态，也不是"值不值得切片"；② 其 `video_path` 指向 `/mnt/d/…` 的**另几场录像**（2026-09-07 EDG、NS_vs_G2），与两份回合夹具的录像（`2026-09-11_20-10-29`、`2026-09-12_08-44-04`）**不同源**。
> 我已实测：用 `2026-09-11_20-10-29`/`2026-09-12_08-44-04` 作为关键词全仓搜索，**没有任何 jsonl 引用这两段录像**。**故已跳过，未硬凑。**

### 4.4 明确无法映射的数据源（如实说明并跳过）

| 数据源 | 跳过原因 |
|---|---|
| 全部 `manifest_*.jsonl`（§4.3） | 帧级 phase，无回合级游戏数值；且与夹具录像不同源，无法对齐 `round_key` |
| `tests/fixtures/valorant_vision/valorant_phase_v1.json/.onnx` | 模型元数据/权重，不是判定记录 |
| `docs/reports/valorant-broadcast-runtime-investigation-20260908.json` | 单场事故的会话级快照，其回合实体已被 §4.1 两个用例覆盖且录像不同源 |

---

## 5. 有没有 ground truth？

**结论：没有"内容层面"的 ground truth；只有"门禁层面"的人工口径。**

1. **存在的人工口径** = 两个 `expected.json`：
   - 2045 的 `_note`：`"P1 修复后的期望口径：105 因权威快照可达而回归入列；其余保持不放宽（出点未定稿/未审计/无排除证据）"`
   - 0901 的 `_note`：`"P1/P2 修复后的期望：065 入列且用权威精修出点 789.75；残差必须为 0。"`
   - `README.md` 第 23 行自述其来源：`| expected.json | 人工口径 | 修复后应得的 included / 逐条 reason_code |`
2. **它的语义是"是否应进入导出草稿"**（逐条 `reason_code` 如 `END_NOT_FINAL`/`NEVER_AUDITED`/`NO_EXCLUSION_EVIDENCE`/`REJECTED`），**不是"这条回合内容是否精彩/值不值得切片"**。
3. **它不是独立标注**：写它的人＝写门禁规则的人，用途是**回归锁定**，不是第三方评判。
4. 另一个方向的人工标注（§4.3 的 897/789/527 帧）是 **phase 标注**，答的是"这帧是买枪还是交战"，同样是**过程属性**而非**精彩度**。
5. **不存在**：`clips.json`/`authority_snapshot.json` 是程序自己的历史输出（前端快照/后端权威快照）；`analysis.json`/`finalization.json` 同理。

> **这个区别决定了口径**：能谈**一致性 / 一致性差异 / 成本 / 延迟**；**不能谈"质量更高"**。若将来要谈质量，必须新采一份独立标注（人工对同一批回合打"值不值得切片"，且标注人不参与规则设计）。

**顺带的一个重要实测结论【实测】**：我把"当前判定"用固定规则从夹具快照重建（见 §7.1），与两份 `expected.json` 逐条比对，**17 条有真值的回合 17/17 完全一致，0 分歧**。
这说明：**夹具里的"当前判定"本身已经被修复后的口径覆盖** —— 换言之，这批数据**天然无法区分"规则"与"期望口径"**，它能验证门禁语义，但**不能作为质量判据**。

---

## 6. ⚠️ 重要发现：`lsc/analyzer/jev_judge.py` 已存在，且**审计期间接线落地**

> ⏱ **并发变更声明**：本次审计进行中，仓库被**另一个 worker 并发修改**。我实测到这些文件的 mtime 都在审计窗口内：
> `lsc/analyzer/jev_judge.py`（9:45:25 → **9:49:40**，35 870 B → **37 906 B**）、`python-backend/handlers/room_handler.py`（**9:48:41**）、`python-backend/settings.json`（**9:48:41**）。
> 本节结论已按 **09:50 的快照**复核修正；在此之前我一度读到的是"已存在但未接线"。**下文以最新复核为准。

**【实测】** 该文件**已存在于工作树**：

- `D:\Project\直播切片多人\lsc\analyzer\jev_judge.py`，35 870 B，mtime `2026/9/20 9:45:25`
- `git status --porcelain` → `?? lsc/analyzer/jev_judge.py`（**未跟踪**，不属于那 189 个已跟踪改动）
- 另有 `.runtime/jev-baseline-porcelain.txt`（`git status --porcelain` 的存档）与 `__pycache__/jev_judge.cpython-312.pyc` ⇒ 该模块**已被 Python 编译过**，说明有人跑过它

**【读码】冻结接口已 100% 落实**（我逐行核对）：

| 冻结约定 | 文件中的实现 |
|---|---|
| `@dataclass(slots=True) class JudgeVerdict` 10 字段同序 | :233-244（`worth_clipping,p_clip,excitement,reason,confidence,model,latency_ms,usage,source,error=""`） ✅ |
| `def judge_round(state: dict, *, timeout: float = 8.0)` | :756，`DEFAULT_TIMEOUT_SEC = 8.0`（:90） ✅ |
| 端点/模型/key 环境变量 | `JEV_ENDPOINT`(:71)、`JEV_MODEL="jev-latest"`(:74)、`JEV_API_KEY_ENV="TYPESAFE_API_KEY"`(:77)，只从 `os.environ.get()` 读 ✅ |
| `highlight_judge_mode` 默认 off | `MODE_OFF/SHADOW/LIVE`(:80-82)、`SETTINGS_MODE_KEY`(:85)、`DEFAULT_JUDGE_MODE=MODE_OFF`(:87) ✅ |
| `highlight_judge_min_confidence` 默认 0.6 | :86, :88 ✅ |
| 无 key/超时/失败不抛异常、`worth_clipping=False`、`source="disabled"` | `_failed_verdict`(:574)、`judge_round` :781/:791-804 ✅ |
| 总耗时不超过 timeout（含退避重试） | `call_jev` :673-688 用 `deadline` 约束 ✅ |

**【实测】接线状态（09:50 复核）= 已接线，默认 off，零行为变化：**

| 接线点 | 位置 |
|---|---|
| 读 `highlight_judge_mode`；off/缺失时**不 import** 判决模块（廉价短路） | `room_handler.py:1706-1728`（`_jev_mode_and_threshold`，:1717-1719 短路，:1721 才 import） |
| 冻结 state 映射 | `room_handler.py:1733-1769`（`_jev_round_state`，透传键表 `_JEV_PASSTHROUGH_KEYS` :1700-1705） |
| 判决执行（同步 `judge_round` 丢进 `_ai_executor`，8 s 预算） | `room_handler.py:1770-1780`（`_jev_judge_one`，`_JEV_TIMEOUT_SEC = 8.0` :1696） |
| shadow 模式 fire-and-forget / live 模式门控 | `room_handler.py:1790+`（`_jev_shadow_worker`）、:1856-1883（`gate_out_reason`/`reason_text`，`LIVE_GATE_FAIL_OPEN` 放行）、:9615（shadow 落 `data/jev_judge_shadow.jsonl`） |
| 设置默认值（写盘兜底） | `room_handler.py:4305-4306`（`'highlight_judge_mode': 'off'`、`'highlight_judge_min_confidence': 0.6`） |
| 运行态 `settings.json` | `python-backend/settings.json:19-20`（`"highlight_judge_mode": "off"`、`"highlight_judge_min_confidence": 0.6`） |

**⇒ 结论**：判定链路上**默认仍然是零行为变化**（`off` + 未 import）。§2.4 的结论不变：**当前判断中没有任何云端大模型参与**。

### 6.1 接线作者独立得出了与本报告相同的"数据缺口"结论（互相印证）

`_jev_round_state` 的 docstring（`room_handler.py:1741-1747`）明确写：

> 「⚠️ 诚实边界：当前分析管线**不产出**回合级游戏语义字段（kills_self / spike_planted / ace / clutch_1vN / round_time_left_sec 等），所以这里一律**不填、不猜**——jev_judge 会把它们明确列为 NOT AVAILABLE。」
> 「同理**不往 extra 里塞边界/审计元数据**：那是「切得准不准」的证据，与「值不值得切」无关。」

这与本报告 §7.2 的映射表**独立地得出同一结论**。并且 `judge_round` → `build_state_text`（jev_judge.py:346-513）**只读冻结的 state 键**、**不读 `state["extra"]`**【读码】⇒ 即使在 `extra` 里塞满边界/审计证据，Jev 也看不到。

**⇒ 所以：上线的 `state` 将只有 `room_id/platform/game/mode/round_no/phase`，其余 14 键 NOT AVAILABLE。** 按现状喂 Jev，判决**不可用于评估其能力**；要让 A/B 有意义，**必须先补采 state**（见 §9）。

### 6.2 🔴 新发现的缺陷：`round_no` 会被送成**语义错误**的值

`room_handler.py:1729-1732`：

```python
def _jev_round_no(round_data):
    """从 round_key（形如 round-000105）解析回合序号；解析不出返回 None（不猜）。"""
    match = re.search(r'\d+', str(round_data.get('round_key') or ''))
    return int(match.group(0)) if match else None
```

**但 `round_key` 不是比赛回合号**，而是 `round-{int(round(start_sec/10))}` 的**10 秒时间桶**（`lsc/analyzer/valorant_ocr_rounds.py:708-710`；同口径见 `docs/reports/valorant-broadcast-runtime-investigation-20260908.md:48`「前者按起始时间十秒桶生成，不是比赛的 Round number」）。

**后果**：夹具 `round-000105`（实际是地图内第 8 个左右的回合）会被当成"第 105 回合"送给 Jev；一场 28 分钟的录像会产出 `round_no` 高达 1600+ 的"回合号"。这**不是缺失，而是错误值**——比 null 更糟，因为 `build_state_text` 会把缺失写成 `unknown`（安全），却会把错误的 `round_no` 当作**明文事实**陈述给模型（jev_judge.py:377-379：`"- Round number in the match: 105."`）。

**建议**：`_jev_round_no` 应返回 `None`（或先修 `round_key` 语义）；在此之前不要开 `shadow`/`live`。

**坐实这一点的对照**：本次基线 `baseline_rounds.json` 中 `round_no` 一律为 `null` 并附原因，其 `state_key_mapping` 明确写着「不是比赛回合号：`round_key='round-'+(start_sec/10)` 十秒时间桶，不可反推回合号」。

---

## 7. 冻结 state 的映射表

### 7.1 `baseline_rounds.json` 的判定规则（写进 JSON 的 `decision_rule`）

```
worth_clipping := (broadcast_audit == "passed")
                  AND (end_by == "broadcast_exclusion" OR end_quality == "precise" OR boundary_quality == "precise")
若该回合出现在 finalization.rejected_candidates  → false（decided_by="rule"）
若既无 passed 也无 rejected 终态              → false（decided_by="none"，表示"快照时无终态结论"）
```

字段合并优先级：`finalization > authority_snapshot > clips > analysis`（终态优先，逐字段记录来源在 `extra.field_source`）。

**【实测】** 该规则 vs `expected.json`：**17/17 一致**（`summary.decision_vs_ground_truth.disagree = 0`）。

### 7.2 21 个冻结键的映射结果

| 冻结键 | 可映射 | 来源 / 不可映射原因（带行号） |
|---|---|---|
| `room_id` | ✅ | `analysis.json.room_id`（`clips.json.request.room_id` 一致） |
| `platform` | ❌ | 任何产物都无平台字段 |
| `game` | ❌ | **无 `game` 字段**。只有 `mode="valorant_round"` 与 `valorant_profile="broadcast"`。按"缺失即 unknown、不要猜"的约定**写 null**；原始值保留在 `extra.session` 供下游自行派生 |
| `mode` | ✅ | `analysis.json.mode` = `"valorant_round"` |
| `round_no` | ❌ | **`round_key` 不是比赛回合号**：`round_key = "round-" + int(round(start_sec/10.0))`（valorant_ocr_rounds.py:708-710；valorant_broadcast.py:1131/:1145；continuous_finalization.py:461；valorant_plugin.py:186）。它是 **10 秒时间桶**。仓库文档同口径：runtime-investigation-20260908.md:48「前者按起始时间十秒桶生成，不是比赛的 Round number」 |
| `phase` | ✅ | `analysis.json.highlights[].phase`（20 条全是 `"combat"`） |
| `score_self` / `score_enemy` | ❌ | 顶部条 `left_score/right_score` 只在运行期解析（:175-217），仅作边界判据（:1365-1375），**不落盘** |
| `round_time_left_sec` | ❌ | 同上（`timer_seconds`，`_is_combat_timer` :681-682），不落盘 |
| `kills_self` / `kills_enemy` / `deaths_self` | ❌ | 回合链路不解析击杀；Kill Feed OCR 仅服务通用回退路径（ocr_detector.py:24-25, :179），结果不入库 |
| `alive_self` / `alive_enemy` | ❌ | 无该信号 |
| `spike_planted` / `spike_defused` | ❌ | 仅以横幅关键词（`"尖刺已引爆"`/`"spike deton"`/`"尖刺已拆除"`/`"spike defus"`，:108/:114）参与出点判定，**无数值/布尔量落盘** |
| `ace` / `clutch_1vN` | ❌ | 词表已**显式删除** `clutch/ace/triple`（:110-112，因它们是高光回放叠加字样，会误判） |
| `ocr_text` | ❌ | **OCR 文本从不落盘**——这是最大的不可复现点：离线无法重放真实判定输入 |
| `recent_events` | ✅ | 无事件流落盘，一律 `[]`（表示"无事件"而非 unknown） |
| `extra` | ✅ | 该回合在各快照的原始字段 + `field_source` + `data_gaps` 说明 |

**映射率：21 键中 5 键有真值（`room_id`/`mode`/`phase`/`recent_events`/`extra`），其中只有 3 键是实质语义（`room_id`/`mode`/`phase`）；16 键为 null。**

---

## 8. 交付物 `baseline_rounds.json`

### 8.1 内容

路径 `D:\Project\browser-control-plugin\_lsc_ab\baseline_rounds.json`（UTF-8 **无 BOM**，95 000 B，已实测前 3 字节非 `EF BB BF`）。

```
{schema_version, generated_by, generated_at, note, source_files[], decision_rule,
 ground_truth_semantics, summary{}, latency_baseline{}, state_key_mapping[],
 data_inventory[], unmapped_sources[], rounds[20]}
```

每条 round：
```json
{"id":"2045:round-000045","source":"tests/fixtures/.../finalization.json","case":"2045",
 "state":{ …21 个冻结键…, "extra":{…} },
 "current_decision":{"worth_clipping":true,"decided_by":"rule","signal":"kind=terminal_accept; broadcast_audit=passed; …"}}
```

### 8.2 数量与覆盖【实测】

- **20 条回合**（`2045` 9 条 + `0901` 11 条），达到"至少 20 条"要求。
- 其中 **17 条带 `ground_truth`**（`expected.json` 的人工口径），3 条为 null（`0901:round-000010-s1`、`0901:round-000093`、`2045:round-000093`）。
- `decided_by` 分布：`rule` 15 条 / `none` 5 条 / `ocr` 0 / **`llm` 0**。
- 规则与真值一致性：**17/17（0 分歧）**。

### 8.3 已知局限（必须让下游知道）

1. **state 太薄**：16/21 键为 null，且 `ocr_text` 为 null ⇒ **按现状喂 Jev，判决不可用于能力评估**（见 §6.1）。
2. **样本只有 2 场录像、20 条回合**，全部来自**同一支赛事的回顾录像**（`EDG夺冠回顾`），且带**明确的修复现场偏置**（`expected.json` 是为 P1/P2 修复写的）。**不能当泛化指标**。
3. **快照互相矛盾**，这是真实现象而非我的抽取错误，例：
   - `2045:round-000105`：`clips.json`/`authority_snapshot.json` = `audit=passed, end_by=broadcast_exclusion, end_quality=precise`，但 `analysis.json` 里仍是 `pending_lookahead`（`extra.field_source` 逐字段标了来源），且其 `confirm_status` 在三份快照里都是 `pending`——而按 `_stamp_broadcast_decision` :699-703，`audit=passed + end_by=broadcast_exclusion` 应伴随 `vision_confirmed`。**这是快照自身的不一致，我只如实记录，不做修正。**
   - `2045:round-000135`：`clips.json` = `pending_lookahead`；`authority_snapshot.json`（经 README 说明按 `backend.log` 实测结论修正过）= `passed/vision_confirmed`。
4. `source_files` 里的绝对路径已转为**仓库相对路径**（正斜杠），便于移植。

---

## 9. 一句话结论：在现有数据条件下能回答什么

> **能回答的**（今天就做得了）：
> ① Jev 与现行规则在 20 条回合上的**逐条一致 / 分歧率**（有 17 条还有门禁口径可比）；
> ② Jev 的**成本与延迟增量**（现行链路云端成本恒为 0，延迟基线见 §3）；
> ③ Jev 判决的**稳定性/可解析性**（能否在 8 s 预算内稳定返回结构化 `JudgeVerdict`）。
>
> **不能回答的**：**"Jev 质量更高"**——仓库里不存在"值不值得切片"的独立人工标注，唯一的真值是开发者手写的门禁回归口径，且它与现行规则 **17/17 一致**，**天然无区分度**。加上 state 的 16/21 键为 null（含 `ocr_text`），Jev 在现有数据上**连可推理的素材都没有**。
>
> **要谈质量，必须先做两件事**：**(A) 补采 state**（至少把 OCR 文本、比分、计时器落盘，或让判定在运行期就地喂 Jev）；**(B) 新采一份独立人工标注**（"这条回合值不值得切片"，标注人不得是门禁规则的作者）。

---

## 10. 复现方式

本报告的全部数据结论由一段**只读** Python 脚本在仓库外（`%TEMP%`）生成，未写入仓库。复现要点：

```powershell
$env:PYTHONIOENCODING='utf-8'
# 解析：tests/fixtures/broadcast_export_case_*/{*.analysis.json,*.finalization.json,clips.json,authority_snapshot.json,expected.json}
# 合并字段优先级：finalization > authority > clips > analysis
# 判定规则与 ground_truth 比对，输出 rounds / summary
```

- Python：`C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe`（3.12.10）。**项目无 `.venv`**，未安装任何新包。
- 所有文件读写显式 `encoding="utf-8"` / `"utf-8-sig"`（夹具带 BOM，读取时用 `utf-8-sig`）；写出用 `utf-8` 无 BOM。

## 11. 不确定项（不编造）

1. 【读码】`valorant_broadcast.py`(3 329 行) 与 `room_handler.py`(579 KB) 为**分区精读**，不是逐行通读；结论所依据的行号均已核对，但同文件其它分支可能影响边界情形。
2. 【读码】生产持续分析**实际传入**的 `sample_fps`/`ocr_sample_interval`/`lookahead_sec` 由调用点决定（room_handler 持续分析循环、valorant_plugin.py:455/:761）；我只确认了函数默认值与 `room_handler.py:2721` 的换算，**未完整读这些调用点**，故 §3.1 的频率是"函数默认值"口径。
3. 【读码】本机 onnxruntime 是否真启用 DML/CUDA、`data/ocr_accel_probe.json` 当前内容：**未执行、未读取**。实际生效 provider 无法从静态代码确认（夹具里记录的是历史上生效过的 `DmlExecutionProvider`）。
4. 【读码】`_refine` 的 docstring 写 10 fps（valorant_ocr_rounds.py:991, :1039-1045）但常量是 `_REFINE_FPS=5.0`（:58）：我判断为**注释过时**，无法排除上层运行时覆盖（该函数签名无 fps 形参）。
5. 【读码】`confirm_status` 的 `ocr_confirmed`/`audio_pending` 在 Python 侧**无写入点**（仅白名单/前端/类型），是否由旧 sidecar 或外部调用带入未确认；`tests/test_continuous_analysis_guards.py:1585` 断言源码里已无 `audio_pending`。
6. 【读码】`_auto_export_highlights` 的自动导出分支"当前不可达"是**按当前代码静态判定**（4 个调用点全传 `list_only=True`）；若未来有调用方用默认参数（`list_only=False`，:7160），该分支即复活。
7. 【读码】非 broadcast 档在 `_is_listable_ocr_round`（:1648-1650）与 `_is_auto_exportable_valorant_round`（:1675-1682）**都不检查 `broadcast_audit`**——是有意还是遗漏，代码与注释未说明。
8. 我**未**核实 `lsc/analyzer/jev_judge.py` 的实现细节是否全部正确（例如 `build_payload` 的字段裁剪、响应解析的容错）；本报告只核对了**冻结接口的签名/字段/默认值/失败语义**，以及 §6.2 的 `round_no` 缺陷；未做端到端运行验证（未开 shadow/live、未发起任何 Jev 请求）。
