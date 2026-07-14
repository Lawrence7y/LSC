# 高光分析优化计划

## 版本信息
- 日期: 2026-07-07
- 状态: 待评审
- 关联文档:
  - `docs/AI高光分析优化与多房间同步导出_全量修改计划.md`（前一轮 P0-P2 优化，已落地）
  - `docs/superpowers/specs/2026-06-06-valorant-highlight-pilot-design.md`（无畏契约试点设计）
  - `.qoder/specs/AI高光分析接入方案_6af43e73.md`（原始接入方案，已落地）

---

## 1. 背景与问题诊断

### 1.1 现状

高光分析系统已落地五种模式（scene / ai / combined / fast / CLI），针对无畏契约做了专项优化（OCR Kill Feed 检测、回合分组、Valorant 融合权重等）。前一轮 P0-P2 优化已修复持久化、进度回调、格式统一等问题。

### 1.2 用户实测反馈

| 模式 | 问题 | 根因 |
|------|------|------|
| **scene** | 切片质量不稳定 | 检测信号太弱（仅像素差异 + 音频 RMS），回合分组用固定 35s 间隔不适配所有回合类型，音频回退阈值过粗 |
| **ai / combined** | 短时间内无法产出切片 | Whisper 转录 60-120 分钟（CPU）、OCR 逐帧串行 15-40 分钟、CLIP 推理 15-40 分钟（CPU），三者串行执行 |
| **fast** | 短时间内无法产出切片 | OCR 全量逐帧扫描同样耗时 15-40 分钟，"30-60s 完成"的估算仅适用于短视频 |

### 1.3 代码审查发现的 Bug

| 编号 | 文件:行号 | 问题 | 影响 |
|------|-----------|------|------|
| Q1 | `pipeline.py:71` | `_estimate_top_percent` 用 `s.get("timestamp", 0)` 取时长，但 speech_scores 和 scene_scores 无此字段 | 长视频时长估算永远为 0，永远用最宽松 15% 阈值，产出过多低质量片段 |
| Q2 | `pipeline.py:409` | `source in ("ocr", "sound")` 把所有声音事件（主播反应、回合结束）当击杀事件参与回合分组 | 片段范围被错误拉伸 |
| Q3 | `pipeline.py:317+322` | OCR 事件既参与融合加权，又作为独立片段追加 | 可能产出重复片段 |
| Q4 | `pipeline.py:274-289` | OCR/Sound 进度未映射到 pipeline 整体区间 | 进度条从 90% 跳回 0% |
| Q5 | `pipeline.py:360-362` | 先 `_merge_close_segments` 再 `_deduplicate_highlights`，顺序反了 | 不同类型片段被错误合并后无法去重 |
| Q6 | `room_handler.py:219` | 音频能量回退取 75 百分位作为阈值 | "音量大"≠"精彩"，buy phase 选枪界面也被选中 |
| Q7 | `fusion.py:441-448` | 结束边界精化只在 `[orig_end, orig_end+range]` 搜索，无法向后扩展 | 与起始边界可向前扩展的逻辑不对称 |

---

## 2. 优化方案总览

按投入产出比排序，分三个层次：

```
第一层：质量修复（Q1-Q7）  ← 改动小、立即见效、修复后 AI 模式质量直接提升
第二层：性能加速（P1-P8）  ← 让 fast 模式真正"快"，AI/combined 可接受时间内完成
第三层：架构改进（A1-A5）  ← 提升 scene 模式质量上限、融合自适应
```

### 实施顺序

```
阶段一  Q1 → Q2 → Q3 → Q4 → Q5 → Q6 → Q7     （质量 Bug 修复）
阶段二  P1 → P2 → P4 → P3 → P8                （OCR + Sound 性能优化）
阶段三  A1 → A2                                （Scene 模式引入轻量 OCR）
阶段四  P5 → P6 → P7                           （CLIP + Whisper 性能优化）
阶段五  A3 → A4 → A5                           （架构改进）
```

---

## 3. 阶段一：质量修复（Q1-Q7）

### Q1: 修复 `_estimate_top_percent` 时长估算 Bug

**文件**: `lsc/analyzer/pipeline.py:68-80`

**问题**:
```python
for scores in (speech_scores, visual_scores, scene_scores or []):
    for s in scores:
        ts = s.get("timestamp", 0)   # ← speech_scores 无 "timestamp"，scene_scores 也无
        if ts > max_ts:
            max_ts = ts
duration = max_ts
```

- `speech_scores` 格式: `{"start", "end", "text", "speech_score", ...}` — 无 `timestamp`
- `scene_scores` 格式: `{"start", "end", "score"}` — 无 `timestamp`
- 仅 `visual_scores` 有 `timestamp`
- 后果：CLIP 失败时 `max_ts=0`，`duration < 300` 恒为 True，`top_percent` 永远 0.15（最宽松）

**修复**:
```python
for scores in (speech_scores, visual_scores, scene_scores or []):
    for s in scores:
        ts = s.get("timestamp", s.get("end", s.get("start", 0)))
        if ts > max_ts:
            max_ts = ts
```

同时修复第 308-309 行日志中的同样问题：
```python
_log.info("峰值选取百分比: top_percent=%.2f (duration≈%.0fs)", top_percent, max(
    (s.get("timestamp", s.get("end", s.get("start", 0))) for s in speech_scores), default=0))
```

**风险**: 低。纯字段兜底取值，不影响正常路径。

**验证**: 构造 speech_scores 无 timestamp 的测试用例，验证 `top_percent` 在长视频场景下返回 0.05 而非 0.15。

---

### Q2: 修复声音事件被错误归类为击杀事件

**文件**: `lsc/analyzer/pipeline.py:406-412`

**问题**:
```python
for hl in highlights:
    if hl.get("type") == round_marker_type:
        round_markers.append(hl)
    elif "击杀" in hl.get("reason", "") or hl.get("source") in ("ocr", "sound"):
        kill_events.append(hl)       # ← voice_burst、round_end 也被归入
    else:
        other_segments.append(hl)
```

`source == "sound"` 将 `voice_burst`（主播反应）、`round_end`（回合结束）都当作击杀事件参与回合分组。`voice_burst` 发生在主播情绪激动时，可能不在击杀回合内，强制并入会拉伸片段范围。

此外，`reason` 中 "击杀音效" 包含 "击杀" 二字也会命中第一个条件，但 "主播反应" 和 "回合结束" 不含 "击杀"，被 `source == "sound"` 兜底捕获。

**修复**:
```python
for hl in highlights:
    if hl.get("type") == round_marker_type:
        round_markers.append(hl)
    elif hl.get("source") == "ocr" or (
        hl.get("source") == "sound" and hl.get("type") == "gunfire"
    ):
        kill_events.append(hl)
    else:
        other_segments.append(hl)
```

仅 OCR 击杀事件和声音检测中的 `gunfire`（枪声）类型参与回合分组。`voice_burst` 和 `round_end` 归入 `other_segments`，保持独立片段不参与回合合并。

**风险**: 低。回合分组精度提升，非击杀事件不再被错误合并。

**验证**: 构造含 `source=sound, type=voice_burst` 的片段列表，验证它不被归入 `kill_events`，而是保留在 `other_segments` 中。

---

### Q3: 修复 OCR 事件双重计入

**文件**: `lsc/analyzer/pipeline.py:310-337`

**问题**:
```python
# 第一次：OCR 事件参与融合加权
results = fusion.fuse_and_extract(
    ...,
    ocr_events=ocr_events,   # ← OCR 事件以 0.5s 窗口铺展到时间线参与加权求和
)

# 第二次：OCR 事件作为独立片段追加
for event in ocr_events:
    results.append({          # ← 同一事件又作为独立片段加入
        "start": max(0.0, event["timestamp"] - pre_pad),
        "end": event["timestamp"] + post_pad,
        ...
    })
```

融合产出的片段基于 0.5s 网格时间戳，OCR 独立片段基于事件时间戳 ± padding，两者 start/end 差异可能使 IoU < 0.6，去重失效，最终产出重复片段。

**修复方案**:

OCR 事件不再同时参与融合加权和独立追加。改为二选一策略：

- **策略 A（推荐）**：OCR 事件仅作为独立片段追加（带回合分组），不参与融合加权。融合阶段只处理 speech + visual + scene 三个维度。理由：OCR 击杀是离散事件信号，不适合铺展到连续时间线加权；它的价值在于精确的事件时间戳和回合分组。
- **策略 B**：OCR 事件仅参与融合加权，不独立追加。融合阶段已通过 ocr 权重反映了击杀密度。

选择策略 A，因为回合分组依赖 OCR 事件的 `timestamp` 和 `type` 字段，独立追加后参与回合分组更有价值。

```python
# fusion 阶段不传 ocr_events
results = fusion.fuse_and_extract(
    speech_scores=speech_scores,
    visual_scores=visual_scores,
    scene_scores=scene_scores,
    speech_segments=speech_scores,
    absolute_threshold=absolute_threshold,
    top_percent=top_percent,
    # ocr_events 不传入，OCR 信号通过独立片段 + 回合分组体现
)
```

同时，声音事件也仅作为独立片段追加，不参与融合加权（声音事件的 `source=sound` 片段同理）。

**风险**: 中。融合权重中 `ocr: 0.30` 变为无输入，需要重新归一化其他权重。修改 `PRESET_WEIGHTS["valorant"]`：
```python
"valorant": {"visual": 0.45, "scene": 0.25, "audio": 0.30}  # ocr 移除后重新归一化
```
或者在 `FusionScorer.fuse` 中自动检测 ocr_events 为空时将 ocr 权重归零并归一化（见 A4）。

**验证**: 
1. 构造含 OCR 事件的测试用例，验证结果中不出现时间重叠 > 60% 的重复片段。
2. 验证 OCR 事件仍参与回合分组（`_group_events_by_round` 输出含回合合并片段）。

---

### Q4: 修复 OCR/Sound 进度未映射

**文件**: `lsc/analyzer/pipeline.py:264-289`

**问题**:
```python
self._report_progress("ocr", 0.0, "OCR 击杀检测中...")       # 进度跳回 0%
ocr_events = detect_kill_events(..., progress_callback=self._report_progress, ...)
# detect_kill_events 内部调用 progress_callback("ocr", pct, ...) 报告 0-90%
self._report_progress("sound", 0.0, "音频事件检测中...")      # 又跳回 0%
sound_events = detect_sound_events(..., progress_callback=self._report_progress, ...)
```

用户看到的进度序列: `90% → 0% → 90% → 0% → 90% → 100%`。

**修复**:

新增进度映射常量和 wrapper，与现有 `_AUDIO_STAGE_MAP` / `_VISUAL_STAGE_MAP` 一致：

```python
_OCR_STAGE_MAP: dict[str, tuple[float, float]] = {
    "ocr": (90.0, 95.0),
}
_SOUND_STAGE_MAP: dict[str, tuple[float, float]] = {
    "sound": (95.0, 98.0),
}
```

```python
def _make_ocr_progress_wrapper(self) -> Callable[[str, float, str], None]:
    def wrapper(stage: str, progress: float, detail: str) -> None:
        pct_range = _OCR_STAGE_MAP.get(stage)
        if pct_range:
            mapped = pct_range[0] + (pct_range[1] - pct_range[0]) * progress / 100.0
        else:
            mapped = progress
        self._report_progress(stage, mapped, detail)
    return wrapper

def _make_sound_progress_wrapper(self) -> Callable[[str, float, str], None]:
    def wrapper(stage: str, progress: float, detail: str) -> None:
        pct_range = _SOUND_STAGE_MAP.get(stage)
        if pct_range:
            mapped = pct_range[0] + (pct_range[1] - pct_range[0]) * progress / 100.0
        else:
            mapped = progress
        self._report_progress(stage, mapped, detail)
    return wrapper
```

调用处改为传 wrapper：
```python
self._report_progress("ocr", 90.0, "OCR 击杀检测中...")
ocr_events = detect_kill_events(
    ..., progress_callback=self._make_ocr_progress_wrapper(),
)
self._report_progress("sound", 95.0, "音频事件检测中...")
sound_events = detect_sound_events(
    ..., progress_callback=self._make_sound_progress_wrapper(),
)
```

**风险**: 低。纯进度映射，不影响分析逻辑。

**验证**: 运行 AI 分析，观察进度条从 90% 平滑过渡到 95% 再到 98%，不出现回跳。

---

### Q5: 修复去重与合并的执行顺序

**文件**: `lsc/analyzer/pipeline.py:360-362`

**问题**:
```python
results = _merge_close_segments(results, max_gap=15.0)   # 先合并
results = _deduplicate_highlights(results, iou_threshold=0.6)  # 再去重
```

合并可能将两个不同类型的片段（如融合片段 + OCR 片段）合为一片，然后去重无法再分离。

**修复**:
```python
results = _deduplicate_highlights(results, iou_threshold=0.6)  # 先去重
results = _merge_close_segments(results, max_gap=15.0)          # 再合并
```

**风险**: 低。先去掉完全重复的，再合并剩余的相近片段，逻辑更合理。

**验证**: 构造两个 IoU=0.7 且与第三个片段 gap=10s 的片段列表，验证去重先生效，合并不跨类型。

---

### Q6: 改进音频能量回退阈值

**文件**: `python-backend/handlers/room_handler.py:175-251`（`_detect_audio_energy_peaks`）

**问题**:
```python
threshold = float(np.percentile(rms, 75))   # 75 百分位
if threshold == 0:
    return []
is_peak = rms > threshold
```

取 75 百分位意味着 25% 的时间都会被标记为"峰值"。Valorant 中 buy phase 选枪界面、回合间等待都有较大音量，这些非精彩段也会被选中。

**修复**:

改用动态阈值，取统计阈值与百分位阈值的较大值，并增加最小持续时间过滤：

```python
# 动态阈值：取百分位和 mean+2*std 的较大值，过滤持续低能量段
percentile_threshold = float(np.percentile(rms, 85))  # 提高到 85 百分位
mean = float(np.mean(rms))
std = float(np.std(rms))
statistical_threshold = mean + 2.0 * std
threshold = max(percentile_threshold, statistical_threshold)
if threshold == 0:
    return []
is_peak = rms > threshold
```

同时将最小持续时间从 2.0s 提高到 3.0s（过滤短暂噪声）：
```python
if end_sec - start_sec >= 3.0:  # 原 2.0
```

**风险**: 低。阈值更严格可能导致部分高光漏检，但可通过降低 `absolute_threshold` 补偿。

**验证**: 对比修改前后的高光段数量和质量，确认非精彩段（buy phase）被过滤。

---

### Q7: 修复 fusion 结束边界精化不对称

**文件**: `lsc/analyzer/fusion.py:441-448`

**问题**:
```python
for ts in reversed(all_ts_sorted):
    if ts > orig_end + search_range:
        continue
    if ts < orig_end:
        break                    # ← 只在 [orig_end, orig_end+range] 搜索
    if ts_to_score[ts] >= threshold:
        refined_end = ts
        break
```

如果 `[orig_end, orig_end+range]` 内所有 score < threshold，`refined_end` 保持 `orig_end` 不变，无法向后扩展。起始边界可向前扩展（搜索 `[orig_start-range, orig_start]`），逻辑不对称。

**修复**:

结束边界精化改为：从 `orig_end + search_range` 向 `orig_end - search_range` 反向扫描，找最后一个 score >= threshold 的点：

```python
# 结束边界：在 [orig_end - search_range, orig_end + search_range] 范围内
# 从 orig_end + search_range 向 orig_end 方向扫描，找第一个 score >= threshold 的点作为结束边界
refined_end = orig_end
for ts in reversed(all_ts_sorted):
    if ts > orig_end + search_range:
        continue
    if ts < orig_end - search_range:
        break
    if ts_to_score.get(ts, 0.0) >= threshold:
        refined_end = ts
        break
```

同时使用 `bisect` 优化线性扫描（见 P9 边界精化性能优化，可作为同一 PR 一并完成）。

**风险**: 低。边界微调，不影响峰值检测的整体结果。

**验证**: 构造 score 曲线在结束边界附近先降后升的测试用例，验证结束边界能向后扩展到高分区。

---

## 4. 阶段二：性能加速（P1-P8）

### P1: OCR 帧间差分预筛 ★最大收益

**文件**: `lsc/analyzer/ocr_detector.py:140-201`

**问题**: 0.25s 采样间隔下 2h 视频 = 28800 帧，逐帧调用 RapidOCR（30-80ms/帧），总耗时 15-40 分钟。Kill Feed 只在击杀时短暂出现（2-3s），大部分帧无变化。

**修复**:

在 OCR 前增加帧间差分预筛步骤：

```python
import cv2

# 预筛：计算相邻帧像素差，仅对变化超阈值的帧执行 OCR
prev_frame = None
frames_to_ocr: list[tuple[str, float]] = []  # (filepath, timestamp)

for i, fname in enumerate(frame_files):
    fpath = os.path.join(tmp_dir, fname)
    frame = cv2.imread(fpath, cv2.IMREAD_GRAYSCALE)
    if frame is None:
        continue
    if prev_frame is not None:
        diff = cv2.absdiff(frame, prev_frame)
        mean_diff = float(np.mean(diff))
        # 像素差低于阈值 → 无变化 → 跳过 OCR
        if mean_diff < _FRAME_DIFF_THRESHOLD:  # 默认 5.0
            prev_frame = frame
            continue
    prev_frame = frame
    ts = precise_timestamps[i] if i < len(precise_timestamps) else i * _SAMPLE_INTERVAL
    frames_to_ocr.append((fpath, ts))

_log.info("OCR 预筛: %d/%d 帧需要 OCR (%.0f%% 过滤)",
          len(frames_to_ocr), len(frame_files),
          (1 - len(frames_to_ocr) / max(len(frame_files), 1)) * 100)

# 仅对变化帧执行 OCR
for fpath, ts in frames_to_ocr:
    if cancel_check and cancel_check():
        break
    result_ocr, _ = ocr(fpath)
    # ... 后续评分逻辑不变，timestamp 用 ts
```

新增常量:
```python
_FRAME_DIFF_THRESHOLD = 5.0  # 像素均值差阈值，低于此值视为无变化
```

需要 `opencv-python` 依赖（检查 requirements-ai.txt 是否已包含，若未包含则添加）。

**预估加速**: 5-10x（取决于视频内容，Valorant 击杀密度低时过滤率可达 80-90%）

**风险**: 中。阈值需要调优：太低过滤效果差，太高可能漏检细微文字变化。建议初版用 5.0，后续根据测试结果调整。

**验证**: 
1. 对比预筛前后的 OCR 事件数量，确认击杀事件未漏检。
2. 记录过滤率和耗时到日志。

---

### P2: OCR 采样间隔 0.25s → 0.5s

**文件**: `lsc/analyzer/ocr_detector.py:20`

**问题**: `_SAMPLE_INTERVAL = 0.25` 产生过多帧。

**修复**:
```python
_SAMPLE_INTERVAL = 0.5  # Kill Feed 显示 2-3s，0.5s 不会遗漏
```

**预估加速**: 2x（帧数减半）

**风险**: 低。Kill Feed 显示持续 2-3s，0.5s 间隔最多延迟 0.5s 检测到，不影响回合分组。

**验证**: 对比 0.25s 和 0.5s 间隔的击杀事件检测率，确认无显著差异。

---

### P4: Sound detector 向量化 FFT

**文件**: `lsc/analyzer/sound_detector.py:87-130`

**问题**: 逐窗口 Python 循环做 `np.fft.rfft`，2h 视频 = 28800 个窗口，每个窗口单独 FFT。Spike 检测时还有冗余 FFT 和 `rfftfreq` 循环内重算。

**修复**:

一次性批量 FFT + 向量化频段能量计算：

```python
# 预计算
freqs_full = np.fft.rfftfreq(window_size, 1.0 / framerate)
band_masks = {name: (freqs_full >= lo) & (freqs_full <= hi) for name, (lo, hi) in _FREQ_BANDS.items()}
band_counts = {name: max(int(np.sum(mask)), 1) for name, mask in band_masks.items()}

# 批量 FFT：一次性计算所有窗口的频谱
all_spectra = np.abs(np.fft.rfft(trimmed, axis=1))  # shape: (n_windows, n_freqs)

# 向量化计算各频段能量
all_energies = {}
for name, mask in band_masks.items():
    all_energies[name] = np.sum(all_spectra[:, mask] ** 2, axis=1) / band_counts[name]

# Spike 检测（向量化）
events: list[dict[str, Any]] = []
for name in _FREQ_BANDS:
    energies = all_energies[name]
    prev_energies = np.zeros_like(energies)
    prev_energies[1:] = energies[:-1]
    
    # 找到所有 spike 位置
    spike_mask = (prev_energies > 0) & (energies > prev_energies * _SPIKE_RATIO)
    spike_indices = np.where(spike_mask)[0]
    
    for i in spike_indices:
        prev = prev_energies[i]
        energy = energies[i]
        score = min(1.0, energy / (prev * 5.0))
        timestamp = i * _WINDOW_SECONDS
        events.append({
            "timestamp": round(timestamp, 3),
            "type": _BAND_LABELS.get(name, name),
            "score": max(0.3, float(score)),
        })
```

亚窗口插值精确定位保留（仅对 spike 窗口执行，数量少不影响性能），但 `rfftfreq` 预计算移到循环外：

```python
# 预计算 half_size 的 freqs（循环外）
half_size = window_size // 2
freqs_half = np.fft.rfftfreq(half_size, 1.0 / framerate)
half_band_masks = {name: (freqs_half >= lo) & (freqs_half <= hi) for name, (lo, hi) in _FREQ_BANDS.items()}
```

**预估加速**: 10x+（numpy 批量 FFT + 向量化能量计算，消除 Python 循环）

**风险**: 中。需要确保向量化结果与逐窗口一致。建议保留原逻辑作为 reference test。

**验证**: 
1. 构造已知音频信号（含特定频率突增），验证向量化版本检出相同事件。
2. 对比修改前后的 events 列表（允许亚窗口插值的微小差异）。

---

### P3: OCR 合并 FFmpeg 解码

**文件**: `lsc/analyzer/ocr_detector.py:122-129` + `255-262`

**问题**: Kill Feed OCR 和 Round Marker OCR 分别启动独立 FFmpeg 进程，完整视频被解码两次。

**修复**:

使用 FFmpeg `split` 滤镜在一次解码中同时提取两个裁剪区域，输出到不同目录：

```python
# 一次性提取 Kill Feed + Round Marker 两区域
killfeed_dir = os.path.join(tmp_dir, "killfeed")
roundmarker_dir = os.path.join(tmp_dir, "roundmarker")
os.makedirs(killfeed_dir, exist_ok=True)
os.makedirs(roundmarker_dir, exist_ok=True)

# 用 split 滤镜分两路，各自 crop 后输出
cmd = [
    ffmpeg_path, "-y", "-loglevel", "error",
    "-i", video_path,
    "-filter_complex",
    f"[0:v]fps=1/{_SAMPLE_INTERVAL},split=2[kf][rm];"
    f"[kf]crop={w}:{h}:{x}:{y},showinfo[kfo];"
    f"[rm]crop={mw}:{mh}:{mx}:{my},showinfo[rmo]",
    "-map", "[kfo]", "-q:v", "3", os.path.join(killfeed_dir, "frame_%05d.jpg"),
    "-map", "[rmo]", "-q:v", "3", os.path.join(roundmarker_dir, "hud_%05d.jpg"),
]
```

注意：FFmpeg 多输出需要解析两组 showinfo 的 pts_time，按顺序分别对应 Kill Feed 和 Round Marker 的时间戳。

同时复用 `RapidOCR()` 实例（当前 `_detect_round_markers` 中新建了第二个实例）。

**预估加速**: ~2x（省去一次完整视频解码）

**风险**: 中。FFmpeg `split` 滤镜 + 多输出的语法较复杂，需要验证时间戳解析正确。Windows 下 FFmpeg 多输出可能有兼容性问题。

**验证**: 
1. 对比合并前后的帧数量和时间戳一致性。
2. 验证 Kill Feed 和 Round Marker 的事件检测数量一致。

---

### P8: OCR pipe 输出替代磁盘 I/O

**文件**: `lsc/analyzer/ocr_detector.py:121-128`

**问题**: FFmpeg 写入 28800 帧 JPG 到磁盘，OCR 再从磁盘读回，产生 ~288MB 临时文件 I/O。

**修复**:

改为 FFmpeg pipe 输出到 stdout，直接读取二进制帧数据：

```python
# 使用 image2pipe 格式输出 JPEG 序列到 stdout
cmd = [
    ffmpeg_path, "-y", "-loglevel", "error",
    "-i", video_path,
    "-vf", f"fps=1/{_SAMPLE_INTERVAL},crop={w}:{h}:{x}:{y},showinfo",
    "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "3",
    "pipe:1",
]
proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, ...)
# 从 stdout 读取 JPEG 帧（JPEG 以 FFD8 开头 FFD9 结尾，可按标记分割）
```

需要实现 JPEG 帧分割逻辑（按 `FFD8` SOI 标记分割），或使用 `imageio`/`cv2.imdecode` 从内存缓冲区解码。

与 P1（帧间差分预筛）配合时，差分预筛在内存中完成，仅对变化帧传给 OCR，进一步减少 OCR 调用次数。

**预估加速**: ~1.5x（省去磁盘写入/读回）

**风险**: 中。JPEG 帧分割需要正确处理 FFD8/FFD9 标记，边界情况需测试。大视频的 pipe 缓冲区可能溢出，需要流式读取。

**验证**: 对比 pipe 和磁盘两种方式的帧数量和 OCR 结果一致性。

---

## 5. 阶段三：Scene 模式引入轻量 OCR（A1-A2）

### A1: Scene 模式增加 OCR Kill Feed 检测信号

**文件**: `python-backend/handlers/room_handler.py:254-427`（`_run_scene_analysis`）

**问题**: scene 模式仅靠 FFmpeg 像素差异 + 音频 RMS 能量，检测信号太弱，切片质量不稳定。Valorant 的核心高光信号是击杀事件，而击杀通过 Kill Feed UI 文字变化体现，像素差异检测不到。

**修复**:

在 `_run_scene_analysis` 中增加轻量 OCR 检测步骤（仅 OCR，不跑 Whisper/CLIP），作为 scene 分数的补充信号：

```python
def _run_scene_analysis(
    video_path: str,
    threshold: float = 0.3,
    min_duration: float = 3.0,
    progress_callback=None,
    cancel_check=None,
    time_range=None,
) -> list[dict[str, Any]] | None:
    # ... 现有的 FFmpeg 场景检测逻辑 ...
    
    # 新增：OCR 击杀检测（轻量，仅 RapidOCR）
    ocr_highlights: list[dict[str, Any]] = []
    try:
        from lsc.analyzer.ocr_detector import detect_kill_events
        from lsc.config import load_config as _load_cfg
        _cfg = _load_cfg()
        _ffmpeg = _cfg.ffmpeg_path or "ffmpeg"
        if progress_callback:
            progress_callback("scene", 85.0, "OCR 击杀检测中...")
        ocr_events = detect_kill_events(
            video_path, ffmpeg_path=_ffmpeg,
            progress_callback=None,  # scene 模式不映射 OCR 进度
            cancel_check=cancel_check,
            game="valorant",
        )
        # OCR 事件 → 高光片段（带 padding）
        for evt in ocr_events:
            if evt.get("type") == "kill":
                ts = evt.get("timestamp", 0.0)
                score = evt.get("score", 0.5)
                pre_pad = 1.0 if score >= 0.7 else 2.0
                post_pad = 4.0 if score >= 0.7 else 6.0
                ocr_highlights.append({
                    "start": max(0.0, ts - pre_pad),
                    "end": ts + post_pad,
                    "score": score,
                    "reason": f"击杀: {evt.get('text', '')[:30]}",
                    "source": "ocr",
                    "type": "kill",
                    "timestamp": ts,
                })
    except ImportError:
        _log.debug("rapidocr 未安装，scene 模式跳过 OCR 检测")
    except Exception as exc:
        _log.warning("scene 模式 OCR 检测失败: %s", exc)
    
    # 合并 scene 检测结果 + OCR 结果
    all_highlights = highlights + ocr_highlights
    
    # 按回合分组 OCR 事件
    if ocr_highlights:
        from lsc.analyzer.pipeline import _group_events_by_round
        all_highlights = _group_events_by_round(all_highlights)
    
    # 去重
    from lsc.analyzer.pipeline import _merge_close_segments, _deduplicate_highlights
    all_highlights = _deduplicate_highlights(all_highlights, iou_threshold=0.5)
    all_highlights = _merge_close_segments(all_highlights, max_gap=15.0)
    
    return all_highlights
```

**设计要点**:
- OCR 检测是可选的：`rapidocr` 未安装时优雅降级为纯 scene + 音频检测
- OCR 事件参与回合分组，提升回合边界精度
- scene 分数和 OCR 分数通过去重合并（IoU 0.5），保留分数较高的

**预估耗时增加**: 3-8 分钟（2h 视频，P1+P2 优化后）。考虑到质量的显著提升，这个代价是可接受的。

**风险**: 中。OCR 依赖 `rapidocr-onnxruntime`，scene 模式原本无需 AI 依赖。需要在 `handle_start_analysis` 中检测依赖并提示用户安装，或降级为纯 scene 检测。

**验证**: 
1. 对比纯 scene 和 scene+OCR 的切片质量（人工评估）。
2. 验证 OCR 依赖未安装时优雅降级。

---

### A2: Scene 回合分组用 OCR 回合标记 + 动态间隔

**文件**: `python-backend/handlers/room_handler.py:384-411`

**问题**: 固定 35s 间隔切分回合，但 Valorant 不同回合类型时长差异大（手枪局 30-40s、长枪局 60-80s、加时赛 80-100s）。

**修复**:

A1 引入 OCR 后，OCR 的 `_detect_round_markers` 会产出回合边界标记。利用这些标记替代固定 35s 间隔：

```python
# 如果有 OCR 回合标记，用标记边界分组
round_marker_timestamps = [e["timestamp"] for e in ocr_events if e.get("type") == "round_marker"]

if round_marker_timestamps:
    # 用 OCR 回合标记作为分组边界
    _log.info("使用 OCR 回合标记分组: %d 个边界", len(round_marker_timestamps))
    # 按标记边界分组 scene 切换点
    # ... 分组逻辑 ...
else:
    # Fallback: 动态间隔（不再用固定 35s）
    # 根据场景切换点间距分布自适应确定回合边界
    gaps = [timestamps[i+1] - timestamps[i] for i in range(len(timestamps)-1)]
    if gaps:
        median_gap = sorted(gaps)[len(gaps) // 2]
        # 回合间隔通常 > 中位数的 2 倍
        dynamic_threshold = max(35.0, median_gap * 2.0)
        _ROUND_MIN_GAP = min(dynamic_threshold, 80.0)  # 上限 80s
    else:
        _ROUND_MIN_GAP = 35.0
```

**风险**: 低。OCR 回合标记可用时使用标记，不可用时用动态间隔，比固定 35s 更鲁棒。

**验证**: 对比固定 35s 和动态间隔的回合分组结果，确认手枪局和长枪局都能正确分组。

---

## 6. 阶段四：CLIP + Whisper 性能优化（P5-P7）

### P5: CLIP 关键帧间隔 0.5s → 1.0s

**文件**: `lsc/analyzer/visual_analyzer.py:423`

**问题**: `interval=0.5` 产生 14400 帧（2h 视频），CPU 上 ViT-B-32 每帧 100-200ms，总耗时 24-48 分钟。

**修复**:
```python
frame_paths, timestamps = self.extract_keyframes(
    video_path, tmp_dir, interval=1.0, scene_timestamps=scene_timestamps  # 0.5 → 1.0
)
```

FusionScorer 的 `_nearest_neighbor` 插值会将 1.0s 间隔的 visual_scores 映射到 0.5s 网格，不会产生空缺。

**预估加速**: 2x（帧数减半）

**风险**: 低。CLIP 语义嵌入在 1s 内变化不大，不影响冲击力评分。场景切换点附近可能丢失 0.5s 精度，但 `refine_peak_boundaries` 可补偿。

**验证**: 对比 0.5s 和 1.0s 间隔的 visual_scores 分布，确认评分趋势一致。

---

### P6: Whisper 模型自动降级

**文件**: `lsc/analyzer/model_manager.py` + `lsc/analyzer/audio_analyzer.py`

**问题**: `whisper_model="auto"` 时由 `detect_device()` 决定模型大小，但 CPU 模式下即使选 base 模型，2h 视频仍需 4-6 小时转录。

**修复**:

在 `detect_device()` 中增加更激进的 CPU 降级策略：

```python
def detect_device() -> dict[str, Any]:
    # ... 现有 CUDA 检测逻辑 ...
    
    if has_cuda:
        return {"device": "cuda", "compute_type": "float16", "whisper_model": "small"}
    else:
        # CPU 模式：默认用 tiny 模型（0.5x 实时），而非 base（0.3x 实时）
        # tiny 模型中文准确率略低，但速度提升 5-10x
        # 用户可显式指定 whisper_model="base" 覆盖
        return {"device": "cpu", "compute_type": "int8", "whisper_model": "tiny"}
```

同时在前端 UI 中增加模型选择提示，让用户了解不同模型的速度/质量权衡：

| 模型 | CPU 速度 | GPU 速度 | 中文准确率 | 显存 |
|------|----------|----------|------------|------|
| tiny | 0.5-1x 实时 | 5-10x 实时 | 中等 | 1GB |
| base | 0.3-0.5x 实时 | 3-5x 实时 | 良好 | 1GB |
| small | 0.1-0.2x 实时 | 1-3x 实时 | 优秀 | 2GB |

**预估加速**: 3-5x（CPU 模式下 tiny vs base）

**风险**: 中。tiny 模型中文准确率低于 base，可能影响 TextScorer 的情绪关键词匹配。建议在 TextScorer 中增加模糊匹配容忍度（如允许 1 字编辑距离）。

**验证**: 对比 tiny 和 base 模型在同一段视频上的转录结果和高光检测质量。

---

### P7: Whisper + CLIP 并行执行

**文件**: `lsc/analyzer/pipeline.py:214-258`

**问题**: 语音分析和视觉分析当前串行执行（先 Whisper 后 CLIP），两者无数据依赖。

**修复**:

使用 `concurrent.futures.ThreadPoolExecutor` 并行执行：

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

# 语音和视觉并行执行
with ThreadPoolExecutor(max_workers=2) as parallel_executor:
    audio_future = parallel_executor.submit(self._run_audio_analysis, video_path, device, compute_type, model_size)
    visual_future = parallel_executor.submit(self._run_visual_analysis, video_path, device, scene_highlights)
    
    # 等待两者完成
    speech_scores = audio_future.result()
    visual_scores = visual_future.result()
```

封装为内部方法：
```python
def _run_audio_analysis(self, video_path, device, compute_type, model_size):
    audio_analyzer = AudioAnalyzer(device=device, compute_type=compute_type, model_size=model_size)
    try:
        return audio_analyzer.analyze(
            video_path,
            progress_callback=self._make_audio_progress_wrapper(),
            cancel_check=self._cancel_check,
        )
    except Exception as exc:
        _log.error("语音分析失败: %s", exc, exc_info=True)
        return []
    finally:
        audio_analyzer.cleanup()

def _run_visual_analysis(self, video_path, device, scene_highlights):
    visual_analyzer = VisualAnalyzer(device=device)
    scene_timestamps = [h["start"] for h in scene_highlights] if scene_highlights else None
    try:
        return visual_analyzer.analyze(
            video_path,
            progress_callback=self._make_visual_progress_wrapper(),
            cancel_check=self._cancel_check,
            scene_timestamps=scene_timestamps,
        )
    except Exception as exc:
        _log.error("视觉分析失败: %s", exc, exc_info=True)
        return []
    finally:
        visual_analyzer.cleanup()
```

**注意**: 
- GPU 模式下 Whisper 和 CLIP 共享 CUDA 显存，需监控显存不足（medium 需 2GB + CLIP 需 512MB）。如显存不足，回退为串行。
- CPU 模式下两者竞争 CPU 核心，加速效果有限（约 1.2-1.5x），但仍有收益（I/O 等待重叠）。
- 进度回调需要合并两个子分析的进度（audio: 5-50%, visual: 50-90% → 并行后两者同时推进，进度条取两者加权平均）。

**预估加速**: ~1.8x（GPU），~1.3x（CPU）

**风险**: 中。GPU 显存竞争可能导致 OOM。建议增加显存预检查（pynvml），不足时回退串行。

**验证**: 
1. 验证并行和串行模式的最终结果一致。
2. 监控 GPU 显存峰值，确认不超限。

---

## 7. 阶段五：架构改进（A3-A5）

### A3: Fast 模式增量 OCR

**文件**: `lsc/analyzer/pipeline.py:607-699`（`_fast_round_detection`）+ `python-backend/handlers/room_handler.py:2848-2870`

**问题**: fast 模式持续分析时每次全量扫描整个录制文件，2h 视频 OCR 仍需 3-8 分钟（P1+P2 优化后）。

**修复**:

为 fast 模式增加增量分析支持：

```python
def _fast_round_detection(
    video_path: str,
    ffmpeg_path: str = "ffmpeg",
    duration: float = 0.0,
    pre_pad: float = 5.0,
    post_pad: float = 5.0,
    max_kill_gap: float = 45.0,
    time_range: tuple[float, float] | None = None,  # 新增：增量分析范围
    known_events: list[dict[str, Any]] | None = None,  # 新增：已知事件（用于合并）
) -> list[dict[str, Any]]:
    """轻量回合检测，支持增量分析。"""
    
    kill_events: list[dict[str, Any]] = []
    sound_events: list[dict[str, Any]] = []
    
    # 增量模式：仅分析 [time_range[0], time_range[1]] 区间
    kill_events = detect_kill_events(
        video_path, ffmpeg_path=ffmpeg_path, duration=duration,
        game="valorant",
        time_range=time_range,  # 新增参数
    )
    sound_events = detect_sound_events(
        video_path, ffmpeg_path=ffmpeg_path, duration=duration,
        time_range=time_range,  # 新增参数
    )
    
    # 合并已知事件
    if known_events:
        all_events = known_events + kill_events + sound_events
    else:
        all_events = kill_events + sound_events
    
    # ... 后续 padding + 回合分组逻辑不变 ...
```

在 `_continuous_analysis_loop` 的 fast 分支中改为增量调用：

```python
elif mode == 'fast':
    def _fast_detect(_vp=video_path, _last=last_analyzed, _cur=current_dur, _known=all_highlights):
        from lsc.analyzer.pipeline import _fast_round_detection
        return _fast_round_detection(
            _vp, ffmpeg_path=_ffmpeg, duration=_dur,
            time_range=(_last, _cur),  # 增量范围
            known_events=_known,       # 传入已知事件
        )
```

`detect_kill_events` 和 `detect_sound_events` 需要支持 `time_range` 参数（类似 `_run_scene_analysis` 的实现，用 `-ss -t` input seek）。

**预估加速**: 持续分析从全量 3-8 分钟降至增量 1-2 分钟（仅分析新增的 60-120s 内容）。

**风险**: 中。增量事件的回合分组需要与历史事件合并，边界处理（跨增量边界的回合）需仔细测试。

**验证**: 对比全量和增量的最终结果（累积后应一致）。

---

### A4: 融合权重自适应

**文件**: `lsc/analyzer/fusion.py:29-44` + `lsc/analyzer/pipeline.py:305`

**问题**: Valorant 融合权重固定（visual 0.35 + ocr 0.30 + scene 0.20 + audio 0.15），当某模块无产出时（如 OCR 检测到 0 个事件），ocr 权重 0.30 浪费，其他维度被稀释。

**修复**:

在 `FusionScorer.fuse` 中增加权重自动归一化：

```python
def fuse(
    self,
    speech_scores, visual_scores, scene_scores, ocr_events=None,
) -> list[dict[str, float]]:
    # 检测各维度是否有有效输入
    has_speech = len(speech_scores) > 0
    has_visual = len(visual_scores) > 0
    has_scene = scene_scores is not None and len(scene_scores) > 0
    has_ocr = ocr_events is not None and len(ocr_events) > 0
    
    # 过滤无输入的维度，重新归一化权重
    effective_weights = dict(self.weights)
    total = 0.0
    for key, has_input in [
        ("audio", has_speech), ("visual", has_visual),
        ("scene", has_scene), ("ocr", has_ocr),
    ]:
        if not has_input:
            effective_weights[key] = 0.0
        total += effective_weights[key]
    
    if total > 0:
        for key in effective_weights:
            effective_weights[key] /= total
    
    _log.info("融合权重自适应: %s (原始: %s)", effective_weights, self.weights)
    # ... 后续加权求和使用 effective_weights ...
```

**风险**: 低。权重归一化是纯数学操作，不影响输入输出 schema。

**验证**: 构造 OCR 为空的测试用例，验证 ocr 权重归零后其他维度权重自动归一化到 1.0。

---

### A5: Scene 分数引入内容质量

**文件**: `python-backend/handlers/room_handler.py:396-411`

**问题**: scene score = `max(0.3, min(1.0, 1.5 - gap/60))`，纯基于场景切换间隔，与实际精彩程度无关。

**修复**:

引入音频能量变化率作为辅助 score：

```python
# 在 _run_scene_analysis 中，提取音频 RMS 包络
audio_rms = _extract_audio_rms_envelope(video_path, ffmpeg_path=_ffmpeg)

# 计算每个高光段的音频能量变化率
for h in highlights:
    seg_start = h["start"]
    seg_end = h["end"]
    # 提取该段的 RMS
    seg_rms = audio_rms[int(seg_start * 2):int(seg_end * 2)]  # 0.5s 窗口
    if len(seg_rms) > 0:
        # 能量变化率 = max / mean，越大表示有突发高能
        energy_ratio = float(np.max(seg_rms) / max(np.mean(seg_rms), 1e-6))
        audio_score = min(1.0, energy_ratio / 5.0)  # 归一化到 0-1
    else:
        audio_score = 0.3
    
    # 综合分数 = 场景间隔分 * 0.6 + 音频能量分 * 0.4
    gap_score = max(0.3, min(1.0, 1.5 - gap / 60.0))
    h["score"] = gap_score * 0.6 + audio_score * 0.4
```

**风险**: 低。增加一次音频 RMS 提取（~1-2 分钟），但可与音频能量回退共用。

**验证**: 对比纯 gap score 和 gap+audio score 的高光排序，确认音频能量高的段排名上升。

---

## 8. 涉及文件汇总

| 文件 | 修改项 |
|------|--------|
| `lsc/analyzer/pipeline.py` | Q1, Q2, Q3, Q4, Q5, A3 |
| `lsc/analyzer/fusion.py` | Q7, A4 |
| `lsc/analyzer/ocr_detector.py` | P1, P2, P3, P8, A3 |
| `lsc/analyzer/sound_detector.py` | P4, A3 |
| `lsc/analyzer/visual_analyzer.py` | P5 |
| `lsc/analyzer/model_manager.py` | P6 |
| `lsc/analyzer/audio_analyzer.py` | P6, P7 |
| `python-backend/handlers/room_handler.py` | Q6, A1, A2, A5 |
| `lsc-electron/src/pages/Workbench/index.tsx` | P6（模型选择提示） |

---

## 9. 预期效果

### 9.1 质量提升

| 指标 | 优化前 | 优化后 |
|------|--------|--------|
| Scene 模式切片准确率 | ~40%（不稳定） | ~70%（引入 OCR 信号 + 动态分组） |
| AI 模式重复片段率 | ~15%（OCR 双重计入） | <5%（去重修复） |
| 长视频 top_percent | 永远 15%（Bug） | 动态 5-15%（Bug 修复） |
| 回合分组准确率 | ~60%（声音事件误归） | ~85%（仅 gunfire + OCR 参与） |

### 9.2 性能提升

| 模式 | 优化前（2h 视频, CPU） | 优化后（2h 视频, CPU） | 加速比 |
|------|----------------------|----------------------|--------|
| fast | 15-40 分钟 | 2-5 分钟（P1+P2+P4） | 5-10x |
| ai | 90-160 分钟 | 40-60 分钟（P5+P6+P7） | 2-3x |
| combined | 100-180 分钟 | 50-70 分钟 | 2-3x |
| scene | 1-3 分钟 | 5-10 分钟（含 OCR） | 质量换速度 |

### 9.3 GPU 模式预估

| 模式 | 优化前（2h 视频, GPU） | 优化后（2h 视频, GPU） |
|------|----------------------|----------------------|
| fast | 10-20 分钟 | 1-3 分钟 |
| ai | 30-50 分钟 | 15-25 分钟 |
| combined | 40-60 分钟 | 20-30 分钟 |

---

## 10. 测试策略

### 10.1 单元测试

每个 Bug 修复（Q1-Q7）配套单元测试：
- `test_estimate_top_percent_fix`: 验证 speech_scores 无 timestamp 时时长估算正确
- `test_group_events_sound_filter`: 验证 voice_burst 不被归入 kill_events
- `test_ocr_no_duplicate`: 验证 OCR 事件不双重计入
- `test_progress_mapping`: 验证 OCR/Sound 进度映射到 90-98% 区间
- `test_dedup_before_merge`: 验证先去重再合并
- `test_audio_threshold_dynamic`: 验证动态阈值过滤 buy phase
- `test_boundary_refine_end`: 验证结束边界可向后扩展

### 10.2 性能测试

- 准备 3 个标准测试视频：5 分钟、30 分钟、2 小时
- 每个优化项前后对比耗时
- 记录到 `docs/perf-benchmark.md`

### 10.3 质量回归

- 准备 5 个已标注的 Valorant 录播（含已知高光时间点）
- 每个优化项前后对比高光检测的 Precision / Recall
- 人工评估切片质量（1-5 分制）

### 10.4 集成测试

- 验证持续分析（边录边分析）在增量模式下的正确性
- 验证多房间同步分析导出的高光映射不变
- 验证分析结果持久化（`{basename}.analysis.json`）正常

---

## 11. 实施风险与缓解

| 风险 | 缓解措施 |
|------|----------|
| P1 帧间差分阈值不准 | 初版用保守阈值 5.0，记录过滤率日志，后续根据测试调优 |
| P3 FFmpeg split 滤镜兼容性 | Windows 下先测试可行性，不可用时回退为两次独立解码 |
| P6 tiny 模型中文准确率低 | TextScorer 增加模糊匹配；用户可显式选 base 模型 |
| P7 GPU 显存不足 | pynvml 预检查，不足时回退串行 |
| A1 scene 模式引入 OCR 依赖 | 优雅降级：rapidocr 未安装时跳过 OCR，纯 scene+音频检测 |
| A3 增量 OCR 回合边界 | 跨增量边界的回合需特殊处理（保留边界事件用于下次合并） |

---

## 12. 不在本次范围

以下项目明确不在本次优化范围：

- **LLM 语义分析**（识别"推理高潮""段子铺垫+爆梗"等深层高光）— 未来升级路径
- **CLIP 领域微调**（游戏专用视觉模型）— 需要标注数据集
- **弹幕密度分析** — 需扩展平台适配器抓取弹幕
- **说话人分离**（diarization）— v2.1 规划
- **多游戏泛化**（CS2/Apex 等）— v3 规划
- **前端 Zustand 状态重构** — P2-14 已在前一轮规划中
- **MaterialClassifier 自动判型** — 试点设计 Phase 5
- **HighlightRanker 六维特征排序** — 试点设计 Phase 2
