# 本地工作区 vs GitHub 仓库 — 逐文件差异清单

> 生成时间：2026-10-19 00:01:53
> 基准：GitHub 远端实时状态（生成前已 `git fetch --all --prune`）
> 仓库：`origin` = https://github.com/Lawrence7y/LSC ，GitHub 侧只有 1 个分支 `main` = `dbeb707d`（v1.0.17）
> 本机：`D:\Project\直播切片多人`，当前分支 `feat/jev-highlight-judge` = `6529bd49`
> 复现命令见 §12。

图例：**增/删** = 相对基准的新增/删除行数；**新增符号** = 该文件里新出现的函数/类/导出；**删除符号** = 消失的函数/类/导出；`-` 表示无（多为文档/配置或纯逻辑改动）。

## 0. 总览

| 类别 | 文件数 | GitHub 能否看到 | 后果 |
| :--- | ---: | :--- | :--- |
| A 已提交未推送 | 30 | 看不到 | 远端缺分支，换机器无法继续 |
| B 工作区已改未提交 | 137 | 看不到 | 改动无备份、不可回滚 |
| C 工作区已删未提交 | 10 | GitHub **仍保留旧文件** | 文档与仓库双向错位 |
| D 已 `git add` 未提交 | 14 | 看不到 | 同上 |
| E 未跟踪源码/文档 | 53 | **完全不存在** | 磁盘故障即永久丢失 |
| F 未跟踪本地产物 | 15 | 不应提交 | `.gitignore` 覆盖不全 |
| G GitHub 有、本地已删/移走 | 13 | 本地已无 | 反向差异 |

合计约 **+11377 / −9162 行**（A+B+C+D）。

**结论**：标签层 100% 对应、本地 `main` 与 `origin/main` 0/0 一致；但**代码内容层严重不对应** —— `CLAUDE.md` 中标为「2026-09-18 / 09-24 已收口」的多数实现只存在于这台机器上。

## 1. 远端 / 分支 / 标签 对应矩阵

### 1.1 两个 GitHub 仓库

| 远端 | 地址 | GitHub 上的分支 | 与本地 `main` 的历史关系 |
| :--- | :--- | :--- | :--- |
| `origin` | https://github.com/Lawrence7y/LSC | `feat/jev-highlight-judge=6529bd49`, `main=dbeb707d` | 本地 `main` 与之完全一致 |
| `full` | https://github.com/Lawrence7y/- | `codex/p1-ui-fixes=33968fed`, `master=b6ef25b5` | **无共同祖先**（`git merge-base` 返回空），相对 `origin/main` 分叉 32/246 与 33/246 |

### 1.2 本地分支

| 本地分支 | HEAD | 对应远端 |
| :--- | :--- | :--- |
| `feat/jev-highlight-judge` | `6529bd49` | `origin/feat/jev-highlight-judge` |
| `fix/debt-20260918` | `dbeb707d` | **GitHub 上无此分支** |
| `main` | `dbeb707d` | `origin/main` — **0 ahead / 0 behind，完全一致** |

### 1.3 标签

| 标签 | 本地 | GitHub | 一致性 | 提交可从 origin/main 到达 |
| :--- | :--- | :--- | :--- | :--- |
| `v1.0.0` | `67c13811` | `67c13811` | 一致 | 否 |
| `v1.0.11` | `9a8792fd` | `9a8792fd` | 一致 | 是 |
| `v1.0.16` | `8e0705cd` | `8e0705cd` | 一致 | 是 |
| `v1.0.17` | `dbeb707d` | `dbeb707d` | 一致 | 是 |
| `v1.5.0` | `33968fed` | `33968fed` | 一致 | 否 |
| `v2.0.0` | `6da1013e` | `6da1013e` | 一致 | 是 |
| `v3.0.0` | `9772e09f` | `9772e09f` | 一致 | 是 |
| `v3.0.21` | `24312c0e` | `24312c0e` | 一致 | 是 |
| `v3.0.22` | `15ad20d6` | `15ad20d6` | 一致 | 是 |
| `v3.0.23` | `d5d8fc7b` | `d5d8fc7b` | 一致 | 是 |

标签层**无漂移、无缺失**。`v1.0.0` / `v1.5.0` / `v2.0.0` / `v3.0.0` 指向的提交不在 `main` 后代链上（历史遗留分叉线，`v1.5.0` = `33968fed` 正是 `full/codex/p1-ui-fixes` 的 HEAD），但对象在 GitHub 上存在，属对应状态。

## 2. A 类 — 已提交但未推送（`origin/main..HEAD`）

- `6529bd49` feat: diagnostic pack export, Store update channel, export codec fallback, output dir policy（2026-09-22）
- `7e92073f` harden: supply chain pin, DPAPI cookies, path policy, persistence bak, codec units（2026-09-22）

| 文件 | 状态 | 增/删 | 所属层 | 新增符号 | 删除符号 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `.pre-commit-config.yaml` | 修改 | 13/4 | 构建 / 依赖 / 规范 | - | - |
| `docs/compose/spec/hardening-and-diagnostics.md` | 新增 | 42/0 | 文档 | - | - |
| `lsc-electron/electron/diagnosticPack.ts` | 新增 | 217/0 | 前端 / Electron 主进程 | `LOG_TAIL_LINES`, `SENSITIVE_KEY_RE`, `INLINE_SECRET_RE`, `HEADER_BLOB_RE` …(+30) | - |
| `lsc-electron/electron/main.ts` | 修改 | 152/4 | 前端 / Electron 主进程 | `trusted`, `jy`, `parsed`, `outputDir` …(+16) | - |
| `lsc-electron/electron/preload.ts` | 修改 | 11/2 | 前端 / Electron 主进程 | - | - |
| `lsc-electron/scripts/install-runtime-dependencies.ps1` | 修改 | 2/3 | 前端 / 构建脚本 | - | - |
| `lsc-electron/scripts/prep-bundle.ps1` | 修改 | 2/3 | 前端 / 构建脚本 | - | - |
| `lsc-electron/src/i18n/en.part.settings.ts` | 修改 | 27/0 | 前端 / 文案 | - | - |
| `lsc-electron/src/pages/Settings/index.tsx` | 修改 | 112/179 | 前端 / 页面组件 | `handleExportDiagnosticPack`, `api`, `result` | - |
| `lsc-electron/src/services/exportPresets.ts` | 修改 | 21/4 | 前端 / 播放与通信 | `_HW_CODEC_MAP`, `resolveExportCodec` | - |
| `lsc-electron/src/types/index.ts` | 修改 | 7/0 | 前端 / 类型契约 | - | - |
| `lsc-electron/tsconfig.node.json` | 修改 | 1/1 | 前端 / 配置 | - | - |
| `lsc/core/controller.py` | 修改 | 29/4 | 核心层 / 编排与服务 | `_normalize_video_bitrate` | - |
| `lsc/core/services/mse_streamer.py` | 修改 | 45/6 | 核心层 / 编排与服务 | `_bitrate_kbps`, `_bitrate_arg` | - |
| `lsc/exporter/jianying_draft.py` | 修改 | 55/24 | 核心层 / 导出 | `_cleanup_failed_draft` | - |
| `lsc/platforms/base.py` | 修改 | 49/14 | 核心层 / 平台适配 | `ERROR_PARSER_STALE`, `redirect_request`, `_opener_for_proxy`, `platform` …(+2) | `redirect_request`, `_opener_for_proxy` |
| `lsc/platforms/cookie_helper.py` | 修改 | 4/3 | 核心层 / 平台适配 | - | - |
| `lsc/platforms/lease_manager.py` | 修改 | 6/2 | 核心层 / 平台适配 | - | - |
| `lsc/recorder/capture.py` | 修改 | 21/13 | 核心层 / 录制 | - | - |
| `lsc/recorder/segmented.py` | 删除 | 0/284 | 核心层 / 录制 | - | `SegmentedStartResult`, `SegmentedStopResult`, `SegmentedRecorder`, `__init__` …(+7) |
| `lsc/utils/process_launcher.py` | 修改 | 168/2 | 核心层 / 工具 | `hidden_run_kwargs`, `MAX_CONCURRENT_SPAWNS`, `MAX_LIVE_CHILDREN`, `_count_transient_spawn` …(+10) | `hidden_run_kwargs` |
| `python-backend/__init__.py` | 删除 | 0/1 | 桥接层 / 后端服务 | - | - |
| `python-backend/dependency_manager.py` | 修改 | 59/15 | 桥接层 / 后端服务 | `_ffmpeg_download_urls`, `_log_unhandled_exception` | `_log_unhandled_exception` |
| `python-backend/handlers/room_handler.py` | 修改 | 490/403 | 桥接层 / WS handlers | `_room_url_shape`, `_validate_room_url_candidate`, `_system_stats_extra`, `_jev_mode_and_threshold` …(+8) | `_validate_room_url_candidate`, `_clear_analysis_job`, `_authority_snapshot_for`, `_set_export_job_state` …(+20) |
| `python-backend/persistence.py` | 修改 | 68/8 | 桥接层 / 后端服务 | `room_payload_violation` | - |
| `python-backend/server.py` | 修改 | 37/3 | 桥接层 / 后端服务 | - | - |
| `python-backend/start.py` | 修改 | 8/0 | 桥接层 / 后端服务 | - | - |
| `python-backend/run_and_export.py => scripts/run_and_export.py` | 重命名 | 0/0 | 离线脚本 | - | - |
| `python-backend/run_full_export.py => scripts/run_full_export.py` | 重命名 | 0/0 | 离线脚本 | - | - |
| `tests/test_segmented_recorder.py` | 删除 | 0/86 | 测试 | - | `test_segmented_command_uses_mkv_segments_and_headers`, `test_segmented_recorder_start_stop_with_fake_process`, `FakeProcess`, `__init__` …(+7) |

## 3. B 类 — 工作区已修改但未提交（基准 = `HEAD`）

这些改动只写在磁盘上，既没提交也没推送。

| 文件 | 增/删 | 所属层 | 新增符号 | 删除符号 |
| :--- | :--- | :--- | :--- | :--- |
| `.github/workflows/ci.yml` | 129/12 | CI/CD | `PY_VER`, `LOCK_VER`, `MAJOR`, `SORT` …(+2) | - |
| `.github/workflows/core-guard.yml` | 12/28 | CI/CD | - | - |
| `.gitignore` | 9/0 | 构建 / 依赖 / 规范 | - | - |
| `CHANGELOG.md` | 839/0 | 文档（权威参考） | - | - |
| `CLAUDE.md` | 194/43 | 文档（权威参考） | - | - |
| `docs/CODING_STANDARD.md` | 16/1 | 文档 | - | - |
| `docs/FEATURE-INVENTORY.md` | 4/4 | 文档 | - | - |
| `docs/PROJECT_DESIGN.md` | 13/7 | 文档 | - | - |
| `docs/SUBSYSTEM_ANALYSIS.md` | 3/3 | 文档 | - | - |
| `docs/reports/MODULE_AUDIT_REPORT.md` | 4/0 | 文档 | - | - |
| `docs/reports/fix-plan-20260913-review-during-analysis.md` | 2/2 | 文档 | - | - |
| `docs/spec-websocket-protocol.md` | 1/1 | 文档 | - | - |
| `lsc-electron/electron/main.ts` | 93/16 | 前端 / Electron 主进程 | `goneProc`, `handleBackendGone`, `_readyToShowFired`, `_loadEntry` …(+7) | - |
| `lsc-electron/package-lock.json` | 2/2 | 前端 / 配置 | - | - |
| `lsc-electron/package.json` | 3/1 | 前端 / 配置 | - | - |
| `lsc-electron/src/components/AnalysisProgress.tsx` | 17/170 | 前端 / 通用组件 | `fixedRange`, `confirmedPercent` | `ExportSummary`, `total`, `h`, `m` …(+15) |
| `lsc-electron/src/components/VideoPreview.tsx` | 38/19 | 前端 / 通用组件 | `nextSource`, `sent` | `epochChanged`, `phaseText`, `phaseHint` |
| `lsc-electron/src/hooks/useWebSocket.ts` | 34/90 | 前端 / Hooks | `_mseCache`, `_mseCachePruneTimer`, `stopMseCachePruning`, `cachedInit` | `_lastBroadcastSeq`, `_mseInitCache`, `_mseInitCacheTime`, `_mseSegmentCache` …(+12) |
| `lsc-electron/src/i18n/en.part.ui.ts` | 3/0 | 前端 / 文案 | - | - |
| `lsc-electron/src/i18n/en.part.workbench.ts` | 2/0 | 前端 / 文案 | - | - |
| `lsc-electron/src/pages/Workbench/components/ControlBar.tsx` | 61/36 | 前端 / 页面组件 | - | - |
| `lsc-electron/src/pages/Workbench/index.tsx` | 140/209 | 前端 / 页面组件 | `hasRoomSelection`, `autoBoundRoomId`, `autoBoundRoom`, `newClip` …(+16) | `markIn`, `markOut`, `recordingStart`, `offset` …(+21) |
| `lsc-electron/src/services/localFileMseSource.test.ts` | 280/39 | 前端 / 播放与通信 | `waitUntil`, `deadline`, `LOCATE_CEILING_MS`, `LOCATE_TEST_TIMEOUT_MS` …(+21) | `error`, `bytes`, `api`, `queuedGetter` |
| `lsc-electron/src/services/localFileMseSource.ts` | 177/17 | 前端 / 播放与通信 | `LOCATE_SLACK_SEC`, `MAX_LOCATE_PROBE_ROUNDS`, `_INDEX_CACHE_MAX_ENTRIES`, `oldest` …(+24) | `estimate`, `windowEnd`, `windowStart`, `windowLen` …(+3) |
| `lsc-electron/src/services/mediaSourcePlayer.test.ts` | 425/0 | 前端 / 播放与通信 | `events`, `player`, `internals`, `video` …(+14) | - |
| `lsc-electron/src/services/mediaSourcePlayer.ts` | 98/3 | 前端 / 播放与通信 | `request`, `accepted`, `now`, `video` …(+4) | - |
| `lsc-electron/src/services/websocket.test.ts` | 200/0 | 前端 / 播放与通信 | `tokenPromise`, `client`, `connectPromise`, `socket` …(+7) | - |
| `lsc-electron/src/services/websocket.ts` | 80/7 | 前端 / 播放与通信 | `message`, `payload`, `seq`, `coalesced` …(+2) | `message` |
| `lsc-electron/src/store/appStore.ts` | 9/0 | 前端 / 状态管理 | - | - |
| `lsc-electron/src/utils/analysisProgress.test.ts` | 262/1 | 前端 / 纯函数层 | `t`, `emptySummary`, `r`, `byPhase` …(+1) | - |
| `lsc-electron/src/utils/analysisProgress.ts` | 198/0 | 前端 / 纯函数层 | `formatDuration`, `total`, `h`, `m` …(+22) | - |
| `lsc-electron/src/utils/clipExportPolicy.test.ts` | 145/0 | 前端 / 纯函数层 | `room`, `s` | - |
| `lsc-electron/src/utils/clipExportPolicy.ts` | 80/0 | 前端 / 纯函数层 | `isApproximateClip`, `resolveManualRecordingRange`, `markIn`, `markOut` …(+10) | - |
| `lsc-electron/src/utils/previewAudioAligner.ts` | 4/58 | 前端 / 纯函数层 | - | `_driftCorrectionTimers`, `cancelDriftCorrection`, `timer`, `applyOffsetWithDriftCorrection` …(+10) |
| `lsc-electron/src/utils/timelineCoords.test.ts` | 37/1 | 前端 / 纯函数层 | `rec`, `prev`, `idle` | - |
| `lsc-electron/src/utils/timelineCoords.ts` | 22/0 | 前端 / 纯函数层 | `pickAutoBindRoomId`, `recording`, `candidates` | - |
| `lsc/analyzer/ocr_detector.py` | 4/2 | 核心层 / 分析管线 | - | - |
| `lsc/analyzer/valorant_broadcast.py` | 24/24 | 核心层 / 分析管线 | - | - |
| `lsc/analyzer/valorant_ocr_rounds.py` | 1/1 | 核心层 / 分析管线 | - | - |
| `lsc/analyzer/valorant_plugin.py` | 5/6 | 核心层 / 分析管线 | - | - |
| `lsc/core/__init__.py` | 3/33 | 核心层 / 编排与服务 | - | - |
| `lsc/core/controller.py` | 8/4 | 核心层 / 编排与服务 | - | - |
| `lsc/core/orchestrator.py` | 538/249 | 核心层 / 编排与服务 | `_mark_failed_candidate`, `_quarantine_failed_probe_candidates`, `_acquire_pending_slot`, `_release_pending_slot` …(+9) | `_mark_failed_candidate`, `_quarantine_failed_probe_candidates` |
| `lsc/core/recording_layout.py` | 2/1 | 核心层 / 编排与服务 | - | - |
| `lsc/core/services/ingest_supervisor.py` | 13/10 | 核心层 / 编排与服务 | `health` | `health` |
| `lsc/core/services/mse_streamer.py` | 12/7 | 核心层 / 编排与服务 | - | - |
| `lsc/core/services/shared_ingest.py` | 90/30 | 核心层 / 编排与服务 | `bind_lease` | `bind_lease` |
| `lsc/core/services/timeline_service.py` | 8/1 | 核心层 / 编排与服务 | - | - |
| `lsc/core/session.py` | 3/1 | 核心层 / 编排与服务 | - | - |
| `lsc/editor/audio_aligner.py` | 6/178 | 核心层 / 音频对齐 | - | `AUDIO_DURATION`, `_is_network_source`, `extract_audio_pcm`, `align_rooms` |
| `lsc/platforms/acceptance.py` | 10/3 | 核心层 / 平台适配 | - | - |
| `lsc/platforms/bilibili.py` | 6/2 | 核心层 / 平台适配 | - | - |
| `lsc/platforms/candidate_health.py` | 4/1 | 核心层 / 平台适配 | - | - |
| `lsc/platforms/credentials.py` | 83/8 | 核心层 / 平台适配 | `invalidation_info`, `invalidate_credentials_on_auth_failure` | - |
| `lsc/platforms/douyin.py` | 16/8 | 核心层 / 平台适配 | - | - |
| `lsc/platforms/douyu.py` | 6/5 | 核心层 / 平台适配 | - | - |
| `lsc/platforms/generic.py` | 3/1 | 核心层 / 平台适配 | - | - |
| `lsc/platforms/huya.py` | 38/9 | 核心层 / 平台适配 | `_fallback_to_web_token` | - |
| `lsc/platforms/kuaishou.py` | 6/2 | 核心层 / 平台适配 | - | - |
| `lsc/platforms/probe.py` | 25/31 | 核心层 / 平台适配 | - | - |
| `lsc/platforms/recovery_policy.py` | 30/0 | 核心层 / 平台适配 | `_account_ref_of` | - |
| `lsc/platforms/registry.py` | 39/6 | 核心层 / 平台适配 | `resolve_stream_v2`, `probe_candidates`, `select_stream_lease`, `select_ingest_lease` …(+1) | `resolve_stream_v2`, `probe_candidates`, `select_stream_lease`, `select_ingest_lease` …(+1) |
| `lsc/platforms/resolver.py` | 33/64 | 核心层 / 平台适配 | - | `_invalidate_credentials_on_auth_failure` |
| `lsc/platforms/url_policy.py` | 24/5 | 核心层 / 平台适配 | `redirect_request` | `redirect_request` |
| `lsc/platforms/weibo.py` | 4/1 | 核心层 / 平台适配 | - | - |
| `lsc/platforms/xiaohongshu.py` | 9/3 | 核心层 / 平台适配 | - | - |
| `lsc/recorder/__init__.py` | 0/8 | 核心层 / 录制 | - | - |
| `lsc/recorder/assets.py` | 2/1 | 核心层 / 录制 | - | - |
| `lsc/recorder/capture.py` | 16/4 | 核心层 / 录制 | - | - |
| `lsc/recorder/timeline.py` | 6/2 | 核心层 / 录制 | - | - |
| `lsc/utils/cancellable_ffmpeg.py` | 0/106 | 核心层 / 工具 | - | `CancellableFFmpeg`, `__init__`, `start`, `poll` …(+5) |
| `lsc/utils/error_messages.py` | 4/1 | 核心层 / 工具 | `humanize_error_with_suggestion` | `humanize_error_with_suggestion` |
| `pyproject.toml` | 104/4 | 构建 / 依赖 / 规范 | - | - |
| `python-backend/broadcast_hub.py` | 157/59 | 桥接层 / 后端服务 | `_offer_locked`, `_offer`, `broadcast_health` | - |
| `python-backend/continuous_finalization.py` | 1/3 | 桥接层 / 后端服务 | - | - |
| `python-backend/dependency_manager.py` | 3/2 | 桥接层 / 后端服务 | - | - |
| `python-backend/handlers/alignment_handlers.py` | 10/12 | 桥接层 / WS handlers | - | - |
| `python-backend/handlers/analysis_handlers.py` | 30/0 | 桥接层 / WS handlers | - | - |
| `python-backend/handlers/export_handlers.py` | 11/11 | 桥接层 / WS handlers | - | `_safe_float` |
| `python-backend/handlers/jianying_handlers.py` | 14/8 | 桥接层 / WS handlers | - | - |
| `python-backend/handlers/recording_handlers.py` | 93/27 | 桥接层 / WS handlers | `read_room_recording_status`, `wait_recording_stopped` | - |
| `python-backend/handlers/room_handler.py` | 18/8 | 桥接层 / WS handlers | - | - |
| `python-backend/handlers/room_utils.py` | 1/8 | 桥接层 / WS handlers | - | `safe_float` |
| `python-backend/main.py` | 69/29 | 桥接层 / 后端服务 | - | - |
| `python-backend/persistence.py` | 30/8 | 桥接层 / 后端服务 | - | - |
| `python-backend/server.py` | 60/17 | 桥接层 / 后端服务 | `broadcast` | `broadcast` |
| `python-backend/start.py` | 51/19 | 桥接层 / 后端服务 | - | - |
| `requirements.txt` | 5/1 | 构建 / 依赖 / 规范 | - | - |
| `scripts/rebuild_draft_from_finalization.py` | 2/1 | 离线脚本 | - | - |
| `scripts/recover_and_backfill_broadcast.py` | 5/8 | 离线脚本 | - | - |
| `scripts/recover_recording.py` | 0/1 | 离线脚本 | - | - |
| `scripts/valorant_vision/build_hard_dataset.py` | 1/0 | 离线脚本 | - | - |
| `scripts/valorant_vision/build_marker_roi_dataset.py` | 1/2 | 离线脚本 | - | - |
| `scripts/valorant_vision/eval_domain_val.py` | 1/1 | 离线脚本 | - | - |
| `scripts/valorant_vision/eval_source_dataset.py` | 17/8 | 离线脚本 | - | - |
| `scripts/valorant_vision/ingest_broadcast_20260804.py` | 2/1 | 离线脚本 | - | - |
| `scripts/valorant_vision/promote_model.py` | 0/1 | 离线脚本 | - | - |
| `scripts/valorant_vision/rebuild_source_separated_datasets.py` | 1/3 | 离线脚本 | - | - |
| `scripts/valorant_vision/reeval_replay_verified.py` | 4/2 | 离线脚本 | - | - |
| `scripts/valorant_vision/train_onnx_finetune.py` | 1/2 | 离线脚本 | - | - |
| `scripts/valorant_vision/verify_broadcast_replay.py` | 1/1 | 离线脚本 | - | - |
| `tests/test_ai_clip_label_upsert.py` | 1/1 | 测试 | - | - |
| `tests/test_audio_aligner.py` | 4/120 | 测试 | `AUDIO_DURATION` | `TestExtractAudioPcm`, `test_returns_empty_array_on_ffmpeg_failure`, `test_returns_empty_array_on_empty_output`, `TestAlignRooms` …(+6) |
| `tests/test_broadcast_audit_batch_throughput.py` | 2/2 | 测试 | - | - |
| `tests/test_broadcast_draft_overlap_accounting.py` | 1/1 | 测试 | `overlaps` | `overlaps` |
| `tests/test_broadcast_draft_silent_drop.py` | 1/1 | 测试 | - | - |
| `tests/test_broadcast_export_authority_lifetime.py` | 8/17 | 测试 | `lookup` | - |
| `tests/test_broadcast_export_gate_parity.py` | 2/0 | 测试 | - | - |
| `tests/test_build_retrain_manifests.py` | 1/1 | 测试 | - | - |
| `tests/test_clip_refine_state.py` | 12/21 | 测试 | - | - |
| `tests/test_continuous_analysis_guards.py` | 6/21 | 测试 | - | `test_round_end_events_merge_keeps_last_spike` |
| `tests/test_cookie_helper.py` | 4/2 | 测试 | - | - |
| `tests/test_cross_feature_guards.py` | 11/4 | 测试 | - | - |
| `tests/test_dvr_mirror_and_review_removal.py` | 3/3 | 测试 | - | - |
| `tests/test_frontend_stability_guards.py` | 112/1865 | 测试 | `test_backend_mse_broadcast_uses_binary_helper`, `test_backend_mse_binary_frame_magic_matches_frontend`, `test_python_handler_clip_naming_helpers`, `test_room_handler_mute_awaits_before_broadcast` …(+4) | `test_websocket_defines_disconnected_queue_policy`, `test_pipeline_health_nested_values_use_structural_equality`, `test_pipeline_health_comparator_tracks_failure_kind`, `test_runtime_events_are_ordered_and_projected_into_room_health` …(+36) |
| `tests/test_mse_streamer.py` | 13/13 | 测试 | - | - |
| `tests/test_offline_file_mse.py` | 1/1 | 测试 | - | - |
| `tests/test_orchestrator.py` | 37/2 | 测试 | `test_call_waits_for_a_slot_instead_of_failing`, `_worker` | - |
| `tests/test_persistence.py` | 29/3 | 测试 | `test_is_not_stale_when_only_mtime_changed`, `test_is_stale_when_mtime_and_size_changed`, `test_is_stale_mtime_changed_without_size_evidence` | `test_is_stale_mtime_changed` |
| `tests/test_persistence_coalesce.py` | 151/10 | 测试 | `_clean_pending`, `_FakeTimer`, `__init__`, `start` …(+8) | `test_schedule_save_rooms_coalesces_writes` |
| `tests/test_platform_fetch_direct_default.py` | 0/2 | 测试 | - | - |
| `tests/test_prepare_label_ui_dataset.py` | 1/1 | 测试 | - | - |
| `tests/test_recording_layout.py` | 1/1 | 测试 | - | - |
| `tests/test_resolve_real_video_path.py` | 1/0 | 测试 | - | - |
| `tests/test_resource_caps.py` | 0/30 | 测试 | - | `test_frame_capture_buffer_capped_when_eoi_missing`, `_FakeStdout`, `__init__`, `read` |
| `tests/test_resource_monitor.py` | 4/1 | 测试 | - | - |
| `tests/test_source_separated_datasets.py` | 1/1 | 测试 | - | - |
| `tests/test_stability_latency_guards.py` | 20/0 | 测试 | `CAPTURE`, `test_read_side_pipes_stay_blocking`, `test_capture_stderr_reader_exit_is_logged` | - |
| `tests/test_stream_url_expiry.py` | 1/0 | 测试 | - | - |
| `tests/test_supervised_recovery.py` | 4/1 | 测试 | - | - |
| `tests/test_synced_continuous_analysis.py` | 5/0 | 测试 | - | - |
| `tests/test_timeline_service.py` | 26/0 | 测试 | `test_invalidate_listener_registration_is_idempotent`, `listener` | - |
| `tests/test_train_onnx_finetune.py` | 1/1 | 测试 | - | - |
| `tests/test_utils.py` | 2/96 | 测试 | - | `TestErrorStats`, `test_record_and_count`, `test_record_multiple_same_code`, `test_clear_resets_everything` …(+7) |
| `tests/test_ux_important_followups.py` | 9/2 | 测试 | - | - |
| `tests/test_valorant_broadcast_thresholds.py` | 1/1 | 测试 | - | - |
| `tests/test_valorant_ocr_rounds.py` | 1/0 | 测试 | - | - |

## 4. C 类 — 工作区已删除但未提交（GitHub 上仍是旧版）

`CLAUDE.md` 多处写「已于 2026-09-18 删除」，但 `origin/main` 里这些文件**依然都在**：

| 文件 | 删除行数 | 所属层 | 该文件原本是什么（取自 `origin/main`） |
| :--- | ---: | :--- | :--- |
| `lsc/analyzer/sound_detector.py` | 299 | 核心层 / 分析管线 | 音频频谱事件检测器。 |
| `lsc/analyzer/valorant_observer_scoreboard.py` | 224 | 核心层 / 分析管线 | Valorant 官方赛事/Observer HUD 顶部比分状态追踪器。 |
| `lsc/core/services/export_service.py` | 449 | 核心层 / 编排与服务 | 导出服务 — 封装视频切片导出业务逻辑，与 UI 无关。 |
| `lsc/core/services/frame_capture.py` | 262 | 核心层 / 编排与服务 | 预览帧抓取服务 — 为 Electron 前端通过 FFmpeg 抓取直播流 JPEG 帧。 |
| `lsc/core/services/mse_sender.py` | 96 | 核心层 / 编排与服务 | MSE 发送器 — 每房间独立，背压控制。 |
| `lsc/utils/error_stats.py` | 106 | 核心层 / 工具 | Error statistics tracking for monitoring and diagnostics.""" |
| `tests/test_cancellable_ffmpeg.py` | 46 | 测试 | from __future__ import annotations |
| `tests/test_core_export_service.py` | 412 | 测试 | 核心导出服务单元测试。 |
| `tests/test_mse_sender.py` | 158 | 测试 | MseSender 背压控制测试 — TDD 先行。""" |
| `tests/test_valorant_observer_scoreboard.py` | 112 | 测试 | Unit tests for ObserverScoreboardTracker.""" |

A 类提交里另有已删除的 `lsc/recorder/segmented.py`、`tests/test_segmented_recorder.py`、`python-backend/__init__.py`，并把 `python-backend/run_and_export.py`、`run_full_export.py` 移到 `scripts/`。

## 5. D 类 — 已 `git add` 但未提交

| 文件 | 行数 | 所属层 | 顶层符号 | 说明 |
| :--- | ---: | :--- | :--- | :--- |
| `lsc-electron/src/services/mediaSourcePlayer.behavior.test.ts` | +232 | 前端 / 播放与通信 | `SEGMENT`, `events`, `player`, `pause` | import { afterEach, describe, expect, it, vi } from 'vitest' |
| `lsc-electron/src/services/websocket.behavior.test.ts` | +190 | 前端 / 播放与通信 | `MockWebSocket`, `originalWebSocket`, `client`, `text` | import { afterEach, describe, expect, it, vi } from 'vitest' |
| `lsc-electron/src/utils/clipExportFlow.behavior.test.ts` | +127 | 前端 / 纯函数层 | `r`, `target`, `next` | import { describe, expect, it } from 'vitest' |
| `lsc-electron/src/utils/clipExportPolicy.behavior.test.ts` | +245 | 前端 / 纯函数层 | `base`, `rejected`, `authoritative`, `c` | import { describe, expect, it } from 'vitest' |
| `lsc-electron/src/utils/clipNaming.behavior.test.ts` | +111 | 前端 / 纯函数层 | `title`, `clips`, `base`, `assigned` | import { describe, expect, it } from 'vitest' |
| `lsc-electron/src/utils/diagnosticPack.test.ts` | +107 | 前端 / 纯函数层 | `raw`, `out`, `input`, `dump` | import { describe, expect, it } from 'vitest' |
| `lsc-electron/src/utils/previewAudioAligner.behavior.test.ts` | +137 | 前端 / 纯函数层 | `video`, `a`, `during`, `result` | import { afterEach, describe, expect, it, vi } from 'vitest' |
| `lsc-electron/src/utils/previewLifecycle.behavior.test.ts` | +82 | 前端 / 纯函数层 | `t`, `prev`, `copy`, `platforms` | import { describe, expect, it } from 'vitest' |
| `lsc-electron/src/utils/timelineCoords.behavior.test.ts` | +244 | 前端 / 纯函数层 | `c`, `now`, `started`, `s` | import { describe, expect, it } from 'vitest' |
| `lsc-electron/src/utils/timelineWindow.behavior.test.ts` | +198 | 前端 / 纯函数层 | `w`, `pinned`, `scrubbed`, `base` | import { describe, expect, it } from 'vitest' |
| `tests/test_batch1_secrets_and_data_guards.py` | +92 | 测试 | `test_normalize_video_bitrate_matches_orchestrator`, `test_build_encoder_args_uses_normalized_bitrate`, `test_jianying_failed_init_does_not_delete_existing_draft`, `BoomFolder` | 批次1止血回归：码率单位归一化 + 剪映草稿不误删用户已有草稿。""" |
| `tests/test_cookie_helper.py` | +4 | 测试 | `test_is_http_header_safe_rejects_replacement_char`, `test_decrypt_chrome_value_does_not_return_replacement_garbage`, `test_decrypt_chrome_value_accepts_plain_ascii`, `test_sanitize_cookie_map_drops_invalid_entries` | Cookie 解密与 HTTP 头安全过滤测试。""" |
| `tests/test_dependency_manager_supply_chain.py` | +117 | 测试 | `ROOT`, `dm`, `test_ffmpeg_urls_default_official_only`, `test_ffmpeg_mirrors_require_explicit_opt_in` | 批次2：dependency_manager 供应链真行为测试（非源码子串门禁）。""" |
| `tests/test_frontend_stability_guards.py` | +76 | 测试 | `ROOT`, `test_shared_preview_keeps_mse_event_names`, `test_backend_alignment_handler_reads_preview_diagnostics`, `test_backend_mse_broadcast_uses_binary_helper` | 前端稳定性的少量静态守卫。 |

## 6. E 类 — 未跟踪文件（GitHub 上完全不存在，`git log` 也查不到）

| 文件 | 大小(KB) | 所属层 | 顶层符号 | 说明 |
| :--- | ---: | :--- | :--- | :--- |
| `lsc-electron/src/utils/clipConfirmPolicy.test.ts` | 6.5 | 前端 / 纯函数层 | `base`, `rooms`, `target`, `otherRoom` …(+1) | import { describe, expect, it } from 'vitest' |
| `lsc-electron/src/utils/clipConfirmPolicy.ts` | 4.5 | 前端 / 纯函数层 | `ConfirmBoundsInput`, `resolveConfirmBounds`, `start`, `end` …(+1) | import type { ClipSegment } from '@/types' |
| `lsc-electron/src/utils/clipExportFlow.test.ts` | 5.8 | 前端 / 纯函数层 | `target`, `r`, `manual`, `a` …(+1) | import { describe, expect, it } from 'vitest' |
| `lsc-electron/src/utils/clipExportFlow.ts` | 4.1 | 前端 / 纯函数层 | `isSameClipEntry`, `ExportRequestSpec`, `buildExportRequest`, `source` …(+1) | import type { ClipSegment } from '@/types' |
| `lsc-electron/src/utils/manualClip.test.ts` | 4.6 | 前端 / 纯函数层 | `fullRoom`, `clips`, `indexes`, `c` …(+1) | import { describe, expect, it } from 'vitest' |
| `lsc-electron/src/utils/manualClip.ts` | 3.5 | 前端 / 纯函数层 | `ManualClipSource`, `countManualClips`, `distributeManualIndexes`, `seen` …(+1) | import type { ClipSegment } from '@/types' |
| `lsc-electron/src/utils/mseCacheStore.test.ts` | 4.3 | 前端 / 纯函数层 | `KB`, `store`, `first`, `drained` …(+1) | import { describe, expect, it, vi } from 'vitest' |
| `lsc-electron/src/utils/mseCacheStore.ts` | 6.7 | 前端 / 纯函数层 | `DEFAULT_INIT_MAX_ROOMS`, `DEFAULT_SEGMENT_MAX_PER_ROOM`, `DEFAULT_SEGMENT_MAX_BYTES`, `DEFAULT_TTL_MS` …(+1) | MSE init / media 段缓存的**有界存储**（2026-09-19 从 `hooks/useWebSocket.ts` 的模块级 |
| `lsc-electron/src/utils/previewAlignment.test.ts` | 3.3 | 前端 / 纯函数层 | `video`, `empty`, `clamped` | import { describe, expect, it, vi } from 'vitest' |
| `lsc-electron/src/utils/previewAlignment.ts` | 2.0 | 前端 / 纯函数层 | `NEGLIGIBLE_ALIGN_OFFSET_SEC`, `AlignmentSeekOutcome`, `applyAlignmentSeek`, `bufStart` …(+1) | 一键对齐结果落到预览播放头的唯一入口。 |
| `lsc-electron/src/utils/previewAudioAligner.test.ts` | 5.0 | 前端 / 纯函数层 | `m`, `a`, `tiny`, `weak` …(+1) | import { describe, expect, it } from 'vitest' |
| `lsc-electron/src/utils/previewLifecycle.test.ts` | 2.9 | 前端 / 纯函数层 | `t`, `phases`, `copy`, `blob` | import { describe, expect, it } from 'vitest' |
| `lsc-electron/src/utils/previewLifecycle.ts` | 2.1 | 前端 / 纯函数层 | `PreviewSource`, `shouldRebuildLivePlayer`, `previewPhaseCopy` | import type { I18nT } from '@/i18n' |
| `lsc-electron/src/utils/previewRecovery.test.ts` | 1.8 | 前端 / 纯函数层 | `now` | import { beforeEach, describe, expect, it } from 'vitest' |
| `lsc-electron/src/utils/previewRecovery.ts` | 1.6 | 前端 / 纯函数层 | `SINK_RESTART_WINDOW_MS`, `SINK_RESTART_MAX_PER_WINDOW`, `_attempts`, `kept` …(+1) | 预览自愈（后端 sink 重建）的房间级预算。 |
| `lsc-electron/src/pages/Settings/CookieStatusLine.tsx` | 3.1 | 前端 / 页面组件 | `CookieStatus`, `cookieStatusFrom`, `CookieStatusLine`, `configured` …(+1) | import { Tooltip } from 'antd' |
| `lsc-electron/src/pages/Settings/PlatformSelfCheck.tsx` | 5.8 | 前端 / 页面组件 | `ROW`, `LABEL`, `PlatformSelfCheck`, `unsub` …(+1) | import { useEffect, useState } from 'react' |
| `docs/reports/debt-audit-20260918.md` | 175.9 | 文档 | `QT_QPA_PLATFORM`, `roomManualCount` | 结构性债务与未收口缺陷审计（2026-09-18） |
| `docs/reports/jev-baseline-audit.md` | 36.2 | 文档 | `_jev_round_no` | Jev 接入前基线摸底审计（只读） |
| `docs/reports/jev-integration.md` | 37.0 | 文档 | `_is_auto_exportable_valorant_round`, `JudgeVerdict`, `judge_round`, `gate_out_reason` …(+1) | Jev 高光判决接入报告（feat/jev-highlight-judge） |
| `docs/reports/local-vs-github-20261018.md` | 44.7 | 文档 | - | 本地工作区 vs GitHub 仓库 — 逐文件差异清单 |
| `docs/reports/model-backbone-yolo-feasibility-20260919.md` | 9.7 | 文档 | - | 把两个视觉模型换成 YOLO 的可行性（内存 / 资源维度） |
| `lsc/analyzer/jev_judge.py` | 38.5 | 核心层 / 分析管线 | `JEV_ENDPOINT`, `JEV_MODEL`, `JEV_API_KEY_ENV`, `MODE_OFF` …(+1) | Jev（TypeSafe SystemOne）高光判决接入层。 |
| `lsc/utils/coerce.py` | 2.9 | 核心层 / 工具 | `try_float`, `safe_float`, `try_int`, `safe_int` | 异构字典（ffprobe JSON / 前端 payload / sidecar / settings）取标量的唯一口径。 |
| `lsc/platforms/diagnostics.py` | 2.2 | 核心层 / 平台适配 | `note_degradation`, `degradation_snapshot`, `reset` | 平台层降级事件计数（2026-09-18 债务收口 P2-2）。 |
| `python-backend/handlers/credential_handlers.py` | 10.8 | 桥接层 / WS handlers | `_credential_liveness`, `register_credential_handlers`, `handle_get_douyin_cookie_status`, `handle_save_douyin_cookies` …(+1) | 平台凭证 WebSocket 处理器（2026-09-18 拆分自 `room_handler.py`）。 |
| `python-backend/handlers/selfcheck_handlers.py` | 4.5 | 桥接层 / WS handlers | `_try_acquire`, `_release`, `_run_self_check`, `register_selfcheck_handlers` …(+1) | 平台自检 WS 路由（2026-09-18 债务收口 P2-2c）。 |
| `tests/test_align_preview_audio_route.py` | 9.7 | 测试 | `ROOT`, `RATE`, `_FakeServer`, `__init__` …(+1) | `align_preview_audio` 路由级用例（§9-P0.5 的"补 handler harness"这一半）。 |
| `tests/test_analysis_freshness.py` | 2.9 | 测试 | `_video`, `test_mtime_未变_不过期`, `test_mtime_变了但体积未变_仍算有效`, `test_mtime_与体积都变了_判定过期` …(+1) | 分析结果新鲜度判据的行为测试（mtime + 体积 双证据）。 |
| `tests/test_backend_entry_convergence_guard.py` | 2.3 | 测试 | `ROOT`, `_read`, `test_all_bootstrap_entries_forward_broadcast_seq`, `test_secondary_entries_have_broadcast_loop_recovery` …(+1) | 次引导入口收敛守卫（2026-09-24 修复，CLAUDE.md §2.2 / §11.3）。 |
| `tests/test_backend_shutdown.py` | 6.3 | 测试 | `ROOT`, `_isolate_real_shutdown`, `_noop_handler_shutdown`, `_noop_room_flush` …(+1) | 后端停机链（`LSCWebSocketBackend.stop()`）回归测试。 |
| `tests/test_batch_record_finish_guard.py` | 2.4 | 测试 | `manager`, `test_job_clears_handle_and_survives_full_mailbox`, `_boom`, `test_job_emits_finished_with_partial_count_on_enqueue_failure` …(+1) | 批量录制收尾守卫（2026-09-24 修复）。 |
| `tests/test_broadcast_seq.py` | 5.7 | 测试 | `ROOT`, `_Bus`, `subscribe`, `_Orchestrator` …(+1) | 广播通道序列号与唤醒失败的回归测试（2026-09-18 债务收口 §11.17）。 |
| `tests/test_broadcast_seq_forwarding.py` | 4.8 | 测试 | `ROOT`, `_Bus`, `subscribe`, `_Orchestrator` …(+1) | 广播 _seq 从队列入队到前端收帧的端到端链路守卫（CLAUDE.md §2.1，2026-09-24 修复）。 |
| `tests/test_coerce.py` | 3.4 | 测试 | `test_try_float_accepts_numbers_and_numeric_strings`, `test_try_float_returns_none_for_unusable_values`, `test_try_float_rejects_non_finite`, `test_try_float_rejects_bool` …(+1) | `lsc/utils/coerce.py` 的取标量口径测试。 |
| `tests/test_continuous_analysis_capability_gate.py` | 4.0 | 测试 | `_FakeServer`, `__init__`, `on`, `_deco` …(+1) | 持续分析能力闸：不被支持的 (game, mode) 组合必须在启动侧就被拒（§11.18-D）。 |
| `tests/test_export_alignment_units.py` | 10.5 | 测试 | `ROOT`, `LATENCY`, `test_ai_highlight_passes_through_without_offset`, `test_full_wall_clock_snapshot_is_exact_and_offset_anchored` …(+1) | 导出/对齐两个 handler 模块的纯逻辑单元覆盖（2026-09-18 债务收口 §9-P0.5）。 |
| `tests/test_export_route_flow.py` | 12.6 | 测试 | `ROOT`, `_FakeServer`, `__init__`, `on` …(+1) | `export_clip` / `cancel_export` / `get_export_job_status` 的路由级用例（§9-P0.5）。 |
| `tests/test_hot_failover_merge_deferred_stop.py` | 5.1 | 测试 | `_patch_config`, `_FakeRegistry`, `__init__`, `get` …(+1) | hot-failover 合并期间停止录制的 deferred 守卫（CLAUDE.md §11.3，2026-09-24 修复）。 |
| `tests/test_jev_judge.py` | 34.4 | 测试 | `REAL_KEY`, `ROOM_HANDLER_SRC`, `GOOD_BODY`, `_outcome` …(+1) | Jev 高光判决接入层测试（lsc/analyzer/jev_judge.py + room_handler 的 shadow/live 接线）。 |
| `tests/test_mixed_recording_ownership_guard.py` | 3.7 | 测试 | `_patch_config`, `_PreviewOnlyIngest`, `__init__`, `_FakeRegistry` …(+1) | 混合态录制归属守卫（2026-09-24 修复，P1-5）。 |
| `tests/test_parser_stale_classification.py` | 2.4 | 测试 | `_RaisingAdapter`, `__init__`, `can_handle`, `parse` …(+1) | 解析失败分类：平台改版（parser_stale）不得再伪装成网络故障（parse_failed）。 |
| `tests/test_persistence_save_seq_guard.py` | 5.1 | 测试 | `ROOT`, `_seq`, `_room`, `_read_ids` …(+1) | rooms.json 持久化竞态守卫（2026-09-24 修复）。 |
| `tests/test_platform_observability.py` | 13.8 | 测试 | `ROOT`, `_Provider`, `__init__`, `invalidate` …(+1) | P2-2 平台失效可观测性（2026-09-18 债务收口）。 |
| `tests/test_process_spawn_budget.py` | 9.2 | 测试 | `_reset_gate`, `test_并发占用不超过名额`, `worker`, `test_拿不到名额就抛并计数` …(+1) | 全局派生闸（CLAUDE.md §7.4）的行为测试。 |
| `tests/test_recording_disk_floor_guard.py` | 4.6 | 测试 | `_patch_config`, `_FakeSharedIngest`, `_patch_registry`, `_low_disk` …(+1) | 磁盘满保护守卫（CLAUDE.md §5.1，2026-09-24 审查修复）。 |
| `tests/test_recording_output_validation_guard.py` | 4.6 | 测试 | `_patch_config`, `_FastIngest`, `__init__`, `stop_recording_sink` …(+1) | 三层验证接入生产停止路径的守卫（CLAUDE.md §5.1.1，2026-09-24 修复）。 |
| `tests/test_room_registry_cleanup.py` | 4.3 | 测试 | `ROOT`, `_Manager`, `remove_room`, `_seed_and_remove` …(+1) | 删房时按房间登记的账本必须被注销（此前只增不减）。 |
| `tests/test_rooms_throttle.py` | 3.0 | 测试 | `ROOT`, `clock`, `test_first_update_goes_out_immediately`, `test_update_inside_the_window_is_merged_not_dropped` …(+1) | `_RoomsThrottle` 的行为契约（§7.4 的"与 drain coalesce 双实现"一条经核是误判）。 |
| `tests/test_save_rooms_validation.py` | 7.5 | 测试 | `ROOT`, `_make_handler`, `dispatch`, `_room` …(+1) | `save_rooms`（rooms.json 第二条写入口）的校验行为测试。 |
| `tests/test_spawn_budget_pairing.py` | 4.0 | 测试 | `_live_by_category`, `test_shared_ingest_launch_registers_and_teardown_unregisters`, `_FakePopen`, `__init__` …(+1) | 中央派生账 note_child/forget_child 配对守卫（CLAUDE.md §7.4，2026-09-24 修复）。 |
| `tests/test_stop_recording_false_failure.py` | 4.9 | 测试 | `_StatusSeq`, `__init__`, `__call__`, `test_timeout_recheck_waits_until_recording_actually_stops` …(+1) | 停录"假失败"回归：跨线程调用超时不等于停止失败。 |
| `tests/test_tick_per_room_isolation_guard.py` | 3.2 | 测试 | `_PoisonIngest`, `recording_active`, `_RoutingRegistry`, `__init__` …(+1) | tick 逐房隔离守卫（CLAUDE.md §7.3.3，2026-09-24 修复）。 |

## 7. F 类 — 本地产物（不应提交，但未被 `.gitignore` 完全覆盖）

| 文件 | 大小(KB) |
| :--- | ---: |
| `.playwright-cli/page-2026-09-22T14-46-47-043Z.yml` | 0.7 |
| `.playwright-cli/page-2026-09-22T14-48-24-068Z.yml` | 0.7 |
| `.playwright-cli/page-2026-09-22T14-50-05-959Z.yml` | 0.7 |
| `.playwright-cli/page-2026-09-22T14-50-42-307Z.yml` | 5.5 |
| `.playwright-cli/page-2026-09-22T14-51-51-535Z.yml` | 6.1 |
| `.playwright-cli/page-2026-09-22T14-52-25-251Z.yml` | 6.1 |
| `.playwright-cli/page-2026-09-22T14-53-39-017Z.yml` | 5.8 |
| `.playwright-cli/page-2026-09-22T14-53-41-687Z.yml` | 5.8 |
| `.playwright-cli/page-2026-09-22T14-53-53-526Z.yml` | 30.5 |
| `.playwright-cli/page-2026-09-22T14-54-25-483Z.yml` | 30.7 |
| `.playwright-cli/page-2026-09-22T14-56-13-501Z.yml` | 5.3 |
| `.playwright-cli/page-2026-09-22T14-57-07-887Z.yml` | 5.8 |
| `.qoder_tmp_survey.md` | 22.4 |
| `output/playwright/.playwright-cli/page-2026-09-22T14-49-22-601Z.yml` | 0.7 |
| `output/playwright/vite-pid.txt` | 0.0 |

`.gitignore` 自己的注释写着「never commit; a single `git add -A` would otherwise pull ~23MB of them in」，但实际只命中了 `*.log` / `*.png`：`*.yml` 页面抓取、`vite-pid.txt` **仍会被 `git add -A` 带走** ⇒ 与项目自身约束矛盾，需补规则。

## 8. G 类 — GitHub 上存在、本地已删或移走（反向差异）

- `lsc/analyzer/sound_detector.py`
- `lsc/analyzer/valorant_observer_scoreboard.py`
- `lsc/core/services/export_service.py`
- `lsc/core/services/frame_capture.py`
- `lsc/core/services/mse_sender.py`
- `lsc/recorder/segmented.py`
- `lsc/utils/error_stats.py`
- `python-backend/__init__.py`
- `tests/test_cancellable_ffmpeg.py`
- `tests/test_core_export_service.py`
- `tests/test_mse_sender.py`
- `tests/test_segmented_recorder.py`
- `tests/test_valorant_observer_scoreboard.py`

## 9. 文档 ↔ 代码 错位清单（`CLAUDE.md` 点名的实现，GitHub 上到底有没有）

| 文件 | GitHub `main` | 本机状态 | 一句话说明 |
| :--- | :--- | :--- | :--- |
| `lsc/platforms/diagnostics.py` | **无** | 未跟踪（**仅本机磁盘**） | 平台层降级事件计数（2026-09-18 债务收口 P2-2）。 |
| `lsc/utils/coerce.py` | **无** | 未跟踪（**仅本机磁盘**） | 异构字典（ffprobe JSON / 前端 payload / sidecar / settings）取标量的唯一口径。 |
| `lsc/analyzer/jev_judge.py` | **无** | 未跟踪（**仅本机磁盘**） | Jev（TypeSafe SystemOne）高光判决接入层。 |
| `python-backend/handlers/credential_handlers.py` | **无** | 未跟踪（**仅本机磁盘**） | 平台凭证 WebSocket 处理器（2026-09-18 拆分自 `room_handler.py`）。 |
| `python-backend/handlers/selfcheck_handlers.py` | **无** | 未跟踪（**仅本机磁盘**） | 平台自检 WS 路由（2026-09-18 债务收口 P2-2c）。 |
| `lsc-electron/src/utils/previewRecovery.ts` | **无** | 未跟踪（**仅本机磁盘**） | 预览自愈（后端 sink 重建）的房间级预算。 |
| `lsc-electron/src/utils/previewAlignment.ts` | **无** | 未跟踪（**仅本机磁盘**） | 一键对齐结果落到预览播放头的唯一入口。 |
| `lsc-electron/src/utils/previewLifecycle.ts` | **无** | 未跟踪（**仅本机磁盘**） | import type { I18nT } from '@/i18n' |
| `lsc-electron/src/utils/clipExportFlow.ts` | **无** | 未跟踪（**仅本机磁盘**） | import type { ClipSegment } from '@/types' |
| `lsc-electron/src/utils/clipConfirmPolicy.ts` | **无** | 未跟踪（**仅本机磁盘**） | import type { ClipSegment } from '@/types' |
| `lsc-electron/src/utils/manualClip.ts` | **无** | 未跟踪（**仅本机磁盘**） | import type { ClipSegment } from '@/types' |
| `lsc-electron/src/utils/mseCacheStore.ts` | **无** | 未跟踪（**仅本机磁盘**） | MSE init / media 段缓存的**有界存储**（2026-09-19 从 `hooks/useWebSocket.ts` 的模块级 |
| `lsc-electron/src/pages/Settings/CookieStatusLine.tsx` | **无** | 未跟踪（**仅本机磁盘**） | import { Tooltip } from 'antd' |
| `lsc-electron/src/pages/Settings/PlatformSelfCheck.tsx` | **无** | 未跟踪（**仅本机磁盘**） | import { useEffect, useState } from 'react' |
| `lsc-electron/electron/diagnosticPack.ts` | **无** | 已提交（本地，未推送） | 诊断包纯函数：脱敏 + 打 zip。 |
| `docs/reports/debt-audit-20260918.md` | **无** | 未跟踪（**仅本机磁盘**） | 结构性债务与未收口缺陷审计（2026-09-18） |
| `docs/reports/jev-integration.md` | **无** | 未跟踪（**仅本机磁盘**） | Jev 高光判决接入报告（feat/jev-highlight-judge） |
| `docs/reports/jev-baseline-audit.md` | **无** | 未跟踪（**仅本机磁盘**） | Jev 接入前基线摸底审计（只读） |
| `docs/reports/model-backbone-yolo-feasibility-20260919.md` | **无** | 未跟踪（**仅本机磁盘**） | 把两个视觉模型换成 YOLO 的可行性（内存 / 资源维度） |

对应无误的部分同样核对过：`python-backend/handlers/timeline_handlers.py`、`alignment_handlers.py` **GitHub 上都在**；`python-backend/message_bridge.py`、`lsc/gui/` **两边都已不存在** ⇒ `CLAUDE.md` §2.1 / §7.1 关于「已删除，仅历史参考」的表述与仓库一致。

## 10. Git 之外还不对应的东西

| 项 | 本机 | GitHub | 影响 |
| :--- | :--- | :--- | :--- |
| `lsc-electron/package.json` version | `1.0.17` | `1.0.17` | 版本号一致；但工作区 `CHANGELOG.md` 顶部已改写为 `v1.6.0（计划中）` ⇒ 版本与 changelog 口径错位，下次发版需同步 bump |
| `git stash` | 5 条（1 条指向已不存在的分支 `feat/simplify-continuous-analysis-ocr`） | 无此概念 | stash 永不上 GitHub，需自行判断是否仍有效 |
| `settings.json` / `data/rooms.json` / `recording_history.json` | 本机运行数据 | 仓库不跟踪 | 正常，属运行时状态，不算不对应 |
| `~/.lsc/cookies`（DPAPI 加密） | 本机 | 不跟踪 | 正确，含凭据不应上传 |

## 11. 推送前必须知道的风险点

1. 共 **214 个文件从未被任何提交记录**（B+C+D+E），GitHub 无法回滚到这些状态，磁盘故障即永久丢失。
2. **C 类模块级删除同时移走了对应测试**（`test_cancellable_ffmpeg.py`、`test_core_export_service.py`、`test_mse_sender.py`、`test_valorant_observer_scoreboard.py`）。提交前确认是有意清理而非误删 —— `CLAUDE.md` §5.2 / §8.5 的表述支持有意删除。
3. **`full` 与 `LSC` 两条历史互不相干**，`v1.5.0` 指向 `full` 线 ⇒ CHANGELOG 的发版号规则（「发布线以 1 开头 / 禁止同线倒退」）跨线不自洽。
4. **`.gitignore` 需补规则**，否则 `git add -A` 会把 F 类产物提交进仓库。
5. **`pre-commit` 钩子已安装**（ruff、trailing-whitespace、end-of-file-fixer、check-added-large-files、debug-statements 等），大批量提交会触发改写与告警；失败时重新 `git add` 再提交，不要用 `--no-verify` 绕过。
6. `tests/test_frontend_stability_guards.py` 一次性 −1865 行（拆到多个 `*.behavior.test.ts` + 新守卫），属重构而非回归；建议独立成一次提交，便于回滚定位。

## 12. 复现本清单的命令

```powershell
git fetch --all --prune
git log --oneline origin/main..HEAD                    # A 类的提交
git diff --numstat -M origin/main HEAD                 # A 类逐文件增删
git diff --numstat -M HEAD                             # B/C 类（工作区）
git diff --cached --numstat -M HEAD                    # D 类（暂存区）
git ls-files --others --exclude-standard               # E/F 类（未跟踪）
git diff --name-status -M origin/main | Select-String '^D'   # G 类
git for-each-ref refs/tags ; git ls-remote --tags origin    # 标签比对
git merge-base full/master main                        # 验证两条历史不相干（exit=1）
```
