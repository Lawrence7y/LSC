# 持续分析模块复盘：2026-08-04 实际运行日志分析报告

> 分析对象：`python-backend/handlers/room_handler.py`（持续分析主循环/worker）、
> `lsc/analyzer/round_detector.py`（hybrid 回合检测）、`valorant_round_fsm.py`（回合状态机）、
> `phase_scheduler.py`（相位调度）、`valorant_plugin.py`（扫描窗口规划）、`ocr_accel.py`（OCR 加速）。
> 运行日志：`%APPDATA%\lsc-electron\logs\backend.log`（2026-08-04 18:19–19:16，两段实况：房间 ca99c27、abc19e6）。

---

## 一、结论先行

1. **分析速度 ≈ 1× 实时（0.86–0.97×），永远追不上录制**，`analysis_lag_sec` 从 43.8s 一路涨到 328s。
   这是「输出晚于下一回合交手」和「回合确认极少」的**总根因**。
2. **回合漏检严重**：11 分钟只确认 2 回合、9 分钟只确认 4 回合（实际应有 5–6 回合），且**全部是 `pending`，没有一个 `vision_confirmed`**——按现有导出门（`_is_auto_exportable_valorant_round` 要求 `vision_confirmed`），**本轮全程 0 个切片被导出**。
3. **回合边界质量差**：出现 24–27s 的"回合"（正常 80–120s），且 `end` 锚在结算画面起点而非"下一回合准备阶段开始"，不符合需求语义。
4. **性能黑洞**：每轮窗口被相位调度器拉大到 136–173s（lookback_sec=120），但只推理新帧（7–54 帧）——抽帧/解码/JPEG/OCR 的绝大部分开销都花在**被丢弃的重复帧**上。
5. **架构可重构且值得重构**：当前"单 worker 串行：粗扫→OCR→chime→refine 全在一条链上"，无粗扫/精修分离，无独立追赶通道。

---

## 二、架构链路（现状）

```
[持续分析主循环 _continuous_analysis_loop]  每 effective_interval(5s 基准) tick
  ├─ 状态：last_analyzed / round_phase / pending_start / hybrid_runtime_state(FSM+分类器)
  ├─ 相位调度 next_round_phase() → 更新 round_phase、predicted_wake_at
  ├─ 窗口规划 plan_scan_window() → scan_range = [last_analyzed - lookback, current_dur]
  │     lookback 由相位决定：UNKNOWN/POST_COMBAT 可达 120–130s（UNIFIED_PROFILE.lookback_sec=120）
  ├─ kick worker（_continuous_valorant_worker，持全局分析信号量）
  │     └─ detect_valorant_rounds_hybrid(video, time_range=scan_range)
  │           ├─ 1) _detect_round_end_chimes()  全窗口音频提取+钟声检测（每轮全量）
  │           ├─ 2) extract_frames_cancellable()  全窗口 1fps 抽帧（mjpeg pipe + cv2 解码）
  │           ├─ 3) 按 last_processed_ts 过滤 → 只推理新帧（但抽帧/解码开销已全付）
  │           ├─ 4) 每帧：模型推理(DML) + 无条件 OCR(read_top_digit_anchors)
  │           ├─ 5) FSM.feed() → opened/closed 事件
  │           └─ 6) 每个 opened/closed 回合：_refine_hybrid_start/_refine_hybrid_end
  │                 再抽 30FPS±1.2s 窗口 + OCR + 推理（每回合额外 2–5s）
  ├─ 消费结果 → merge_round_windows → _auto_export_highlights(list_only=True)
  │     └─ confirm_status=pending → 只入列 clip_queued，export_deferred=True，不导出
  └─ 录制停止 → finalize 全量收尾扫描（_HYBRID_FINALIZE_OVERLAP_SEC）
```

---

## 三、今日日志实证

### 3.1 分析速度（最致命）

**房间 ca99c27（18:19:32–18:22:15，约 170s 墙钟）：**
| 时刻 | 已分析到 | 增量 | 耗时 | 速度 |
|---|---|---|---|---|
| 18:19:33 | 608.9s | — | — | — |
| 18:20:13 | 651.8s | ~43s | 40s | ~1.07× |
| 18:20:29 | 657.9s | 6s | 16s | 0.38×（窗口回退 538 起点） |
| 18:22:15 | 748.3s | ~90s | 106s | ~0.85× |
| 19:07 后 | 换房间重启，从 0 重扫 | | | |

**房间 abc19e6（19:06:35–19:16:54，547s 墙钟）：** 分析 9.9s → 541.0s，推进 531s 视频，耗时 547s，**速度 0.97×**。
`analysis_lag_sec`：18:19:32 时 43.8s → 19:16:45 时 **328.1s**（且最后一次更新后不再变化，录制已停止）。

> 结论：分析速度≈0.85–0.97× 实时，lag 必然单调增长。录制 10 分钟时，分析还停在 7 分钟前的画面；等切片输出时，直播早已进入 3–4 个回合之后。

### 3.2 回合产出

| 房间 | 视频时长 | 确认回合 | 全部 confirm_status | 实际应有 |
|---|---|---|---|---|
| ca99c27 | ~780s | 2（651.3–678.2、694.8–737.3） | pending | ~5–6 |
| abc19e6 | ~541s | 4（160.3–234.8、297.2–321.5、348.1–430.4、443.2–524.0） | pending | ~4–5 |

- 第二回合 297.2–321.5 仅 24.3s，明显是残段/误切（其余 74–82s 正常）。
- ca99c27 前 650s **一个回合都没确认**（OCR 早期 0 读取率 + 1fps 信号稀疏 + 窗口回退导致 FSM 上下文丢失）。

### 3.3 OCR 读取率时间线（房间 ca99c27）

```
18:19:32–18:19:41  ocr_read_rate=0.0   ocr_primary_active=False   ← OCR 完全不可用
18:19:42          0.667 / True
18:19:51          0.556
18:20:00          1.0
```

> 前 10 秒扫描窗口 OCR 全灭（初始化失败或首个窗口无有效读帧），导致开场约 600s 视频没有任何比分/计时器信号，FSM 既无法靠交战钟开局也无法靠比分变化闭合。

### 3.4 窗口回退（重复扫描证据）

```
18:20:13 kick range=622-658   (36s)
18:20:29 kick range=538-674   (136s)  ← 起点回退 84s！
18:20:46 kick range=554-690   (136s)
18:21:11 kick range=570-715   (145s)
...
19:10:02 kick range=36-180    (144s)  ← 起点回退 68s
19:11:54 kick range=148-292   (144s)
19:16:03 kick range=368-541   (173s)
```

> 相位调度器在 UNKNOWN（lookback≥90–120s）与 INTERMISSION 之间抖动时，`scan_start = last_analyzed - lookback` 把窗口起点拖回上百秒。抽帧/解码/JPEG/音频整窗执行，但推理只走新帧 → **每轮 70–80% 计算量是重复的**。

### 3.5 导出结果

全程仅见 `clip_queued`（export_deferred=True）+ `continuous_analysis_complete`（total_highlights=4, listed_clip_count=4）。
**没有任何 FFmpeg 导出任务产生切片文件**——因为所有回合 confirm_status=pending，`_is_auto_exportable_valorant_round()` 恒 False。

---

## 四、根因定位（按严重度排序）

### 根因 1：分析速度 ≈ 实时，无追赶能力（架构级）
- 单 worker 串行执行全部重活（抽帧→推理→OCR→chime→refine），一轮窗口 30–50s，期间录制又前进 30–50s。
- 每帧路径：FFmpeg mjpeg 编码 + cv2.imdecode + DML 推理 + RapidOCR 顶部条带 + （回合边界时）refine 密扫。实测约 **1s/帧**，而粗扫只有 1fps → 处理速度≈1× 实时。
- 无"快速追赶通道"：lag 一旦形成，窗口随 lag 膨胀（见根因 2），速度进一步下降，正反馈。
- `_window_scan_timeout` 给 OCR 窗 2×窗长+90s 超时，worker 实际在超时内跑完，未触发降级，但也没追平。

### 根因 2：窗口回退 + 大 lookback 导致重复计算（性能级）
- `phase_scheduler.py` UNIFIED_PROFILE：`lookback_sec=120`；UNKNOWN 相位强制 `max(lookback, 90)`。
- `scan_budget_for_phase()`：`start = max(0, last_analyzed - lookback)`。
- 相位在 UNKNOWN/INTERMISSION/COMBAT 间抖动 → 窗口起点忽前忽后（日志 538/554/570、36/60/148…）。
- `detect_valorant_rounds_hybrid` 虽用 `last_processed_ts` 过滤推理帧，但**抽帧、解码、JPEG 编解码、音频提取都是整窗的**——每轮 136–173s 窗口中只有 9–54s 是新的。

### 根因 3：回合确认门过严且信号缺失 → 永不导出（质量/功能级）
- `grade_round_confirmation()`：只有 `start_strong && end_strong` 或 `start_strong && score_confirm` 才给 `vision_confirmed`。
- FSM `_close_round()`：`score_increment_seen`（OCR 比分变化）是 vision_confirmed 的强前提；而 OCR 读取率 0.556–1.0，且有大量无计时器帧。
- `_is_auto_exportable_valorant_round()` 硬性要求 `vision_confirmed` → pending 永远不导出。
- 结果：即便检测到回合，也只"入列"，**用户要求的切片输出量为 0**。

### 根因 4：回合闭合依赖"下一回合买枪"（next_buy），输出天然滞后一整局
- FSM 主闭合路径：`ROUND_OPEN` 中连续 2–4 帧 buy（下一回合买枪）才 close，`end = result_seen_ts` 或 buy 起点。
- 即：必须等分析推进到**下一回合的买枪期**才能确认上一回合 → 在分析速度≈1× 的前提下，输出至少晚一局（~100s+），叠加 lag 328s，完全违背"下一回合交手前输出"。

### 根因 5：OCR 每帧无条件执行（效率级）
- `detect_valorant_rounds_hybrid` Step 1："无条件 OCR — 不再被分类器门控"。每帧跑 `read_top_digit_anchors()`（RapidOCR 顶部 12% 条带）。
- 即使 DML 加速，RapidOCR 每帧仍数百 ms，是 1s/帧 的主要成分之一；大量帧（直播间画面/黑场/replay）OCR 无意义。
- OCR 早期不可用（read_rate=0）无重试/预热机制，直接丢失开场的全部信号。

### 根因 6：回合边界语义与需求不一致（质量级）
- 需求："完整输出从**交手阶段**到**下一回合准备阶段的开始**"。
- 现状：`start` = 买枪游程首帧（model_buy_exit，含买枪阶段）；`end` = result 结算画面起点（不含回放，也不到下一 buy）。
- 且 24–27s 短回合说明 1fps 粗扫 + 弱信号下 FSM 开局/闭合不稳定（大概率被 next_buy 提前闭合或 mid-stream 误开）。

---

## 五、修改方案

### 方案 A：架构重构 — 粗扫/精修双阶段流水线（推荐，收益最大）

**目标**：粗扫速度 ≥ 2–3× 实时持续追赶，回合闭合即时化；边界精修异步化，不阻塞粗扫。

```
[粗扫通道 _coarse_scan_loop]              每 5s tick，窗口 = [last_analyzed, current_dur]（无 lookback 回退）
  ├─ FFmpeg GPU 硬解 + rawvideo(bgr) 直通 numpy（去掉 mjpeg/cv2.imdecode）
  ├─ 1fps 模型推理（DML）→ FSM.feed()
  ├─ OCR 只在 FSM 需要时做（WAIT_BUY/开局窗口/比分变化候选帧），其余帧跳过
  ├─ chime 音频检测增量缓存（RMS 只算新增段）
  └─ 回合 closed → 立即入"精修队列"，事件广播 pending 切片（先行入列）
       ↓（异步，不阻塞粗扫）
[精修通道 _refine_worker]                 队列消费
  ├─ _refine_hybrid_start/_refine_hybrid_end（30FPS 密扫 + OCR）
  ├─ 边界微调后升级 confirm_status
  └─ 触发实际导出（defer_export=False）
```

改动点：
1. `room_handler.py`：主循环拆两条异步任务（粗扫 worker + 精修 worker），共享 `_analysis_semaphore` 改为双信号量或按通道分锁（粗扫优先）。
2. `round_detector.py`：`detect_valorant_rounds_hybrid` 增加 `refine_async=True` 模式——closed 事件不就地 refine，而是返回事件列表由上层入队；opened 的 refine 也推迟（start 先用粗锚，精修通道补正）。
3. 抽帧：`extract_frames_cancellable` 的 mjpeg pipe 改为 `-f rawvideo -pix_fmt bgr24`，直接 `np.frombuffer().reshape()`，省掉 JPEG 编解码（约 0.2–0.4s/帧）。

### 方案 B：窗口/相位调度修复（低成本，立竿见影）

1. **lookback 收敛**：`UNIFIED_PROFILE.lookback_sec` 120 → 30；`scan_budget_for_phase()` 的 UNKNOWN/POST_COMBAT lookback 上限压到 40s；INTERMISSION 保持 30s。
2. **禁止窗口起点回退**：`scan_start = max(scan_start, last_analyzed - 30)`（仅允许 30s 上下文回看，防止 136–173s 大窗）。
3. **增量优先**：`scan_end = min(current_dur, last_analyzed + 60)`，单轮最多追 60s 新内容（`VALORANT_MAX_CATCHUP_SEC` 480→60 常态，追赶模式才放宽）。
4. **相位信号修正**：`_derive_round_signals` 的 `chime` 应同时驱动相位 POST_COMBAT（钟声=回合已结束的最强音频信号），避免一直 INTERMISSION/UNKNOWN 抖动。

### 方案 C：确认与闭合逻辑放宽（质量优先：宁多勿漏）

1. **闭合信号提前**：FSM 增加 `end_by="chime"` 闭合路径——`_detect_round_end_chimes` 检测到钟声落在 ROUND_OPEN 区间且距 open ≥30s 时，立即闭合（end=钟声点+尾段），**不再等 next_buy**。下一回合 buy 出现后由精修通道把 end 校正到"下一准备阶段开始"。
2. **确认门放宽**：
   - `grade_round_confirmation()`：`end_strong` 允许 `chime 命中 || ocr_timer_at_zero || score_confirm` 之一；
   - `_is_auto_exportable_valorant_round()`：允许 `confirm_status in (vision_confirmed, pending)` 且时长 ≥ 25s 即导出（用户明确要切片，pending 只是边界证据弱，不是假回合）；
   - 保留 `fake_round_dropped`（visual_combat<3 且无 strong）防假回合。
3. **边界语义对齐需求**：closed 时 `end` 初值 = result/chime 点；`_refine_hybrid_end` 改为向后找"下一买枪计时器首见帧"作为最终 end（=下一回合准备阶段开始），找不到再回退 result 点。

### 方案 D：性能修复（配合 A/B）

1. **OCR 门控**：`read_top_digit_anchors` 只在 `predicted in (buy, combat, result)` 或 timer 过期/比分变化候选时调用；`replay/non_game/unknown` 帧跳过（这几类帧占大量比例）。
2. **OCR 预热**：分析启动首个窗口强制跑 1 次 OCR 基准并重试，避免 0 读取率导致开场全丢。
3. **chime 增量检测**：RMS 音频缓存跨窗口复用，只对新段检测（`_get_cached_audio_pcm` 已有缓存，但要改为追加式而非整窗）。
4. **GPU 硬解链修复**：日志报 `Impossible to convert between the formats supported by the filter 'Parsed_scale_cuda_0' and 'auto_scale_0'`——修复 `build_hwaccel_vf` 的 scale_cuda 链（在 scale_cuda 后加 `format=nv12`/`hwdownload`），避免回退 CPU 软解。

### 方案 E：追赶兜底（防 lag 失控）

- 主循环检测 `lag = recorded_duration - last_analyzed > 120s` 时，进入**追赶模式**：
  - interval 压到 2s；窗口禁止回退；OCR 关；chime 关；refine 暂停；
  - 只做 1fps 粗扫 + FSM，直到 lag < 60s 再恢复精修。
- 录制停止 finalize 时，确保用**全量精修**（已有 `_finalize_scan_timeout` 加时），并把最终边界按方案 C-3 对齐。

---

## 六、预期效果（方案 A+B+C 组合）

| 指标 | 现状（今日实测） | 目标 |
|---|---|---|
| analysis_lag_sec | 43.8 → 328s（恶化） | 稳定 ≤ 30–60s |
| 回合检出率 | 2/5~6、4/4~5（漏 1/3） | 100%（不漏回合） |
| 导出切片数 | 0 | 每回合 ≥1 |
| 回合时长 | 24–82s 参差 | 80–130s 完整（交手→下一准备开始） |
| 输出时机 | 晚 2–4 个回合 | 下一回合交手前 |
| 窗口计算浪费 | 136–173s 窗/9–54s 新帧 | 30–60s 窗，无回退 |

---

## 七、实施顺序建议

1. **P0（立即，1–2 天）**：方案 B（lookback/窗口回退/增量上限）+ 方案 D-1/D-2（OCR 门控与预热）——不改架构先止血，预期速度提到 1.5–2×，lag 开始收敛。
2. **P1（3–5 天）**：方案 C（chime 闭合 + 确认门放宽 + 边界语义对齐）——恢复切片产出与完整性。
3. **P2（1–2 周）**：方案 A（粗扫/精修双通道架构）+ D-3/D-4（chime 增量、GPU 硬解修复）——达成"下一回合交手前输出"与 GPU 最大化利用。
