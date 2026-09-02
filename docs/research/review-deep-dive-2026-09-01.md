# 六项评审意见深调研报告（2026-09-01）

> 调研分支：`research/deep-dive-review-6items`
> 基线：`9a8792f`（v1.0.11 文档统一提交）
> 目标：逐条核验「对上的事实」、①~⑥ 技术判断，并给出证据、差异与建议。

---

## 0. 结论摘要

- 原评审的大部分事实核对成立，且证据比评审写得更充分。
- 需要修正的两处重要事实：
  1. 「虎牙还有硬闸」在**当前代码里并未生效**：`_V2_PLATFORM_HARD_BLOCKLIST` 为空集合，测试也明确允许虎牙经 allowlist 进 V2。
  2. `room_handler.py` 的覆盖死路由**不止 8485/8671/8802**，实际共 17 条重复路由，域拆分已经完成，下一步应删死体、抽 MSE 预览，而不是再拆一次域。
- 工作区确为多件独立事项叠放，不是单一功能；`recording_layout.py` 与两份 layout 测试必须随目录约定一起提交，否则 diff 断裂。
- 测试验证：相关 Python 测试 346 通过、1 失败（该失败在本调研基线已存在，与工作区改动无关）；前端 vitest 4 通过；`tsc --noEmit` 通过。

---

## 1. 事实核对

| 评审原文 | 核验结果 | 证据 |
| :--- | :--- | :--- |
| v1.0.11（2026-09-01），稳定性 + MSIX | 对 | `CHANGELOG.md` 首节 `v1.0.11 (2026-09-01)`；`lsc-electron/package.json` version `1.0.11`；`README.md` 有 `1.0.11` 与 `2026-09-01`。 |
| 未提交约 +700/−180 | 接近，精确为 **27 文件、+702/−182** | `git diff --stat`：`27 files changed, 702 insertions(+), 182 deletions(-)`。 |
| 5 个 stash | 对 | `git stash list` 共 5 条；主题为 OCR 简化、临时文档、剪映合并、de-Qt/analyzer plugin、CS2 计划。 |
| `room_handler.py` 9051 行、约 73% | 行数对；占比精确为 **9051 / 12190 = 74.2%** | `python-backend/handlers/*.py` 合计 12190 行，`room_handler.py` 9051 行。 |
| WS `9876` vs `19876`、`output_dir` 两处默认、架构图还画着 `message_bridge.py` | 全部仍在 | 见 §3。 |
| V2 默认关、生产走 legacy，且带平台 allowlist | 代码默认对；生产入口通过 `main.py` 走 legacy | `lsc/config.py:217` 默认 `False`；`CLAUDE.md:191` 说明生产实际走 legacy `parse_stream`。本地 gitignored `python-backend/settings.json` 含 `shared_ingest_enabled: true`，属于本机覆盖，不影响仓库默认。 |
| 分析插件 spec + `test_analyzer_plugin_*.py` | 对 | `docs/spec-deqt-orchestrator-analyzer-plugin.md`；`tests/test_analyzer_plugin_contract.py`、`test_analyzer_plugin_parity.py`、`test_analyzer_registry.py`。 |
| 「35 个文件、改了 5 个测试」偏大 | 对，tracked 27；测试侧 7 个 pytest + 1 个前端测试，另有 2 个未跟踪 layout 测试 | `git diff --name-only` 27 个 tracked；pytest 修改 7 个：`test_analyzer_plugin_parity.py`、`test_continuous_analysis_guards.py`、`test_frontend_stability_guards.py`、`test_mse_streamer.py`、`test_recording_reconnect_tick.py`、`test_server.py`、`test_shared_ingest.py`；前端 `analysisProgress.test.ts`；未跟踪 `tests/test_recording_layout.py`、`tests/test_recording_layout_wiring.py`。`.mimosa/` 与 `宣传物料/` 是工具历史/宣传资产，不应计入功能改动。 |

---

## 2. ① 工作区：确为多件独立事项，不是单一功能

当前未提交改动可清晰拆成以下几组：

### 2.1 录制目录约定（必须连同未跟踪文件一起提交）

- 新模块：`lsc/core/recording_layout.py`（未跟踪）
- 目录语义：单房间 `{output}/{主播名}/`；对齐后 `{output}/{主播A}+{主播B}/{主播名}/`；停录后再把「录制中」文件改名为「开始至结束」并搬进组合目录。
- 引用点：
  - `lsc/core/orchestrator.py:29` 导入 `finalize_room_recording` / `recording_in_progress_path` / `room_recording_dir`
  - `python-backend/handlers/alignment_handlers.py:22` 导入 `bind_rooms_to_bundle`
  - `python-backend/handlers/export_handlers.py:22` 导入 `resolve_clip_output_dir`
  - `lsc/gui/pages/recording_controller.py:599` 使用 `recording_in_progress_path`
  - `lsc/core/session.py` 新增 `output_bundle_dir`
- 配套测试：`tests/test_recording_layout.py`、`tests/test_recording_layout_wiring.py`（均未跟踪）。
- 结论：若只提交 tracked diff 会留下对未跟踪模块的 import，属于断裂改动；必须先纳入 `recording_layout.py` 与两份测试。

### 2.2 持续分析首窗

- `lsc/analyzer/valorant_plugin.py`：
  - `_adaptive_catchup_cap` 无吞吐历史时从 `MAX_CATCHUP_SEC` 改为 `MIN_CATCHUP_SEC`。
  - `compute_valorant_scan_budget` 首窗（`last_analyzed<=0`）不再是 `scan_end=current_dur`，而是 `min(dur, catchup_cap)`，避免中途开分析一次吞掉已录全部时长。

### 2.3 稳定性

- WS 慢客户端：
  - `python-backend/server.py` 新增 `_SEND_TIMEOUT_SEC = 2.0`、`_SLOW_KICK_AFTER_SEC = 15.0`；单次 send 超时只记慢起点，持续 15s 才剔除，发送成功清除标记，硬异常立即剔除。
- 主动重连后台化：
  - `lsc/core/orchestrator.py::_do_proactive_reconnect` 将重启段投递 `self._worker_pool.submit(_restart_on_worker)`，避免阻塞编排线程。
- subprocess 强制 UTF-8：
  - `lsc/utils/process_launcher.py::hidden_run_kwargs` 对 text 模式注入 `encoding="utf-8", errors="replace"`。
  - `lsc/platforms/probe.py` 在 `subprocess.run(..., text=True)` 显式传 UTF-8。
- FFmpeg `-headers` 上限：
  - `lsc/platforms/base.py::headers_to_ffmpeg_input_args` 从 2048 改为 3800（低于 4096 留余量，避免误杀抖音/B 站完整 Cookie）。
- CLAUDE.md 已同步上述三条稳定性契约（§11.3 新增条目）。

### 2.4 MSE / 共享进样 / 前端分析进度文案

- `lsc/core/services/mse_streamer.py`：文件回看直接软解软编，新增 `resolve_mse_encode_attempts`，避免与直播录制抢 NVENC。
- `lsc/core/services/shared_ingest.py`：新增 `preview_input_bytes`、`preview_media_ready`，预览 sink 就绪必须以实际 init+media 产出为准；预览编码 `-threads 2` 限流；停滞诊断日志增强。
- `python-backend/handlers/room_handler.py`：`_shared_preview_reconnect_ready` 改为要求 `preview_media_ready`，重连等待窗最长 8s，避免零产出死循环；持续分析丢弃旧文件扫描结果/游标越界兜底。
- 前端：
  - `AnalysisProgress.tsx` + `utils/analysisProgress.ts` 展示「本窗 from–to」。
  - `Workbench/index.tsx` 录制回看启动去重（`recordingReviewInFlightRef`）。
  - `ControlBar.tsx` 两处 `!= null` 修正。

结论：这些是相互独立的主题，应拆提交；测绿不能作为「整包一次合入」的充分条件。

---

## 3. ② `room_handler` 拆分的真实状态：域拆分已完成，当前是「删死体 + 抽 MSE」

### 3.1 域拆分确实已做过

`python-backend/handlers/` 下已有：

- `recording_handlers.py`（3 条路由）
- `alignment_handlers.py`（2 条路由）
- `export_handlers.py`（3 条路由）
- `analysis_handlers.py`（10 条路由）
- `jianying_handlers.py`（2 条路由）
- `timeline_handlers.py`（3 条路由）

`room_handler.py` 末尾（约 8940 行起）依次调用 `register_*`，在**全部内联 `@server.on` 之后**注册。

### 3.2 `server.on` 是后注册覆盖

`python-backend/server.py:102`：`self.handlers[message_type] = fn`。

因此，凡在 `room_handler.py` 内联定义、随后又被子模块同名注册的路由，运行时**永不触发**。

### 3.3 实际覆盖死路由共 17 条（不止评审提到的 3 条）

自动比对 `@server.on('...')` 路由名，得到以下重复：

| 路由 | room_handler 旧体位置 | 子模块活路由位置 |
| :--- | :--- | :--- |
| `set_content_offset` | 4773 | alignment_handlers 87 |
| `align_preview_audio` | 4788 | alignment_handlers 102 |
| `start_recording` | 4077 | recording_handlers 62 |
| `stop_recording` | 4266 | recording_handlers 233 |
| `start_analysis` | 6266 | analysis_handlers 167 |
| `start_analysis_export` | 6376 | analysis_handlers 290 |
| `cancel_analysis` | 6520 | analysis_handlers 448 |
| `get_analysis_results` | 6533 | analysis_handlers 461 |
| `get_continuous_analysis_status` | 8733 | analysis_handlers 698 |
| `start_continuous_analysis` | **8485** | analysis_handlers 509 |
| `stop_continuous_analysis` | **8671** | analysis_handlers 646 |
| `begin_refine_clip` | 8772 | analysis_handlers 733 |
| `confirm_highlight_clip` | **8802** | analysis_handlers 759 |
| `cancel_refine_clip` | 8873 | analysis_handlers 824 |
| `cancel_export` | 5198 | export_handlers 767 |
| `get_export_job_status` | 5233 | export_handlers 800 |
| `repair_recording` | 9028 | recording_handlers 268（注：room_handler 中 repair_recording 在子模块注册之后仍存在，需单独确认是否也是覆盖死代码） |

其中评审点名的 `start_continuous_analysis` / `stop_continuous_analysis` / `confirm_highlight_clip` 当前分别位于 **8485 / 8671 / 8802**，与评审一致。

### 3.4 CLAUDE.md §11.5 的行号已过期

`CLAUDE.md:844` 仍写 `L8382/L8568`，实际为 8485/8671。需要同步更新。

### 3.5 MSE 预览块确实值得单独抽

`room_handler.py` 中：

- `@server.on('enable_preview')` 在 **5242**
- `_handle_mse_preview` 在 **5267**
- 下一条 `@server.on('request_mse_init')` 在 **6205**

即 MSE 预览主体约 **5242–6205**，约 960 行，是活路径中最大的连续块。

### 3.6 §11.5 的 8 项死代码不应绑在这次清理里

`CLAUDE.md §11.5` 列出的 `align_rooms` / `MseSender` / `ExportService` / `CancellableFFmpeg` / `ErrorStats` / `generic_plugin.scan_window` / `export_handlers._deferred_export_jobs` 等分别位于各自的包，并有测试引用，确实不应与 `room_handler` 清死路由混在一起。

---

## 4. ③ 文档不一致：确实存在，但成本/回报不同

### 4.1 架构图 / §1.1 / §1.2（优先修）

- `CLAUDE.md:18-25` 仍写「Qt 事件循环 (主线程) + WebSocket 服务器 (工作线程)」「Qt 槽调用」。
- `CLAUDE.md:44` 仍列 `message_bridge.py`：利用 Qt 信号槽。
- `CLAUDE.md:71`（§2.1 NOTE）才是现状：已迁移为 `RoomOrchestrator.call()` + `BroadcastHub`，`message_bridge.py` 已不存在。
- 另外 `docs/PROJECT_DESIGN.md`、`docs/ai-prompts/...` 也有同类过时描述。

### 4.2 WS 端口（应收敛到单一常量）

- `python-backend/main.py:172`：`LSCWebSocketServer(host="127.0.0.1", port=9876)`（生产入口）。
- `python-backend/server.py:82`：类默认 `port=19876`。
- `python-backend/server.py:418`：模块级 `server = LSCWebSocketServer()`（裸构造，走 19876）。
- `python-backend/server.py:483` 的独立入口 `main()` 使用全局 `server`，因此 `python server.py` 与 `python main.py` 端口不一致。
- `tests/test_server.py:112`：`assert srv.port == 19876`。
- `CLAUDE.md:90` 与 `CLAUDE.md:848` 已记录该不一致。

### 4.3 `output_dir`（不要当顺手活）

- settings/handler 层默认：`~/LSC/output`（`room_handler.py`、`export_handlers.py`、`recording_handlers.py` 多处）。
- `lsc/config.py:244`：`LscConfig` 默认 `~/LSC/recordings`。
- `CLAUDE.md:130` 已标注两处不一致。
- 本地 `python-backend/settings.json`（gitignored）已有显式 `output_dir`，说明老用户不受默认值影响；统一默认还需要决定 `output` 还是 `recordings` 胜出，并考虑迁移映射，属于产品决策。

---

## 5. ④ V2：灰度机存在，但「虎牙硬闸」当前未生效

### 5.1 灰度机制

`lsc/config.py`：

- `platform_pipeline_v2_enabled: bool = False`（全局总开关，默认关）
- `platform_pipeline_v2_allowlist`（平台 allowlist）
- `platform_pipeline_v2_room_allowlist` / `user_allowlist` / `account_allowlist` / `app_version_allowlist`（可选维度）
- `is_platform_pipeline_v2_enabled()`：先查硬闸，再查总开关，再查平台 allowlist，最后查各维度 allowlist。
- `is_platform_pipeline_component_enabled()`：子能力门控（`unified_resolver_v2`、`media_probe_v2`、`stream_lease_v2`、`ingest_supervisor_v2` 等）。
- `_shared_ingest_v2_enabled()`（`room_handler.py:1349`）把 shared ingest 与 V2 allowlist/硬闸绑在一起。

### 5.2 重要差异：「虎牙硬闸」当前是空集合

- `lsc/config.py:329`：`_V2_PLATFORM_HARD_BLOCKLIST: frozenset[str] = frozenset()`。
- `tests/test_huya_ingest_as_probe.py` 明确测试：`platform_pipeline_v2_enabled=True` + allowlist `["huya"]` 时，`is_platform_pipeline_v2_enabled("huya") is True`。
- 因此代码里有硬闸**机制**，但**当前没有把虎牙列入硬闸**。文档/计划里曾出现过 `frozenset({"huya"})` 的阶段 0，但当前工作区不是这个状态。

结论：评审说「虎牙还有硬闸」需要修正为「硬闸机制还在，但当前列表为空；如果产品仍要虎牙走 legacy，需要重新把 `huya` 加回 `_V2_PLATFORM_HARD_BLOCKLIST`」。

### 5.3 灰度 vs 二选一

按平台继续灰度（第三种选项）是代码结构已经支持的：
- 全局开关可一键 kill；
- allowlist 可按平台放开/收回；
- room/user/version 可再收窄；
- 硬闸可在必要时强制平台走 legacy。

这与「全迁完删 legacy」或「整包砍 V2」不同，代码现状就是为可回滚灰度设计的。

---

## 6. ⑤ 分析器泛化：Valorant 已是插件，现状比评审说的更进一步

- `lsc/analyzer/registry.py`：
  - `get(game)` / `register(plugin)` / `list_plugins()` / `default()`。
  - `_ensure_builtins()` 注册 `GenericAnalyzerPlugin` 与 `ValorantAnalyzerPlugin`。
- `lsc/analyzer/valorant_plugin.py:87`：`class ValorantAnalyzerPlugin`，`game="valorant"`，实现 `capabilities()` / `plan_scan_window()` / `scan_window()`。
- `lsc/analyzer/generic_plugin.py`：
  - `scan_window()` 确实恒返回 `[]`（只更新 `last_analyzed`），与 §11.5 描述一致。
- `room_handler.py` 仍有大量 Valorant 辅助函数（`_valorant_round_key`、`_merge_round_windows`、`_is_ocr_round`、`_continuous_valorant_scan_budget` 等），部分函数已转调 `valorant_plugin.py`（如 `compute_valorant_scan_budget`），但未完全下沉。
- stash 中确有 CS2 计划（`stash@{4}: Add CS2 OOB and rule-editor validation implementation plan`）。

结论：第二梯队方向确实是「更多游戏 + UI 选插件 + 把 room_handler 剩余 Valorant 辅助函数搬进插件」，而不是把 Valorant 从零改成插件。

---

## 7. ⑥ 共享进样：技术图景已部分更新，设置表仍过时

- 默认 `False`：`lsc/config.py:217` 附近 `shared_ingest_enabled: bool = False`；`CLAUDE.md` 设置表也写默认 `False`。
- §7.3.2（`CLAUDE.md:347-370`）已经是现状描述：
  - 不是「单个 FFmpeg 双输出」；
  - 是「单个远端上游 FFmpeg + 独立录制 sink + 独立预览 sink」；
  - 录制 sink 故障不停预览，预览 sink 故障不停录制。
- §3.2 设置表（`CLAUDE.md:143`）仍写「单 FFmpeg 进程同时输出录制和预览」，与 §7.3.2 冲突，属于需要修的过时说法。
- `docs/ai-prompts/LSC-full-system-generation-prompt.md:90,503` 也仍是旧「单 FFmpeg 双输出」描述。
- 共享进样与 V2/故障隔离耦合：
  - `room_handler._shared_ingest_v2_enabled()` 同时看 `shared_ingest_enabled`、`is_platform_v2_hard_blocked`、`is_platform_pipeline_component_enabled("ingest_supervisor_v2")`。
  - `orchestrator._start_shared_recording_if_enabled()` 也要求 `shared_ingest_enabled` 或 `use_v2_ingest` 之一成立。
- 本地 gitignored `python-backend/settings.json` 当前 `shared_ingest_enabled: true`，这只是本机覆盖，不是仓库默认。

结论：默认关不能简单归结为「信心不足」；它与 V2 allowlist、硬闸、故障隔离绑在一起。补集成测试后默认打开确实与 ④ 是同一个灰度问题，不应作为独立开关单独推进。

---

## 8. 测试验证（本调研时点）

| 检查 | 结果 |
| :--- | :--- |
| 相关 Python pytest | **346 passed, 1 failed**（`test_frontend_stability_guards.py::test_timeline_1x_zero_and_dvr_lookback_contract`） |
| 该失败是否由工作区引起 | 否。失败断言 `"zoom <= 1 && input.followLive && !input.scrubbing"` 在 HEAD 的 `timelineWindow.ts` 同样不存在；该测试在 HEAD 已处于过期状态，与本次工作区改动无关。 |
| 前端 vitest（analysisProgress.test.ts） | **4 passed** |
| TypeScript | `tsc --noEmit` **exit 0** |

---

## 9. 建议顺序（与评审一致，附证据）

1. **按主题拆当前工作区**：稳定性（WS/重连/UTF-8/headers）→ 录制目录（含 `recording_layout.py` + 两份 layout 测试）→ 分析首窗/前端进度。测绿后按主题提交，不要整包合入。
2. **改 CLAUDE.md §1.1/§1.2、§11.5 行号、§3.2 共享进样描述**，让权威文档与 `RoomOrchestrator` + `BroadcastHub` + `SharedRoomIngest` 对齐。
3. **只删 `room_handler` 里已被覆盖的旧路由体**（17 条），不要再次按域大拆；MSE 预览块（5242–6205）作为独立抽取候选。
4. **V2 / 共享进样默认开 / 多游戏插件等用户拍板**；其中共享进样与 V2 是同一个灰度问题。若产品仍要求虎牙走 legacy，需把 `huya` 加回 `_V2_PLATFORM_HARD_BLOCKLIST` 并补测试。

---

*报告完*