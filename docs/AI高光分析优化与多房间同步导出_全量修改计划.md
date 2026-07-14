# AI 高光分析优化与多房间同步导出 — 全量修改计划

> 版本: v2（基于已落地的 AI 高光分析接入方案，做优化 + 新功能扩展）
> 日期: 2026-07-07
> 关联: `.qoder/specs/AI高光分析接入方案_6af43e73.md`（原方案，已落地）

---

## 一、背景与现状校准

原"AI 高光分析接入方案"**已落地**，本次为针对已落地代码的优化 + 新功能扩展。

### 已落地事实（探索确认）
- `lsc/analyzer/` 子包 7 文件全实现（pipeline/audio_analyzer/visual_analyzer/text_scorer/fusion/model_manager/__init__）
- `handle_start_analysis`(room_handler.py:2077) 支持 scene/ai/combined 三模式
- `_ai_executor`(max_workers=2, room_handler.py:98) 已存在
- WebSocket 已有 `analysis_progress`/`cancel_analysis`/`get_analysis_results` 三个消息
- Clip 模型(models.py:91) 已有 score_breakdown/highlight_reason/transcript 字段
- 前端 Workbench 已有分析 UI
- `requirements-ai.txt` 已存在

### 关键现状校准（影响方案设计）
1. NVENC 探测实为 **2 套独立实现**（mse_streamer 模块级缓存 + recording_controller 类级缓存）
2. combined 模式 scene+AI **串行**（room_handler.py:2138-2139）
3. pipeline audio/visual 失败时**静默置空列表**，长视频部分失败被吞为"无高光"
4. scene 模式 `_run_scene_analysis` 用 `subprocess.run` **既无进度也无法取消**
5. 前端 scene 模式被显式排除在 AnalysisProgress 外（Workbench line 1157）
6. `RoomSession.analysis_highlights/analysis_in_progress` 是**死代码**从未写入
7. appStore 完全没有 analysis 字段，6 个 useState 散在 Workbench
8. fusion.py `align_timeline` 是 **O(n·m) 嵌套循环**（line 111-137）
9. 高光结果仅存内存 `_analysis_jobs`（5分钟TTL），重启丢失
10. **多房间高光按房间独立**，audio_align 对齐的是时间偏移不共享高光，现状不支持"同步双房间导出"

---

## 二、优化方案 P0：阻塞 / 数据丢失 / 长视频主场景

### P0-1 长视频音频分段提取
**文件**: `lsc/analyzer/audio_analyzer.py`（重构 extract_audio line 98 / transcribe / analyze）

- 新增 `extract_audio_segments(video_path, tmp_dir, segment_minutes=10, overlap_seconds=30)`：ffprobe 取时长 → 按 10 分钟切段每段重叠 30s → 每段独立 FFmpeg `-ss -to` 提取 `audio_seg_{idx:04d}.wav`
- `transcribe` 改段级：每段独立 `model.transcribe`，时间戳加 `start_offset` 还原全局；重叠区文本相似度 >0.8 去重（首尾 50 字匹配）
- `analyze` 循环每段提取→转录→评分，单段转录完立即 `os.remove`
- 进度回调按"已处理段数/总段数"映射
- 常量 `_SEGMENT_MINUTES=10`、`_OVERLAP_SECONDS=30` 可配置
- **风险:中**。重叠区去重算法需测试；段长 10 分钟是 Whisper beam_size=5 舒适区

### P0-2 fusion 时间线对齐向量化
**文件**: `lsc/analyzer/fusion.py`（重写 align_timeline line 65-140）

- visual 最近邻：`np.searchsorted` 替代每秒 `np.argmin`（O(n·m)→O(n·log m)），visual_ts 防御性 argsort 保证有序
- speech/scene 覆盖查找：向量化广播 `(ts_grid[:,None] >= starts) & (ts_grid[:,None] < ends)`，`np.where(cover, vals, 0).max(axis=1)`
- 输入输出 schema 不变
- **风险:低**。纯算法替换，现有测试验证结果一致

### P0-3 结果持久化到房间级 JSON
**文件**: `python-backend/persistence.py`（追加 load/save）、`room_handler.py`（line 2157-2176、2197-2221）

- 存储位置：录制文件同目录 `{record_basename}.analysis.json`（生命周期绑定，删录制自然清理）
- Schema: `{schema_version, room_id, video_path, video_mtime(校验), mode, analyzed_at, analysis_time_sec, whisper_model, weights, highlights[]}`
- 完成分支写内存 `_analysis_jobs` 同时落盘；`get_analysis_results` 内存未命中回退读 JSON，返回附 `persisted:true`
- scene 模式结果也写入（字段补齐见 P0-5）
- video_mtime 变化时结果失效；可选 `force` 参数跳过缓存重跑
- **风险:低**。复用已有 `_atomic_write_json`

### P0-4 scene 模式进度回调 + 可取消
**文件**: `room_handler.py`（重写 `_run_scene_analysis` line 106-187）、前端 `Workbench/index.tsx` line 1157

- `subprocess.run` → `subprocess.Popen` + stderr PIPE 流式读
- 正则解析 `pts_time:xxx`，每解析到切换点回调 `progress = pts_time/duration*100`（封顶 95%）
- `cancel_check()` 为 True 时 `proc.terminate()` + `wait(5)`，返回 None 表示取消
- 前端移除 `analysisMode !== 'scene'` 条件，scene 也显示进度条
- **Windows 注意**: `bufsize=1` 行缓冲可能失效，改 `bufsize=0` + 手动按 `\n` split
- **风险:中**

### P0-5 scene 与 AI 返回格式统一
**文件**: `room_handler.py`（line 2106-2108、2171-2178）

- `_run_scene_analysis` 返回的 highlight 补齐 `reason:"场景切换频繁"`、`speech_score:0.0`、`visual_score:0.0`、`transcript:""`
- scene/AI 完成分支统一走 `_finalize_analysis(room_id, highlights, mode)` 辅助函数
- **风险:低**。纯字段补齐，向后兼容

---

## 三、优化方案 P1：性能 / 正确性 / 资源保护

### P1-6 GPU 显存监控 + AI 预检查
**文件**: `lsc/core/services/resource_monitor.py`、`lsc/analyzer/model_manager.py`、`room_handler.py`

- 选型 **pynvml**（追加到 `requirements-ai.txt`）
- resource_monitor 仿 psutil try/import 模式新增 `collect_gpu_stats()`：返回 `{available, gpus:[{index,name,memory_total_mb,memory_used_mb,memory_percent,gpu_util_percent,encoder_util_percent}]}`，合并进 `collect_system_stats` 的 `gpu` 字段
- model_manager 新增 `check_gpu_memory_available(required_mb=2048)` → `(ok, reason, stats)`
- `handle_start_analysis` AI 分支前调用：显存不足 → whisper 降级 medium→base（1024MB）→ 仍不足或 `encoder_util>95` 拒绝并返回 `gpu_stats`
- 阈值常量: medium 2048 / base 1024 / CLIP 512 / NVENC 预留 512
- **风险:中**。无 NVIDIA 卡时 nvmlInit 抛异常需 try/except 兜底；预检查失败降级而非硬阻塞

### P1-7 AI 独立 semaphore + combined 并行
**文件**: `room_handler.py`（line 93-103、2136-2150）

- 新增 `_ai_semaphore = threading.Semaphore(1)`：AI 分析全程持锁防多房间挤爆 GPU（scene 分支不持锁）
- combined 并行：`ThreadPoolExecutor(2)` 同时跑 scene（CPU）和 AI 的 audio 阶段（GPU）；AI 的 visual 阶段需等 scene 完成
- **风险:中**。用 `with` 语句管理锁防死锁；日志加 room_id 前缀

### P1-8 FFmpeg 子进程可中断取消
**文件**: 新增 `lsc/utils/subprocess_runner.py`、`audio_analyzer.py`、`visual_analyzer.py`、`room_handler.py`

- 封装 `run_cancellable(cmd, cancel_check, timeout, ...)`：Popen + 轮询 cancel_check(0.5s) + terminate/kill 兜底
- audio extract_audio / visual extract_keyframes / scene _run_scene_analysis 三处复用
- **风险:中**。Windows FFmpeg 需测 `creationflags` 下 terminate 行为

### P1-9 关键帧分段提取
**文件**: `lsc/analyzer/visual_analyzer.py`（extract_keyframes line 151-254 / analyze line 383-443）

- extract_keyframes 支持 `time_range=(start,end)`，FFmpeg 加 `-ss -to`
- 按 30 分钟切段（30分钟=900帧，CLIP batch=32 约 30s）
- 跨段 change_score：保留前段末帧 embedding 参与下段首帧计算
- 时间戳全局化：加 `segment_start_offset`
- 段目录 `seg_{idx:04d}/`，推理完 `shutil.rmtree`
- **风险:中**。跨段 embedding 衔接数据流稍复杂

### P1-10 异常降级显式上报
**文件**: `lsc/analyzer/pipeline.py`（line 205-207、225-227、236-253）、`room_handler.py`

- analyze 返回值 `list|None` → `AnalysisResult` dataclass：`{highlights, cancelled, partial_failure:{stage,reason}|None, analysis_time_sec}`
- 不再静默置空，记录 failure_stage；融合全空时返回空列表+partial_failure 标志
- `handle_start_analysis` 透传 partial_failure 给前端，Modal 显示"部分维度失败"
- **风险:低**。调用点单一，同步改 room_handler line 2144-2169

---

## 四、优化方案 P2：体验 / 架构清理

### P2-11 NVENC 探测统一
**文件**: 新增 `lsc/core/services/hw_encode.py`；改 `mse_streamer.py`（删 line 39-75）、`recording_controller.py`（删 line 162-197/425-457）、`room_handler.py`（4 处 import）

- 公共模块提供 `check_nvenc(force=False)` + `check_encoder(codec)`，进程级 dict 缓存 + threading.Lock
- recording_controller 的 `is_nvenc_available` 保留为薄包装调用公共函数（保 mock 兼容）
- **风险:低**

### P2-12 视觉权重可配置化
**文件**: `fusion.py`（line 29-33）、`room_handler.py`、前端 Workbench line 961

- `_DEFAULT_WEIGHTS` 改 Valorant 友好默认：`{audio:0.55, visual:0.20, scene:0.25}`
- 预留 `weights_profile`（"valorant"/"general"/"custom"）映射预设
- 前端硬编码权重改为从 settings 读
- **风险:低**。默认值调整需回归测试

### P2-13 RoomSession 死代码清理
**文件**: `lsc/gui/multi_room/session.py`（line 63、65）

- 删除 `analysis_highlights`、`analysis_in_progress`（grep 确认无写入）
- **风险:低**

### P2-14 前端类型集中 + 状态上升 Zustand
**文件**: `types/index.ts`、`store/appStore.ts`、`Workbench/index.tsx`（删 line 16-35/69-74）、`AnalysisProgress.tsx`（删 line 15-20）

- types 集中: AnalysisMode/Highlight/AnalysisProgressInfo
- appStore 新增 analysis slice: analyzing/analysisMode/analysisResults/analysisProgress/showAnalysisModal/analysisSortBy + actions
- Workbench 6 个 useState → useAppStore 选择器
- **风险:低**。纯重构

### P2-15 结果 Modal 拆组件
**文件**: 新增 `Workbench/components/AnalysisResultModal.tsx`；`Workbench/index.tsx` 删 line 1425-1537

- 110 行 Modal JSX 拆独立组件，props: results/visible/onClose/onImport/sortBy/onSortChange
- `getScoreColor` 提取到 utils
- **风险:低**

---

## 五、新功能：高光分析并自动导出

### 5.1 功能定义

**能力 1 — 单房间"分析并导出"**
对选中房间高光分析 → 自动导入切片列表 → 自动批量导出到 output_dir。

**能力 2 — 多房间"同步分析导出"**
多选已对齐房间 → 弹窗选主直播间 → 仅分析主直播间 → 用 content_offset 映射高光时间段到所有目标房间 → 批量导出每个房间对应片段。

### 5.2 触发与交互（用户确认）

| 决策 | 选择 |
|------|------|
| 多房间入口 | 多选房间一键触发 + 弹窗选主直播间 |
| 未对齐处理 | 必须先对齐（content_offset 已知），未对齐时按钮置灰提示"请先一键对齐" |

### 5.3 后端实现

#### 5.3.1 对齐组记忆
**文件**: `lsc/gui/multi_room/session.py`、`lsc/gui/multi_room/manager.py`

- RoomSession 加字段 `align_group_id: str = ""`（session.py，content_offset 后）
- `manager.py` 的 align 完成回调（line 1195-1209）给参与房间设置同一 `align_group_id`（如 `f"align_{int(time.time())}"`）
- 房间重连/录制重启时重置 `align_group_id = ""`（与 content_offset 同步重置）
- 同步导出校验：所有 target_room_ids 的 `align_group_id` 一致且非空

#### 5.3.2 公共函数：构造 ExportProfile
**文件**: `python-backend/handlers/room_handler.py`

抽取 `handle_export_clip`(line 1373-1436) 的 profile 构造逻辑为：
```python
def _build_export_profile(preset_id: str, settings: dict) -> ExportProfile:
    """从 settings + preset_id 构造导出配置，供 export_clip 和 analysis_export 复用。"""
    ...
```

#### 5.3.3 新增消息 `start_analysis_export`
**文件**: `python-backend/handlers/room_handler.py`（handle_start_analysis 之后）

```python
@server.on('start_analysis_export')
async def handle_start_analysis_export(data):
    """高光分析并自动导出（单房间 / 多房间同步）。

    参数:
        main_room_id: str         — 做高光分析的主直播间
        target_room_ids: [str]    — 要导出的所有房间（含 main；单房间时=[main]）
        mode: 'scene'|'ai'|'combined'（默认 scene）
        whisper_model, weights, threshold  — 仅 AI/combined
        preset_id: str            — 导出预设
        job_prefix: str           — 前端关联进度用
    """
```

**编排流程**：
```
1. 校验
   - main_room_id 存在且有录制文件
   - 多房间时：target_room_ids 的 align_group_id 一致且非空
   - 每个 target_room 有录制文件
2. 在 _ai_executor（AI模式）或 _bridge_executor（scene模式）跑高光分析
   - 复用现有 _do_analysis 逻辑（含进度广播 analysis_progress）
   - 分析主房间 main_room_id
3. 分析完成后，bridge.call 切主线程提交批量导出（QThread 必须主线程创建）
   for 高光 [t1, t2] in highlights:
     for target_room R in target_room_ids:
       Δ = offset_main - offset_R          # 多房间映射；单房间 Δ=0
       mapped_start = max(0.0, t1 + Δ)
       mapped_end   = max(0.0, t2 + Δ)
       if mapped_start >= mapped_end: continue
       title = f"{R.streamer_name or R.room_id}_高光{i+1}_s{t1:.0f}"
       job_id = f"{job_prefix}-{i}-{R.room_id}"
       manager.start_export(R.room_id, mapped_start, mapped_end,
                            output_dir, title, profile, on_done, on_progress)
       export_jobs[job_id] = clip_id
4. 立即返回 {success, submitted_count, job_ids}
5. 每个导出独立广播 export_progress / clip_completed / clip_failed（复用现有事件）
6. 全部完成后广播 analysis_export_done {main_room_id, total, succeeded, failed}
```

**关键实现要点**：
- **不要复用 handle_export_clip**：它硬性要求 mark 且做墙钟映射，会把高光文件秒数当 MSE currentTime 减 2s 延迟导致错位
- **直接调 manager.start_export**：start_sec/end_sec 透传给 controller 不做映射，高光 start/end 已是录制文件秒数
- **闭包变量捕获用默认参数**：`def on_done(..., _jid=job_id, _start=mapped_start, ...)` 防循环变量引用末值
- **并发限制**：N高光×M房间=N个并行FFmpeg，NVENC 3-5路上限。建议用 `threading.Semaphore(3)` 限制同时导出数，或排队
- **on_done/on_progress 回调签名**：`on_done(success, output_path, error, size_mb, thumbnail_path)` / `on_progress(percent, elapsed, total)`，在 QThread 执行，用 `asyncio.run_coroutine_threadsafe(..., loop)` 切回广播

#### 5.3.4 时间映射公式（已核对）
offset 语义（compute_offset 注释 line 153-154 + session.py line 84-89）：正值=该房间内容超前，事件在录制文件时间戳更小。

```
目标房间 R 的导出区间 = [t1 + (offset_main - offset_R),  t2 + (offset_main - offset_R)]
```
- 单房间：Δ=0，直接用高光区间
- 多房间：offset_main - offset_R 可正可负，按公式映射
- content_offset 单房间不补偿（文件内部时间轴已含一切偏移），仅跨房间映射时用

### 5.4 前端实现

#### 5.4.1 房间列表多选
**文件**: `lsc-electron/src/pages/Workbench/index.tsx`（房间列表区域）

- 房间卡片增加 checkbox 多选
- 新增 `selectedRoomIds: string[]` 状态（上升 appStore）
- 多选时显示批量操作栏

#### 5.4.2 同步分析导出入口
- 批量操作栏"同步分析导出"按钮：仅当 selectedRoomIds.length >= 2 且所有选中房间 align_group_id 一致时可用
- 点击弹窗（Modal）：
  - 列出选中房间，单选"主直播间"（Radio）
  - 选择分析模式（scene/ai/combined）
  - 选择导出预设
  - 确认按钮 → send('start_analysis_export', {...})

#### 5.4.3 单房间"分析并导出"
- 选中单个房间时，分析按钮 Dropdown 增加"分析并导出"选项
- 点击 → send('start_analysis_export', {main_room_id, target_room_ids:[main_room_id], ...})

#### 5.4.4 多房间导出进度展示
- 复用现有 `export_progress`/`clip_completed`/`clip_failed` 监听（前端零改动）
- 新增 `analysis_export_done` 监听：汇总提示"导出完成：成功 X / 失败 Y"
- 切片列表展示多房间片段（clip 带 room_id + room_name 区分）

### 5.5 文件命名规则
- 单房间：`{streamer_name}_高光{i+1}_s{start秒}.mp4`
- 多房间：`{streamer_name}_高光{i+1}_s{start秒}.mp4`（每房间独立文件名，streamer_name 区分）
- 缩略图：`{title}_thumb.jpg` 同目录（ClipExporter 自动生成）

---

## 六、长视频分段策略汇总

| 维度 | 段长 | 重叠 | 聚合 |
|------|------|------|------|
| 音频提取 | 10分钟 | 30s | 段文件转录完即删，时间戳加 start_offset，重叠区文本相似度去重 |
| Whisper 转录 | 按段 | 30s | 同上 |
| 关键帧提取 | 30分钟 | 无 | 时间戳全局化，段目录隔离推理完即删 |
| CLIP 推理 | 按段 | 无 | 嵌入 concat |
| change_score | 跨段 | 保留前段末帧 embedding | 段边界衔接 |
| fusion 时间线 | 整体 | - | 段级结果合并后统一 align_timeline |
| 进度回调 | 段级 | - | "已处理 X/Y 段"映射百分比 |

---

## 七、导出链路复用要点（已学习确认）

1. **高光 start/end 已是录制文件时间轴秒数**，无需墙钟映射、无需 mark
2. **绕过 handle_export_clip**，直接调 `manager.start_export(room_id, start_sec, end_sec, ...)`
3. **bridge.call 包裹导出提交**（QThread 必须主线程创建）；分析在 `_ai_executor` 跑不阻塞主线程
4. **闭包变量默认参数捕获**
5. **content_offset 单房间不补偿**，仅多房间映射用 offset_main-offset_R
6. **Copy 预设在 start_sec>0 自动降级 libx264**（clip.py:262-275，保切口精度，预期行为）
7. **NVENC 并发 3-5 路上限**，需信号量限制
8. **文件名=title 消毒**，多房间必须在 title 拼 room_name
9. **回调签名**: on_done(success,path,error,size_mb,thumb) / on_progress(percent,elapsed,total)，QThread 执行需 run_coroutine_threadsafe 切回
10. **抽 `_build_export_profile(preset_id, settings)` 公共函数**复用

---

## 八、实施顺序

### 阶段一：优化 P0（长视频主场景 + 数据丢失）
1. P0-2 fusion 向量化（独立、低风险、立竿见影）
2. P0-1 音频分段（长视频核心）
3. P0-3 结果持久化（数据丢失修复）
4. P0-4 + P0-5 scene 进度与格式统一（同函数一起做）

### 阶段二：新功能（分析并导出）
5. 抽 `_build_export_profile` 公共函数
6. 后端对齐组记忆（align_group_id）
7. 后端 `start_analysis_export` 消息
8. 前端房间多选 + 同步导出入口 + 主直播间弹窗
9. 前端单房间"分析并导出"入口 + 进度展示

### 阶段三：优化 P1（资源/性能）
10. P1-8 FFmpeg 可取消（封装公共函数，铺路 P1-9）
11. P1-9 视觉分段（依赖 P1-8）
12. P1-6 GPU 监控（独立模块可并行）
13. P1-7 semaphore + combined 并行（依赖 P0-4）
14. P1-10 异常降级上报（收尾 P1）

### 阶段四：优化 P2（架构清理）
15. P2-11 NVENC 探测统一
16. P2-12 视觉权重可配置化
17. P2-13 RoomSession 死代码清理
18. P2-14 前端类型集中 + 状态上升 Zustand
19. P2-15 结果 Modal 拆组件

---

## 九、关键文件清单

| 文件 | 改动点 |
|------|--------|
| `lsc/analyzer/fusion.py` | P0-2, P2-12 |
| `lsc/analyzer/audio_analyzer.py` | P0-1, P1-8 |
| `lsc/analyzer/visual_analyzer.py` | P1-8, P1-9 |
| `lsc/analyzer/pipeline.py` | P1-10 |
| `lsc/analyzer/model_manager.py` | P1-6 |
| `lsc/core/services/resource_monitor.py` | P1-6 |
| `python-backend/handlers/room_handler.py` | P0-3,4,5 / P1-6,7 / 新功能5.3 / `_build_export_profile` |
| `python-backend/persistence.py` | P0-3 |
| `lsc/gui/multi_room/session.py` | P2-13 / 新功能 align_group_id 字段 |
| `lsc/gui/multi_room/manager.py` | 新功能 align_group_id 设置 |
| 新增 `lsc/core/services/hw_encode.py` | P2-11 |
| 新增 `lsc/utils/subprocess_runner.py` | P1-8 |
| `lsc/core/services/mse_streamer.py` | P2-11 |
| `lsc/gui/pages/recording_controller.py` | P2-11 |
| `lsc-electron/src/types/index.ts` | P2-14 |
| `lsc-electron/src/store/appStore.ts` | P2-14 / 新功能 selectedRoomIds |
| `lsc-electron/src/pages/Workbench/index.tsx` | P0-4,5 / P2-14,15 / 新功能5.4 |
| `lsc-electron/src/components/AnalysisProgress.tsx` | P2-14 |
| 新增 `lsc-electron/src/pages/Workbench/components/AnalysisResultModal.tsx` | P2-15 |
| 新增 `lsc-electron/src/pages/Workbench/components/SyncAnalysisExportModal.tsx` | 新功能5.4.2 |
| `requirements-ai.txt` | P1-6(追加 pynvml) |

---

## 十、分发方案（方向，不展开）

- 新增 `scripts/install_ai_deps.bat/sh`：检测 Python + CUDA → 按需装 GPU/CPU 版 torch + faster-whisper + open-clip + Pillow + pynvml → 预下载默认模型
- Electron 设置页加"安装 AI 分析依赖"按钮调用脚本并实时显示输出
- 依赖未装时 `install_guide:True`（已实现 line 2114-2118）+ 前端安装引导链接

---

## 十一、风险矩阵

| 风险项 | 概率 | 影响 | 缓解 |
|--------|------|------|------|
| 长视频分段去重算法不准 | 中 | 高光重复/丢失 | 先用简单首尾50字匹配，复杂场景迭代 |
| 多房间 offset 映射方向错 | 低 | 切口错位 | 已核对 compute_offset 注释；导出前可加预览校验 |
| NVENC 并发超限导致导出失败 | 中 | 部分导出失败 | 信号量限制 3 路 + 失败重试 |
| bridge.call 死锁 | 低 | UI 卡死 | 分析在 executor 线程，仅导出提交切主线程且快速返回 |
| GPU 显存不足 OOM | 中 | 分析失败/录制掉帧 | P1-6 预检查 + 模型降级 |
| scene Popen 流式读 Windows 缓冲问题 | 中 | 进度不更新 | bufsize=0 + 手动 split |
| AI 依赖未安装用户点分析 | 高 | 报错 | ImportError 捕获 + install_guide（已实现） |
