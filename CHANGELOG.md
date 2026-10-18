# LSC 直播切片系统 — 更新说明

## v1.6.0 (计划中，2026-09-18 起在本分支累积)

> **编号更正（2026-09-19）**：本节原先记作 `v1.0.18`。用户定下"发布线以 1 开头"后回查标签，
> 发现 1.x 线上**已存在 `v1.5.0`（2026-07-02）**（审计报告原先的标签全集漏了它和 `v1.0.0`），
> ⇒ `v1.0.18` 在**同线内**也是倒退，永远发不出去。本分支的下一次发版号因此定为 **v1.6.0**；
> CI 的 Release integrity 步骤现在会同时卡住：跨线发版、同线倒退、以及
> `package.json` / `pyproject.toml` / `package-lock.json` 三处版本号不同步。

### 新增（单实例锁：多开不再产生多份后端）

- `electron/main.ts` 在 ready 之前取 `app.requestSingleInstanceLock()`，取不到锁的实例**不初始化任何资源
  （窗口 / 托盘 / 依赖检查 / 后端子进程）直接退出** —— 拦截点放在 ready 回调最前面，避免第二个实例仍然
  `spawnBackend()` 抢 9876（及 19877… 回退端口）并各自持有一份房间账本，互相覆盖 `settings.json` / `rooms.json`
  （`docs/FEATURE-INVENTORY.md` §17 列为 P1 缺口，本次关闭）。
- 第二个实例启动改为**聚焦已有窗口**：`second-instance` 事件 `restore() + show() + focus()`，最小化到托盘时
  也能被唤到前台。

### 修复（预览自救：播放器耗尽后自动重开后端 sink）

现场（2026-09-15 长预览冻结）只能靠用户手动关/开预览恢复：前端播放器把「重建 MediaSource」预算也用尽后直接
终态报错，而它**发不出任何「强制重启后端预览 sink」的请求** —— 旧 `enable_preview` 在已推流时只回一句「已在推流」
（不重建 sink）。三层接上（CLAUDE.md §7.2.2 遗留项）：

- **后端接受 `restart`**：`enable_preview {..., restart: true}`（仅 `enabled=true` 时生效）→
  `_handle_mse_preview(force_restart=True)` 重建 sink 并换 `preview_epoch_id`，前端据 epoch 变化重建播放器，
  不需要新协议消息。
- **播放器有界地问一次**：新增 `MsePlayer._trySinkRestart()`，插在**硬重建预算用尽之后**、两条终态报错
  （stall 预算耗尽 / 强制 seek 预算耗尽）之前；文件回看通道不问（没有后端 sink）。
- **UI 层持房间级预算**：新增 `src/utils/previewRecovery.ts`（10 分钟窗口内最多 2 次）。播放器实例会随新 epoch
  重建而重置，只按实例计数会让「重建后再次卡死」无限重启后端 FFmpeg；预算按房间记才收得住。未连接后端
  （`send` 返回 false）视为未发出，不消耗预算。
- **语义**：被拒 / 未接线 / 后端重建失败时保持原口径报错（不承诺自动恢复）；重建成功的话新 epoch 会在几秒内
  重建播放器并自动清掉错误遮罩 —— 「先如实报错、再自愈」，不做静默挂起。
- **守卫**：`tests/test_frontend_stability_guards.py::test_preview_sink_restart_wired_end_to_end`；夹具
  `mediaSourcePlayer.test.ts`（+6：接单 / 一次性 / 预算拒绝 / 未接线 / 文件通道 / 回调抛异常）、
  `src/utils/previewRecovery.test.ts`（+6）。

### 修复（预览画质双基线：回退 CRF 同源）

- 独立模式 `MseStreamer` 的软编回退 CRF 硬编码 `28`，共享进样回退取 `shared_ingest_preview_crf`（默认 `23`）
  ⇒ 同一路直播在两种预览模式下的回退画质不一致。现统一为**同一来源**（`MseStreamer._fallback_crf` 读同一个
  配置键）。
- 澄清口径：正常路径的 CRF 由 `room_handler._PREVIEW_QUALITY_PRESETS` 按预览画质档位给出（原画 20 / 高清 26 /
  标清 30 / 流畅 32），配置键只在调用方未传值时兜底（CLAUDE.md §3.2 已改写）。
- **守卫**：`tests/test_frontend_stability_guards.py::test_preview_fallback_crf_has_single_source`。

### 修复（P3-2：底部时间线不再退化成 0-1s 空轴）

不勾选任何房间卡片时，底部时间线用 `room = selectedRoom`（undefined）驱动：轴长恒为 `max(contentEnd,1)=1s`，
点击/拖动落在空轴上什么也不发生（远 seek 静默无效，现场曾被误判为缺陷）。现在时间线**始终绑定到一个具体房间**：

- **自动跟随**：未勾选时按 `pickAutoBindRoomId` 选一个绑定对象 —— 候选唯一才绑（优先「录制中的房间」，
  没有录制房时取「已开预览且已连接」的房间）；**多个候选或无候选一律不猜**（猜错会让轴、切片色带与 seek
  落到用户没在看的那一路）。绑定后录制轴 / 预览轴、切片色带与走带按钮都回到真实轴。
- **必须明示、不留静默态**：自动跟随时控制栏显示「未勾选房间：时间线自动跟随「{name}」，勾选卡片可切换」；
  无候选可绑时时间线**置灰且不可交互**（`opacity .45 + pointer-events:none`）并提示「勾选任一房间以启用回放轴」。
- **seek 与轴同源**：`resolveSeekTargets()` 在无勾选时优先落在自动绑定的房间（此前会退到 `rooms[0]`，
  可能不是正在显示的那一路）。
- **守卫**：`tests/test_frontend_stability_guards.py::test_timeline_binds_a_room_when_nothing_is_checked`；
  夹具 `lsc-electron/src/utils/timelineCoords.test.ts`（+7）。
- 只影响时间线显示 / 寻道 / 提示，**不改变标记、切片与导出口径**。

### 文档

- **删除死代码**：`lsc/editor/audio_aligner.py` 的 `align_rooms` / `extract_audio_pcm`（从录制文件用 FFmpeg 提取音频的对齐路径）
  连同只服务该路径的 `_is_network_source`、`AUDIO_DURATION`、`_SEEK_BUFFER`、`_MAX_EXTRACT_WORKERS` 与 4 个已无用的 import 一并移除，
  `tests/test_audio_aligner.py` 同步删除 `TestExtractAudioPcm` / `TestAlignRooms` 两个测试类（6 条用例）。全库零生产引用（当时唯一调用方是这两个测试类），
  运行时对齐只走预览音频 `compute_offset` / `align_audio_map`。模块 docstring 保留「已删除」说明，防止后续重复引入。
- `CLAUDE.md` §7.2.2：`enable_preview restart` 遗留项标记为已修并写明三层接线与语义；§3.2 预览 CRF 口径改写。
- `docs/FEATURE-INVENTORY.md`：单实例锁从「缺口」移入已实现（缺口 4 → 3）。

### 债务收口（2026-09-18 结构性债务审计的执行批次，见 `docs/reports/debt-audit-20260918.md`）

**质量门禁从"存在但没在执行"变为真执行**

- **vitest 进 CI**：新增 `frontend-tests` job（ubuntu + windows 矩阵，跑 `npm test`），并加入 `release` 的 `needs`。此前 CI 只有 `npm ci` + `tsc`，27 个测试文件 / 274 条用例**从不执行**。
- **修掉一条红了两个版本的守卫**：`localFileMseSource.test.ts` 的「定位窗口被 IPC 读上限截断」（守 2026-09-15 真机事故）自 v1.0.17 起即失败 —— 它与 `readSeekWindow` 同批进库却从未验证过。根因不是逻辑缺陷而是**用墙钟预算等一件耗时取决于调度的工作**：远定位耗时 = 扫描区间 ÷（每轮一次 IPC 读 × `pollIntervalMs`），2000/2500/4000ms 三个上限都是空闲机实测值，与全量 pytest 并发时必红（复现：空闲 4/4 绿、并发必红）。改为 `waitUntil(条件, LOCATE_CEILING_MS)` + 各用例显式 `timeout`（vitest 默认 5s 本就压不住这些用例）。**未删除、未放宽任何断言。**
- **`ruff check lsc/ python-backend/` 归零**（15 → 0）：其中 6 处位于本次未改动文件，即**已提交的 HEAD 上这条 CI 阻塞项本就是红的**。`SIM114/SIM117` 手工合并而非改变语义；`MseStreamer` 的 F401 通过删除 `room_handler.py` 内两处函数级重复 import 解决（同时修掉两处 §11.4 违规）。
- **pre-commit 钩子真正安装**：`.git/hooks/pre-commit` 此前从未存在（配置 pin 的 ruff v0.5.0 比本地/CI 落后 10 个 minor 版本）。现 ruff 在本地、钩子、CI 三处钉为同一版本 `0.15.16`（CI 原本 unpinned，上游发新版就能把 main 弄红）；`ruff-format` 从钩子移除并注明原因（实测会重排 99/109 个文件 = 每次提交顺带污染无关 diff），要引入须先单独提一次全量格式化。
- **覆盖率口径修正**：CI 原本只 `--cov=lsc`（报 73%），而 `python-backend` 完全不被测量 —— 那里装着最关键的并发与导出编排。合并测量后真实基线 **62%**（`room_handler.py` 39%、`alignment_handlers` 15%、`export_handlers` 16%、`dependency_manager` 24%、6 个文件 0%）。已加 `--cov=python-backend` 与 `--cov-fail-under=55` 防退步地板。
- mypy 仍是 advisory，但把注释里冻结的「87 errors in 22 files」更正为实测「112 errors in 27 files」，并去掉指向不存在 issue 的假引用。
- `.gitignore` 补齐 `.mypy_cache/`(19MB)、`.ruff_cache/`、`artifacts/`(3.8MB)、`*.orig`、`NVIDIA Corporation/`、`~/` —— 一次 `git add -A` 原本会提交约 23MB 缓存。

**"静默失效"类缺陷**

- `tests/test_broadcast_export_authority_lifetime.py` 5 条 `pytest.skip("C1/C2/C3/C6 未实现")` 是**陈旧死分支**（被探测的 5 个符号全部存在，实测 19 passed / 0 skipped）。真正的风险是反的：`getattr(mod, name, None) is None` 式兜底会让**符号重命名把测试静默变成 skip 而非失败**。已全部改为直接属性访问。
- `room_handler.py:6189` 的「死代码：本 handler 已被 alignment_handlers 后注册覆盖，勿改」注释**三个事实全错**（它指向的是最活的 `check_dependencies` 唯一实现；`align_preview_audio` 早就不在本文件；`server.on` 对重复注册抛 `ValueError` 而非覆盖），会把维护者引到错误的文件，已改写。
- `room_handler.py:638` `_onnx_inference_semaphore = _analysis_thread_semaphore` 是**同一对象取两个名字**，读起来像两份独立预算。已删别名，acquire 点直接用真名并注明"刻意复用同一槽位"。
- `persistence.flush_pending_room_saves()` 定义了却**无任何生产调用点** —— `schedule_save_rooms` 有 1s 去抖，关停前 1s 内的房间增删会静默丢失（rooms.json 属 P0 持久化）。已接入 `main.py stop()` 末段（放在 handler/manager 关停之后，此时不会再有 schedule 投递），失败记 ERROR。
- `dependency_manager.py` 的 `assert proc.stdout is not None` 违反本项目自己的 §11.4（`python -O` 会整条移除 assert），改为显式判断 + `_emit_error` + `return False`。
- 预览冻结的可诊断性：健康快照原本只在 `_handleError` 构造一次，卡死/`updateend` 观测全走受 `_debug` 压制的 `_log` ⇒ 生产日志只有死亡瞬间、没有致死曲线，`CLAUDE.md` 宣称的"下一次定位依据"当时并不成立。新增 `_warnFrozenProgression()`：每 5s 至多一条 WARN，带**累计**冻结时长（旧代码每轮把 `_lastStallTime` 归零，增长曲线从未被记录）、`mode=render-frozen|data-starved` 分型 + 完整健康快照；配 1 条真行为测试。

**死代码清理（净减约 1,565 行实现 + 约 1,000 行只测死代码的用例）**

删除 `mse_sender.py`(96)、`export_service.py`(449)、`cancellable_ffmpeg.py` 的 `CancellableFFmpeg`(93，**保留活异常类型 `FFmpegCancelled`**)、`error_stats.py`(106)、`sound_detector.py`(299)，以及 §11.5 清单**漏收**的 `frame_capture.py`(262) 与 `valorant_observer_scoreboard.py`(224)；`lsc/core/__init__.py` 收敛为纯包声明（其 re-export 的 8 个名字全仓零命中）。
⚠️ 同时纠正清单里一处**错误**：`scene_analysis.py` 被标为"遗留分支"，实为**活代码**（14 处生产引用，含 `room_handler.py:1428/3807/3811`），**未删除** —— 它是覆盖率问题（实测 6%）而非死活问题。

**全局 FFmpeg 派生闸（新 `CLAUDE.md` §7.4 的代码落地）**

在 `lsc/utils/process_launcher.py` 建出此前完全缺失的中央记账点：`spawn_slot(category)`
（无名额抛 `SpawnBudgetExhausted`）、`note_child`/`forget_child`（存活分类账）、`budget_snapshot()`
（可观测出口）。默认 `MAX_CONCURRENT_SPAWNS=2`、`MAX_LIVE_CHILDREN=48`，均可用环境变量覆盖。
三条设计边界写进注释以防被破坏：名额只覆盖**启动瞬间**（否则长驻录制进程永久占闸，12 房死锁）；
存活超限**只告警不强杀**（杀掉正在录制的进程比争抢更糟）；`run_hidden` 路径 **fail-open + 5s 预算**
—— 它被编排线程/asyncio 线程上的短探测共用，硬等 30s 会撞上 §11.3"禁止占用编排线程"红线，
放行次数计入 `failopen_transient_total` 而非静默降级。
已接线：`run_hidden`（一处覆盖 6 个调用模块）+ `MseStreamer` 预览启动与两条 teardown；
`budget_snapshot()` 已挂进 `get_system_stats` 与 `low_tick` 系统快照广播 —— 下次预览冻结时
"当时有多少 FFmpeg 在跑"第一次成为可查事实。**其余派生点（capture / shared_ingest /
exporter/clip / segmented / probe / room_utils）尚未接线，仍是无闸启动**，见报告 §11.1 遗留清单。
配套 6 条行为测试 `tests/test_process_spawn_budget.py`（封顶、异常归还、注销、fail-open 留痕）。

**文档↔代码矛盾收口**

生产实际走**共享进样**（`settings.json=true` 经 `room_handler.py:4599` 覆盖 `LscConfig`），而 §3.2/§7.3.1 写的是"默认独立双进程"；`dvr_output_path` 并非"恒为空串"；`handle_align_preview_audio` 旧副本已不存在；WS 引导入口其实有**三条**（含 0% 覆盖的 `python-backend/start.py`）；`output_dir` 全仓 8 处默认实测已统一。新增 **`CLAUDE.md` §7.4 资源并发预算与全局上界**，把"FFmpeg 进程总数无任何全局上界（33 个派生点 / 15 文件，所有信号量都是串行化而非封顶）"建档并约束后续新增派生点。

**发布完整性守卫（`ci.yml` release job）**

新增一步 bash 守卫，把两类已实测到的发布事故变成 CI 硬失败：
① **tag 必须等于 `package.json` 的 version**（v1.0.12~v1.0.15 曾无 tag 发版）；
② **版本号不得倒退**（v3.0.23 打于 2026-09-01 21:26，同天 22:00 又打了 v1.0.11，
而 `compareVersions` 按三段数字比较 ⇒ 装过 v2/v3 的用户永远看到"已是最新版本"）。
四个分支都做过本地复演验证（非法形态 / 不一致 / 倒退 / 现存最高取自真实 tag）。

⚠️ **该守卫会拦下按现编号的下一次发版**：仓库现存最高 tag 是 `v3.0.23`，而 app 版本是 `1.0.17`。
这正是设计目的（把静默陷阱变响），但发布前需先二选一：**向前重新编号**（如 v3.0.24 / v4.0.0），
或删除 v2/v3 那批 tag 并确认没有用户装着它们。属于需要产品决策的事，故未代为改动编号。

**平台解析失效第一次可被区分（新错误码 `parser_stale`）**

legacy 面原本只有 4 个错误码，`ERROR_PARSE_FAILED` 是 catch-all，且被 B站/斗鱼大量用在
"接口字段变了"的场景 ⇒ 用户看到"解析失败/请检查网络"，而实际需要的是更新解析器。
现在在 `registry.parse_stream` 这**唯一集中点**按异常类型分类：结构类
（`KeyError`/`IndexError`/`TypeError`/`AttributeError`）判为 `parser_stale` 并给出
"页面结构可能已变更（不是网络问题）"的文案 + ERROR 日志；网络类仍为 `parse_failed`。
9 个适配器共用这一处，不在各适配器里重复判断。已确认 `offline` 由独立探测判定、
不受新码影响，因此不会篡改重连语义。新增 3 条行为测试。

**派生闸补最后一条无界生产路径：独立录制 FFmpeg**

`recorder/capture.py` 的 `Popen` 接上 `spawn_slot`，用**新增的 `fail_open=True`** 语义：
等 8s 拿不到名额就 WARNING + 计数后照常启动 —— 绝不因我们自己的闸丢一场录制，
录制被拒绝比争抢更糟。`run_hidden` 原先自己写的那份 if-acquire 逻辑已改为复用同一个
`spawn_slot(fail_open=True)`（不再是两套）。fail_open 的关键安全性质有用例锁住：
**未持有名额时绝不 release**，否则闸会越放越宽 —— 正是 `capture.py:269`
「从未成功 acquire 过不得 release」注释警告的同类错误。

覆盖面收口结论（`CLAUDE.md` §7.4 表格已更新）：`exporter/clip.py` 已被 `_export_semaphore`(1-2)
限住无需再接；`platforms/probe.py` 与 `recorder/segmented.py` 属 V2 暗发布路径；
`handlers/room_utils.py` **没有派生点**（只有一处 `Popen` 类型注解，审计初版把它列入是错的）。

两处附带收获：mypy 棘轮**当场逮住**我新引入的一条 `arg-type`（证明它拦得住新错误类型）；
`lsc/recorder/segmented.py` 经复核**无任何生产 import**，是 §11.5 清单外新增项
（暂不删除 —— 它的去留属「V2 管线命运」那个待定决策）。

**派生闸再接一处：共享进样上游（最大扇出点）**

`shared_ingest._launch_process` 是 4 个调用点（上游 / 录制 sink / 预览 sink / 换源候选）共用的
唯一启动咽喉点，且**4 处都已用 `try/except Exception` 包住**并走现成的启动失败清理
（含把 manifest 落成 `FAILED`）—— 这个前提成立才敢接。采用"只闸启动瞬间"形态：`with` 退出即归还名额，
不做存活记账，因此不可能出现"某处忘了注销导致后来全部起不来"。超时取 **6s**：必须明显小于
`orchestrator.call` 的 10s 默认预算，否则上游恢复若在编排线程触发会连累整条 call 通道超时。
名额归还保证已由 governor 层用例 `test_块内异常也必须归还名额` 覆盖；
调用方吸收异常的部分为结构性事实（4 处 `except Exception`），未另加测试。

**mypy 从"全仓静默放行"变为阻塞门禁 + 债务棘轮**

CI 原本是 `mypy ... || echo "::warning::"`，等于**整个仓库的类型错误一律不影响 CI**，
注释里冻结的"87 errors"也早已失真（实测 112，且仍在漂）。现在该步骤**阻塞**，存量债务用
`pyproject.toml` 的 `disable_error_code` **按模块 × 按错误码**精确豁免（26 个模块，由 mypy
真实输出生成，每个只列它当前实际触发的码）。因此：非豁免文件、以及**豁免文件里新出现的错误码类型**，
都会被 CI 直接卡住。用探针验证过：往已豁免模块注入一个未豁免的 `name-defined` 错误即报错。

顺带修掉一条真实错误：`hidden_run_kwargs()` 返回 `dict[str, object]` 会让它**所有**调用点的
`subprocess.run` 重载匹配失败，改签名为 `dict[str, Any]` 后 `lsc/utils/process_launcher.py` 归零
（109 → 108）。**注意这是"围住"而非"清零"**：108 条存量仍在，需按 `acceptance` 17 /
`orchestrator` 14 / `valorant_broadcast` 14 的顺序逐码删除 override。

**窄口径缺陷批次（审计报告 §11 遗留项）**

- **`save_rooms` 是 rooms.json 的第二条写入口，却比 `add_room` 松**：只查"是不是字符串"，
  既不校验 URL 结构，也没有房间数上限 —— 一次批量保存即可绕过 `MAX_ROOMS=12` 这条产品硬限制，
  并把超长/带控制字符/非 http(s) 的脏链接直接落盘。抽出与单条入口同源的纯结构判据
  `_room_url_shape()`（单条入口改为调用它，**两条入口从此共用一份规则**），批量入口补上限与结构校验。
  新增 6 条行为测试 `tests/test_save_rooms_validation.py`。
- **`repair_recording` 挤占录制启动池**：它内部同步、超时上限 600s，却跑在 6 槽的 `rec` 池里，
  一次修复可占住一槽 10 分钟，多路叠加即饿死录制启动。新增与既有四个池同构的
  `_repair_executor`（1 槽，天然串行）并纳入 `shutdown_room_handlers` 的关停清单。
- **删房不清账本**：`remove_room` 过去只清权威快照与预览 registry，从不调用
  `_reset_epoch_scoped_clip_state`；`_manual_clip_counters` / `_ai_clip_counters` 更是全仓无任何
  pop/clear。后果不止内存：重新加入同名房间会继承上一场的"已删除 tombstone / 已入列键"，
  把新回合的 `clip_queued` 当成重复而吞掉。新增单一咽喉点 `_forget_room_registries`
  （委托既有 epoch 清理），配 2 条行为测试并做过红→绿双向验证。
- **分析结果新鲜度只看 mtime**：复制录像、搬目录、moov 修复后重写容器都会推进 mtime 而内容未变，
  单判据会把这些情况误判为"分析过期"，静默丢弃数小时成果。sidecar 增加 `video_size` 作为第二证据，
  **mtime 与体积同时变化**才算过期；老 sidecar 缺该字段时保持原严格判据（不因升级而放宽）。
  新增 `tests/test_analysis_freshness.py` 6 条。
  ⚠️ 这是一次**契约变更**：`tests/test_persistence.py::test_is_stale_mtime_changed` 原本断言
  "只改 mtime 即过期"，正是被修掉的缺陷本身，已改写为三条（仅 mtime 变 → 有效；
  mtime+体积都变 → 过期；无体积证据 → 维持严格）。

**两项经核查判定为"文档误判，不改代码"**

- `SUBSYSTEM_ANALYSIS` 与审计初版把 `wait_for(shield(fut))` 的两层嵌套列为"推迟强停"缺陷。
  实为**有意设计且两层都有显式上界**：源码注释写明"超时后仍须持锁等到线程经 cancel_check 退出，
  避免双 OCR 并发压垮 DirectML"、"不得 cancel 协程后提前释放 semaphore"；停止路径是
  grace(`_SCAN_ABORT_GRACE_SEC`) → 硬上限(`_SCAN_ABORT_HARD_SEC`) → 强制释放，逐级 WARNING/ERROR。
  拆掉它会直接违反 DirectML 单上下文约束。**未改。**
- "settings 双写源"这一条**部分成立但未修**：`autoLaunch` / `minimizeToTray` 确实同时存在于
  Electron 的 `app-settings.json` 与后端的 `settings.json.appSettings`，且 `locale` 与 `language`
  是同概念两个键名。实际生效方是 main 进程那份。收敛它需要改动用户设置的持久化格式并做迁移，
  风险面是"用户设置丢失"，故精确记录归属问题而**不仓促改**（见报告 §11.5）。

**另两处静默缺陷**

- **一次性分析 sidecar 孤儿化**：`analysis_handlers.py` 的 `analysis_storage_path` 在分析**启动时**捕获、
  几分钟后落盘才用；期间录像定稿改名（`_录制中.mp4` → `_至_….mp4`）就会把 sidecar 写到已不存在的
  录像名上，永不被 `get_analysis_result` 读回。单房与多房两处落盘现在都在写前重取房间当前路径
  （取不到才沿用捕获值），与持续分析侧 `_sync_analysis_save_path` 同语义，无新增管线。
- **timeline 失效回调重复投递**：`timeline_service.add_invalidate_listener` 是纯 append 且没有注销路径，
  而 `invalidate_timeline` 遍历整张表逐个调用 —— 注册方在重连/重建时被再次装配就会投递多次，
  并把回调持有的旧引用永久钉住。改为幂等注册，配 1 条已验证红→绿的用例
  （摘掉去重即 `回调被重复投递 3 次` 失败）。

### 修复（P0：退出应用后 python 后端进程残留）

**这一条是把 `python-backend` 纳入 mypy 范围当天掉出来的缺陷，属于每次退出必触发。**

`main.stop()` 里这一行：

```python
asyncio.run_coroutine_threadsafe(self.server._server.close(), self._loop)
```

`websockets` legacy 的 `WebSocketServer.close()` 是**同步**方法（实测
`inspect.iscoroutinefunction(...) is False`），传进去的是 `None`，于是
`run_coroutine_threadsafe` 每次抛 `TypeError: A coroutine object is required`；
而紧邻的 `except RuntimeError: pass` **抓不到 TypeError** ⇒ 异常冲出 `stop()`，
排在后面的 `self._main_stop.set()` 永不执行，主线程永久停在
`self._main_stop.wait()` 上。用户侧表现即"关掉应用后端进程还在"，打包前总要手工
杀残留进程也是同一件事。

- 改用 `call_soon_threadsafe(srv.close)`；并把 ws 那两步整体包进 `try/finally`
  —— **清理链后半段（放行主线程、join ws 线程、最终落盘）必须无条件执行**。
- 两处 `except RuntimeError` 与落盘那处 `except OSError` 放宽为 `Exception` 并**写日志**
  （不是 `: pass`）：停机路径上任何异常都不该吞掉后续清理。
- 新增 `tests/test_backend_shutdown.py` 6 条：钉"用 call_soon_threadsafe 投递"这一机制
  （**做过负控**：塞回旧写法即变红），钉"ws 两步坏成什么样都必须放行主线程"这一不变量，
  以及 join/落盘确实执行、stop 幂等。
- 附带修掉 `stop()` 之前的一处静默：`flush_pending_room_saves` 的捕获面只有 `OSError`。

### 债务收口 · 第三批（2026-09-18 下午，见报告 §11.9）

**门禁范围：三道"看起来有、实际管不到"的闸全部改到真实覆盖面**

| 闸 | 之前实际覆盖 | 现在 |
| :--- | :--- | :--- |
| `ci.yml` 的 ruff | 只有 `lsc/ python-backend/`；`tests/` **从未被任何门禁检查**（24 条：未用 import、lambda 赋值、`%` 格式化、`zip` 无 `strict`），`scripts/` 被 `--exit-zero` 明放（13 条） | `ruff check .` 全仓一条命令，**52 条清零**；删掉 `--exit-zero` 那步 |
| `core-guard.yml` 的 `lint-core-python` | **常红**：末步 `ruff format --check` 实测 34/41 文件会被重排（本仓库从未 ruff-format 过），且 `ruff>=0.5` 未钉版本 | 删除该 job（ruff 已由 ci.yml 全仓覆盖；保留本工作流独有的 `tsconfig.electron.json` 检查），理由与复现数字写在工作流头部 |
| `mypy` | 只有 `lsc`。**python-backend 整个不在范围**；原注释给的原因（"目录名含连字符不是合法包名"）是错的，真实阻塞物是 `python-backend/__init__.py` 那只有一行注释的残留文件 | 删该残留 + 配 `mypy_path`/`files` ⇒ **79 → 105 文件**受阻塞检查 |

`scripts/valorant_vision/eval_source_dataset.py` 的两条是真问题：本地 `gates` 表算出的
`all_passed` **算了但没人用**，裁决来自另一张表 `check_all_gates`，而打印的逐行
PASS/FAIL 用本地表 ⇒ 两张表分叉时会"逐行 PASS 却结论 FAILED"。现
`gates_passed = all_passed and not failures` 并对两种分叉各给一种结论行；那条
"仅为可读性保留"的注释与代码不符，已更正。`recover_and_backfill_broadcast.py`
的返回注解引用了从未导入的 `JianyingDraftResult`（靠 `from __future__` 才没炸），已补。

**拆分第二批**：`room_handler.py` **11038 → 10874 行**，6 条平台凭证路由外移到
`handlers/credential_handlers.py`（该簇此前有 11 处函数体内 `import`，把真实依赖
藏起来了）。settings 簇**经实测搬家不安全**并停止：15+ 处测试直接
`monkeypatch` room_handler 的 `SETTINGS_FILE` / `_settings_cache` / `load_settings`，
搬走会造成"测试静默测空气"与"单测写用户真实 settings.json"两种事故（报告 §11.9-B）。

**平台失效可观测性（P2-2）**

- 新增 `lsc/platforms/diagnostics.py`：`(platform, kind)` 降级计数 +
  `degradation_snapshot()`，接入 `get_system_stats` 的 `platform_degradation`。三个源
  接进来：`parser_stale`、**虎牙 SDK 改写回退**（原 4 条静默 `return raw`，而 raw 恰是
  "只推一段 GOP 就 EOF"的坏形状 ⇒ 虎牙改版表现为"预览卡几秒没了"且日志无因）、
  **凭据实测失效**。
- `collect_system_stats(extra=…)` 从两个调用点收成**一个** `_system_stats_extra()`
  —— 两份 dict 各写一遍必然分叉（`platform_degradation` 第一次就只进了按需那条，
  顶栏周期刷新看不到）。
- **Cookie 从"配了没"升级为"还好不好"**：`invalidate()` 的 `reason` 不再被 `del`，
  改存 `(时刻, 原因)` + 新增 `invalidation_info()`；三条 `get_*_cookie_status` 与
  `get_platform_credential_status` 一并返回 `credential_status` /
  `credential_invalidated_at` / `_reason`；设置页 `CookieStatusLine` 在**确实配过**
  的前提下显示"实测已失效：原因 + 时间"。真正的缺口不是"无人标记"（resolver 期
  一直有），而是 **ingest 期漏接** —— 现由 `mark_failed_candidate`（10 个调用方共用
  的收口）调用升格后的唯一入口 `credentials.invalidate_credentials_on_auth_failure`。
- **平台自检进产品**：`lsc/platforms/acceptance.py` 那套控制面验收此前只有开发者跑
  `scripts/platform_acceptance.py` 才碰得到。新增 `run_platform_self_check` 路由 +
  设置页「平台自检」面板，固定 `record_dir="" / recording=False / preview=False`
  （不启 FFmpeg、不落盘），复用循环自带线程池、同一时刻只允许一个在飞，
  URL 判据**注入**添加房间那一条而不是自带一套。新增 15 条用例（含 2 条设置页链路
  与 3 条"必须复用同一条判据"的接线守卫）。

**类型层顺带清掉的真问题**（非机械码已归零：`operator` / `union-attr` / `no-redef` /
`dict-item`）：`resolver` 里同一循环一半容错一半直取属性；`acceptance` 往
`Mapping[str, ProbeResult]` 里塞 `{id: None}`；`controller` 用 4 个位置参构造
`ExportResult` 把 `""` 落进 int 字段 `clip_index`；`error_messages` 的返回类型与
自己的 docstring 矛盾；`persistence` 缺侧车顶层形状校验；`alignment_handlers`
手写两处 `room_utils.safe_float` 已有的逻辑（`jianying` 那处**不**复用 —— 它 except
时要丢掉整条 clip，换默认值会把"缺失"伪装成 0 秒拉偏均值）。

**一次自我更正**：本报告与代码注释最初都写"`invalidate()` 全仓零调用点"，**这是错的**
—— `resolver.py` 早有私有助手且被三处调用。是既有的
`test_platform_probe_and_resolver` 三条用例在我给共享判据加 `credential_kinds` 预筛后
变红才发现的。已按既有契约收敛（去掉预筛，显示层再判"配过没配过"），并删掉我新写
的那份重复实现。**这是"新增机制前先找既有收口"的教训，写在这里而不是删掉。**

### 修复（P2-1：本地回看远定位不再顺序扫几百万字节）

症状（报告 §7.3 第一条）：小时级录像上点一下时间轴，画面半天不动。

根因：`localFileMseSource._seekToFileTime` 只做**一次**外推，用的是**头部平均码率**。
变码率录像（前段静止/低码率、后段高运动）里这个斜率与落点附近的真实码率能差
两个数量级 ⇒ 外推严重低估字节位置 ⇒ 差额全靠顺序读盘补齐。

- 新增 `probeBoundaryTime()`：在定位窗口里找合法 fragment 边界（复用主路径的
  `findFragmentBoundary`）并读它的 tfdt 时间（复用 `fmp4Box.readTfdtSeconds`）。
  **刻意不新引第二套 box 解析** —— 这条路上有三次真机事故换来的守卫。
- 定位改为：第一枪仍按外推（等码率录像一发命中，行为与今天完全一致），
  随后把探测到的 (时间, 字节) 当**稀疏检查点**登记进索引，并在**字节轴上二分**
  （至多 4 轮）。二分与码率无关，所以变码率不再致命。
- 接受条件刻意**单向**：只接受"落在目标上或略早"，晚于目标会真跳过内容；
  早一点只是多读几段。窗口几何（右端收敛在外推位置）保持原样，
  2026-09-15 那条"窗口不得向右扩"的守卫不受影响。
- `estimateByteOffset` 顺带修正：目标被两个锚点夹住时改用**两点局部插值**，
  右侧无锚点才退回全局码率外推（原两条用例的行为不变，仍绿）。
- 检查点随 `_indexCache` 跨会话缓存 ⇒ 用户来回拖轴时后续 seek 直接受益。
- **实测改进（含负控）**：400 段 1KB + 300 段 512KB 的变码率夹具，目标文件轴
  1650s —— 单发外推落在字节 423,088 / 157,728,548（需 134 次读盘才到目标）；
  加二分后落在 116,829,404（4 次探测，出画前读盘次数 <60）。把轮数改回 1 即
  用例变红，是这条改动的负控。
- 新增 3 条用例（局部插值 / probeBoundaryTime 真实时间 / 远目标落点与读盘上界）。

### 债务收口 · 第五批：mypy 非机械码清零 + 修掉一条永不成立的适配器协议

关掉全部豁免实测的残留从 **355 → 296 条 / 42 → 31 文件**，且只剩
`no-untyped-def` 273（机械签名）与 `arg-type` 23（异构字典取标量的类型噪声）。
`operator` / `union-attr` / `no-redef` / `dict-item` / `call-overload` /
`no-any-return` / `assignment` / `import-untyped` 现在**全仓为 0**；
豁免段从 42 收紧到 31。

**这批最大的收获是一条协议级缺陷**：`lsc/platforms/base.py` 把
`PlatformAdapter` 的 `platform` / `display_name` / `capabilities` 声明成**可变属性**，
而 9 个内置适配器全部以 `@property` 实现 —— mypy 判定
"expected settable variable, got read-only attribute"，于是
**9 个适配器没有一个满足这条协议**。含义不是"有个报错"，而是平台层
（产品唯一入口）的接口契约**纸面存在、实际永不成立**：新写一个漏了 `parse()`
的适配器也不会被静态检查拦住。改为只读 property 声明后：

- 三处因此被放宽成 `Iterable[object]` 的 resolver 形参改回
  `Iterable[PlatformAdapter]`（TYPE_CHECKING 导入，运行时不求值，已验证不成环）；
- `registry._DEFAULT_ADAPTERS: tuple[PlatformAdapter, ...]` 这行标注从此成为
  **编译期守卫**：谁把 property 改回可变属性，CI 立刻红。

逐条分诊另修：`_finish_v2_lease_refresh` 往 `dict[str, StreamInfo]` 写 None 的
推导链改为可证明、`resolver`/`jianying_draft` 两处同名变量承载两种类型
（`probe`、`usable`）、`_run_cancellable` 用真实 `CompletedProcess` 取代
`SimpleNamespace` 冒充、`ocr_detector` 那条**一直无效**的
`# type: ignore[import-untyped]`（ignore 挂错了行）归位、`url_policy` 用公共基类
`list[BaseHandler]` 表达 `build_opener` 的真实契约、17 处 `network_context`
**只标注本地拷贝**而不放宽对外契约。

**过程中我自己写坏了一次**：给两个文件补 `from typing import Any` 时用
"最后一个顶层 import 之后插入"的启发式，插进了 `weibo.py` 一个跨行 import 的
括号内部 ⇒ 语法错误、42 个测试文件收集失败，被 mypy + pytest 当场拦下。

### 修复（回看读盘在播放头停住时无界增长，实测 ~8 MiB/s）

**P1，用户可触发**：回看时只要播放头停下来（用户按暂停，或 §7.1 那种 media clock 冻结），
`LocalFileMseSource` 的读盘**不会停** —— `_pump` 每 `DEFAULT_POLL_MS=500` 重入一次、
每轮读 `DEFAULT_CHUNK_BYTES=4MiB` 一块，而前瞻门只是"不喂给播放器"、出画后的队列修剪
又整体关闭 ⇒ **约 8 MiB/s（480 MiB/min）持续堆进渲染进程 JS 堆**。暂停 30 秒 ≈ 240 MB，
4 分钟量级就是 2 GB ⇒ 渲染进程 OOM/崩溃，并且反过来加深冻结（正反馈）。

- **修复**：在 `_pump` 循环**读盘之前**先 `_drainQueue()` 再判"队列是否已跑到播放头前面"，
  是则本轮不读盘、等下一次 poll。顺序很关键：只加判据而不先 drain，会把回看钉死在原地
  （播放头恢复推进后既不喂段也不读盘）—— 这条被新用例的 C 阶段当场抓到。
- **可执行用例**：`localFileMseSource.test.ts`「出画后播放头不动时：读盘必须停下，
  队列峰值与文件长度无关」三段式（冻结期读游标必须一格不走 / 队列峰值 ≪ 文件长度 /
  播放头追过队列后读盘必须恢复）。负控：删掉那 3 行 ⇒ 队列峰值 400ms 内
  262,292 → 584,060 且仍在线性增长，用例变红。
- 同时把审计 §7.3 那条"上界只在出画前成立"改正为事实：**出画后根本没有上界**，
  而且 §7.1（预览时钟冻结）多了一个已阻断的放大环节（根因仍开）。
  详见 `docs/reports/debt-audit-20260918.md` §11.15。

### 修复（断连发件箱溢出不再静默丢消息）+ 删除生产外的第二套分段录制实现

- **`websocket.send()` 队列满时的语义改正**：原先"压满 100 条就 `shift()` 丢最旧的一条，
  但对当次调用仍返回 `true`" —— 被丢的那条更早的消息，调用方**已经拿到过 `true`**（据此不弹警告），
  属静默失效；而且丢最旧会破坏命令顺序（`add_room` 被挤掉、后继 `set_settings` 留下）。
  现在**拒绝最新一条**（`warn` + `return false`），已排队的保持 FIFO，调用方原有的
  `DISCONNECTED_SEND_WARNING` 分支自然生效，不需要新协议。
  用例 `websocket.test.ts`（上限压到 2 发 3 条，断言第三条 false 且 flush 顺序为
  `auth, get_rooms{n:1}, get_settings`）；负控：改回 `shift()` ⇒ 用例红。
- **删除 `lsc/recorder/segmented.py`（284 行）+ `tests/test_segmented_recorder.py`**：
  §7.3 的"两套分段录制并存"经核不是"两套都在跑"—— 生产沿是
  `shared_ingest.build_segmented_recording_command` + `_finalize_segmented_manifest`
  （`orchestrator.py:2845/2863` 的 `segmented=True` 全走这条），而 `SegmentedRecorder`
  **没有任何生产 import**（CLAUDE.md §5 早就把它标为"➖ 非生产路径"）。与前几批同类项
  （`mse_sender` / `export_service` / `frame_capture` / `sound_detector`）一致处置；
  连带让"派生闸待接线"清单少一项。mypy 覆盖 105 → 104 文件、pytest 2129 → 2126。
- 另附一次**对自己旧结论的复核表**（报告 §11.16-C）：sidecar 路径重取、分析新鲜度、
  退出刷盘、`save_rooms` 校验、嵌套 `wait_for(shield)`、`dependency_manager` 的 assert
  —— 六条逐条回到代码核为真；不成立的那条（回看队列上界）已在 §11.15 改正并修复。

### 修复（"通用场景"持续分析永不产出）+ 导出簇死副本清理 + handler 覆盖率

- **P1，用户可见**：选择"通用"档启动持续分析时，后端会照常启动循环、UI 进度照常报、
  每轮往日志写一条 WARN，但**结果永远 0 条**。链路：前端发 `mode='scene'` →
  粗扫分支只覆盖 `game='valorant' + mode='valorant_round'` → 落到的
  `_detect_rounds_by_audio_rhythm` 是个恒返回 `[]` 的废弃兼容包装，而且它报
  `scan_succeeded=True` ⇒ 未覆盖窗口被记成"扫过但没有回合"。
  `GenericAnalyzerPlugin` 自己声明 `realtime_continuous=False`（只有录制后 `analyze_file`），
  这条声明此前**没有任何执行点**。现在：启动侧按能力 + 实际覆盖组合拒绝并给出可执行指引
  （`当前只有「无畏契约 · 回合」模式支持直播中持续分析…`），兜底分支改为如实
  `scan_succeeded=False` + 首次 ERROR，废弃包装删除。
  用例：`tests/test_continuous_analysis_capability_gate.py` 4 条。
  **遗留（需产品确认）**：前端「通用」档在持续分析入口仍可选（只是会被拒），
  要在 UI 上禁用需确认。
- **导出批状态双副本**：`_notify_export_overall`/`_reset_export_batch`/`_export_job_states`
  在 `room_handler.py` 与 `export_handlers.py` 各一份，被调用的是后者，而它**丢了
  `_export_batch_id = batch_id`** ⇒ 广播里的 `batch_id` 永远是空串。已删死副本、修活的那份
  （新用例第一次跑到就打红，修完转绿）。另查实：`_reset_export_batch()` 无任何调用方 ⇒
  `export_overall_progress` 这条协议消息从未发出（前端也无监听者）—— 已在协议表标注
  "当前不可达"，真接上需要先定批量导出协议。
- **`room_handler.py` 搬家残留清扫**：13 个模块级函数 / 123 行 + 3 个死全局删除
  （判据：本文件内零引用 + 全仓无 `from room_handler import X`；用 `ast` 取精确行区间）。
  `_refresh_audit_queue_state`（被测试直接断言）与 `format_manual_clip_label`
  （前后端 parity helper，被文子守卫要求）**刻意保留**，理由记入报告。
- **两个 handler 模块从"只有文本守卫"补上真断言**（§9-P0.5）：
  `export_handlers.py` 覆盖率 16% → **33%**、`alignment_handlers.py` 16% → **23%**，
  新增 `tests/test_export_alignment_units.py` 30 条（导出入/出点 5 条分支、状态表 LRU、
  批计数、预设回退、`_epoch_ms_to_mono` 跨时钟换算保住两路 5s 采集差 等）。
- **打包卫生**：`python-backend/run_and_export.py`、`run_full_export.py`（硬编码 test video 的
  一次性开发脚本，0% 覆盖、无人引用）会随 `extraResources` 进入每个用户安装包；
  已移到 `scripts/` 并加入既有脚本排除清单，运行时行为零变化。
- `docs/reports/debt-audit-20260918.md` §11.18-G：§7.4 的"`_RoomsThrottle` 与 drain coalesce 双实现"经核是**误判**（两者分别在生成侧/发送侧，`force=True` 分支是超集不是丢更新），并把该节流从 0 覆盖补成 4 条可执行断言。
- 详见 `docs/reports/debt-audit-20260918.md` §11.18。mypy 关掉豁免后的实测残留
  296 → 282 条（`no-untyped-def` 259 + `arg-type` 23），非机械码仍为 0。

### 修复（广播通道的"丢消息检测器"本身不可信）

`BroadcastHub` 是后端→前端唯一的状态推送通道，前端 `useWebSocket` 靠消息里的 `_seq`
判断"我是不是漏了一条广播"。修之前这条防线是假的，三处叠加：

- **发号与入队不同步**：`self._seq += 1` 与 `put_nowait` 之间没有任何锁，生产者是
  orchestrator 线程 + 一批 executor 线程 ⇒ 队列里可以是 `[6, 5]`。前端只在
  `_seq > last + 1` 时报警，于是倒序到达既不报警、还把 `last` 拉低，**掩盖真正的缺口**。
- **遥测也占号**：`system_stats` / `rooms_updated` / `mse_segment` 这类可丢弃消息同样发号
  ⇒ 队列满时**设计内的丢弃**被放大成假告警，真告警混在噪音里。
- **唤醒失败不可见**：asyncio loop 已关闭时 `notify_broadcast` 只 `_log.debug` ⇒ 生产日志
  里没有痕迹，表现就是"前端卡在旧状态"。

现在：`_offer_locked()` 在 `_seq_lock` 里同时完成"取号 + 入队"，**只给非 `_DROPPABLE_TYPES`
的消息发号**，入队失败不提交号段；驱逐/扩容整段持锁（换队列期间不会再有线程往旧队列里
塞导致消息凭空蒸发）；饱和慢路径改成"扩容 + 有界重试"，最后兜底一次**不带号**的阻塞入队
—— 宁可这条不参与缺号判定，也不像原实现那样 `put` 超时后把关键消息整条丢掉。
唤醒失败计数 + 首次 WARN，并新增 `broadcast_health()` 快照，经 `_system_stats_extra(bridge)`
进 `system_stats.extra.broadcast`（顶栏周期刷新与按需查询同源）。

- 用例：`tests/test_broadcast_seq.py` 6 条（并发 8×25 下队列顺序 == 序列号顺序且连续、
  遥测不占号、满队列丢遥测不产生幻影缺口、唤醒失败计数且只 WARN 一次、
  唤醒路径异常不得逃回生产者线程、快照可序列化）。并发用例把 `put_nowait` 换成
  "先让出 GIL 再入队"，否则 CPython 下竞态很少发生、用例等于碰运气。
- 负控 4/4 抓到（给所有消息发号 / 发号与入队不同锁 / 唤醒失败退回 debug 不计数 /
  去掉兜底 except 让异常逃逸），逐条把真实行为改坏后用例变红。
- 顺带删掉 `broadcast_hub._TERMINAL_TYPES`：定义了却没人读的死常量
  （真正的"永不驱逐"判据是 `not in _DROPPABLE_TYPES`，覆盖面更大）。

### 债务收口 · 第六批：把"只匹配源码文本"的守卫换成可执行用例（含负控）

审计 §5：`tests/test_frontend_stability_guards.py` 141 条用例里 **137 条只做源码
子串匹配、0 条断言行为** —— 重命名就红、逻辑写反却绿，还会让"这块有测试"变成
假话。本批立了一条硬顺序：**先写可执行用例 → 负控证明它有牙 → 才删对应守卫**。

- `lsc-electron/src/services/mediaSourcePlayer.test.ts` **9 → 26 条**：新增 10 条真跑
  `MsePlayer`（假 `video` + `play()` 永不 settle 的"hanging"假件，这才是 media clock
  冻结的真实形状），覆盖 goLive 落点 / 空缓冲不写播放头 / idle 早退 /
  `getBufferedRanges` 退化区间 / `_playExhausted` 置位与清除 / 停滞强扫与预算清零。
- **负控 6/6 抓到**（改坏真实行为看谁变红）；同一批变异下**全量文子守卫有 4 条完全无感**
  （去掉 `_playExhausted` 或条件、不过滤 `end===start`、空缓冲写播放头、`stop()` 不发 resume）。
  负控还抓到我自己第一版的假用例：播放头放 119.9 同时满足了出界条件，被测分支没被
  单独隔离 ⇒ 补了几何上**只有** `_playExhausted` 能触发的用例（缓冲 [100,120]、播放头 110）
  加同几何反证。详见报告 §11.13。
- 下线被完全替身的 **5 条**文子守卫（141 → 136，原地留指针注释）；
  `..._source_open_and_cleanup_are_single_lifecycle_paths` **保留** —— 它还断言
  `sourceopen` 只注册一处，替身未覆盖，不顺手多删。
- 明确了 `Workbench/index.tsx`（5259 行）拆分的前置条件：它名下 42 条按源码文本写的
  守卫会随搬家集体变红，**必须先迁移再拆分**，否则就是让守卫给重构让路。

### 债务收口 · 第七批：一键对齐的播放头落点（一个从未生效的"纸面功能"）

审计 §5 的迁移线走到预览音频对齐簇时，挖出的不是守卫问题而是**一个被三份文档写成机制、
但自写入那天起就没有任何调用点的功能**：`previewAudioAligner.ts` 里的
`applyOffsetWithDriftCorrection`（seek 后量残余差 → `playbackRate ±5%` 平滑追赶 → 恢复 1.0，
连同一个模块级 `WeakMap` 共 55 行）。`git log -S` 显示历史上两处改动都是定义行，
即**从未被接上过**；`docs/PROJECT_DESIGN.md §11.4`、`docs/SUBSYSTEM_ANALYSIS.md` 数据流、
`docs/reports/MODULE_AUDIT_REPORT.md` ⑤（还列为"亮点"）都按"前端就是这么落的"在写。

- **真实落点**一直是 `Workbench/index.tsx` 内联的 8 行 seek：任何可执行用例都跑不到它，
  只被一条文子守卫当作 `split()` 的锚点。
- 现抽成唯一入口 **`src/utils/previewAlignment.ts` 的 `applyAlignmentSeek(video, offset)`**
  （返回 `seeked / negligible / no-buffer / seek-failed`）。落点 `currentTime - offset`
  **按符号求差**并夹在已缓冲区间内：在后端"最慢房间归零 ⇒ offsets 恒 ≥ 0"的不变量下
  与原实现逐位一致，同时把这条**跨语言隐含约定**写进代码，将来放开有符号偏移也不会
  静默丢掉前向修正（原 `offset < 0.05` 的写法依赖该不变量）。
- 唯一的行为改动：`alignedCount` 不再计入"setter 抛异常"的房间（原先 `try{}catch{}` 后
  照样 `alignedCount++`）⇒ Toast 里"已对齐 N 个"必须真的动过播放头。
- **新增 8 条可执行用例 + 负控 6/6 全抓到**（去掉 `Math.abs`、去掉缓冲下限、去掉上限、
  不 `play()`、吞异常仍报 `seeked`、把 no-buffer 判在 negligible 之前）。
- **没有**盲接残余差修正：`playbackRate` 微调作用在用户正在看的 9 路预览上、与停滞强扫
  相互作用，多房间真机验证前无法验收（同 §13 剪映边界的处置）。改为把纸面承诺改如实：
  死代码删除 + 原地留说明，`PROJECT_DESIGN §11.4` 改写为"当前未实现"并写明**导出不受影响**
  （导出补偿用后端 `content_offset` 数值，不经过播放头），`MODULE_AUDIT_REPORT.md` ⑤ 标注作废。
- 详见 `docs/reports/debt-audit-20260918.md` §11.14（含一条我提出后又自行证伪的猜测：
  "负偏移被整间跳过"经核 `align_audio_map` 出口 `max(0.0, ...)` 归一，当前不成立）。

### 债务收口 · 第十四批：取标量口径收成一个模块，`lsc/` 侧 mypy 豁免清零（报告 §11.22）

- 新增 `lsc/utils/coerce.py`（`try_float` / `safe_float` / `try_int` / `safe_int`），把全仓
  20+ 份手抄的 `float(d.get(k))` + `try/except` 收成一处；`pyproject` 里 mypy 的
  **23 条 `[arg-type]` 噪声一次归零**，且不是靠写 `cast` 藏起来。
- 一并删掉三份已成形的副本：`handlers/room_utils.safe_float`、
  `handlers/export_handlers._safe_float`、`handlers/room_handler._safe_float`
  （`register_analysis_handlers(safe_float=…)` 的注入位保留，夹具仍可塞假件）。
- **口径变严（有意）**：`NaN` / `Inf` / `bool` 一律回退，`try_int` 不截断小数。
  旧实现全都放行 `nan` —— 它会静默扩散进时间轴与打分；`int(True)==1` 会把误传的开关
  变成合法数值。`tests/test_coerce.py` 30 条钉住判据，**负控 7/7 全部被抓**。
- 导出路由补一条行为用例：`start`/`end` 传字符串照常入队，传解析不出来的值回退成 0
  后被业务校验拒绝，而不是把 `ValueError` 抛给前端。
- 顺手把 `lsc/` 侧剩下 12 个未标注函数补完签名 ⇒ **`lsc/` 的 mypy 豁免一条不剩**
  （豁免段从 21 处减到 15 处，全在 python-backend）。补标注立刻暴露 4 处此前看不见的
  类型不符：`HTTPRedirectHandler.redirect_request` 的 `fp`/`headers` 参数类型、
  `open_http_stream()` 返回 `Any`（调用方对返回对象的属性访问全都不校验）、
  `SharedRoomIngest._lease_manager` 被推断成 `None` 类型。
  `registry.py` 5 个惰性转发壳也钉回了真实返回类型（此前对调用方表现为 `Any`）。
- 约定入档：`docs/CODING_STANDARD.md` 新增 §2.7「从异构字典取标量」+ 红线第 8 条。

### 债务收口 · 第十五批：持续分析状态推导链搬进可测层（报告 §11.23）

- `components/AnalysisProgress.tsx` 里的 **18 分支有优先级** 的判定链 `derivePrimaryStatus`
  与三个格式化函数、`hasFixedScanRange` 一起搬进 `src/utils/analysisProgress.ts`
  （组件 701 → 561 行），并拆出 `confirmedAnalysisPercent`。
  文子守卫只能证明"文案还在"，证明不了"`completed` + `checkpoint_saved` 同时成立时
  用户看到哪一个" —— 而"把没扫完显示成已完成"正是历史事故形状。
- `src/utils/analysisProgress.test.ts` 5 → **33 条行为用例**（优先级、每个终态的语气与
  引导动作、运行态细节拼接顺序与插值参数、非有限值边界），**负控 11/11 全部被抓**。
- 三条守卫按判据瘦身/改写；并抓出一处**守卫被注释糊弄**的实证：
  `assert "待调" in progress` 长期靠一行 `// 待调：…` 注释通过，
  用户可见文案早已是「N 个待 OCR 复核」。
- 元教训入档（§11.23-D）：负控脚本必须自证变异改变了文本（`mutated != ORIG`），
  并把"语法炸/收集失败"与"断言失败"分开报 —— 前两批的"抓到/漏过"里各有 3 条是无效变异。
- 一处现状**未改**、记为待产品确认：valorant 模式下后端不给 `round_phase` 时状态行不拼相位段，
  显式 `unknown` 才显示"寻找回合"（`derivePrimaryStatus` 与 JSX 各有一份同样表达式）。

### 债务收口 · 第十六批：`Workbench` 顶部三个纯函数搬进可测层（报告 §11.24）

- `isApproximateClip`（谁算"定位不准"）、`resolveManualRecordingRange`（墙钟 mark →
  录制轴秒数，媒体起点优先 + 扣 `content_offset` + 夹负 + 空区间拒绝）、`clipSnapshotJobId`
  从 `pages/Workbench/index.tsx` 搬进 `src/utils/clipExportPolicy.ts`
  （组件 5,259 → 5,222 行）。它们本来就不依赖 React 闭包，"搬不动"只是因为整文件 import 代价高，
  于是历史上只有文本守卫 —— `test_is_approximate_clip_excludes_ai_highlights` 就是在切函数体找子串。
- `clipExportPolicy.test.ts` 17 → **29 条**行为用例，**负控 8/9 被抓**；那条"漏过"经按代码顺序
  证明是**等价变异**（函数首行早退让末段的 `precision !== 'exact'` 成死条件），
  而另一条初次"漏过"（mark 只挡 undefined）补了字符串输入断言后被抓 ——
  判据入档：报"漏过"必须先区分 等价变异 / 断言不区分 / 变异无效。
- `reconcileContinuousListedClips` **刻意没搬**：它在 `Workbench/index.test.tsx` 已有真行为用例。

### 债务收口 · 第十七批：导出摘要聚合搬进 `clipExportPolicy`（报告 §11.25）

- `Workbench` 里那段内联 `useMemo` 的导出摘要（待确认/已入队/导出中/完成/失败/入列）搬成
  `utils/clipExportPolicy.ts: summarizeClipExport()`；`ExportSummary` 接口也从
  `AnalysisProgress.tsx` 搬进同一处当**唯一定义**（组件改用 `import type`，prop 名与调用点不变）。
- 原来守它的方式是**断言一段文本不出现**
  （`assert "status === 'pending') summary.queued" not in workbench`）—— 防写法而非证行为；
  现在换成 6 组输入/输出用例，**负控 7/7 全部被抓**（含"待确认被映射成入队""`exported`
  老字段盖住真实 `export_status`""`listed` 被改成未导出条数"等）。
- 首轮两条"漏过"经核分别是**变异无效**与**夹具不区分**（样本没设老字段 `exported`），
  修正后被抓；判据与 Windows 上"负控写回必须带重试 + 还原后读回比对"的坑一起入档 §11.25。
- `Workbench` 5,222 → 5,201 行（`wc -l`）；守卫仍 135 条（该条改为只守"接线"）。

### 债务收口 · 第十八批：手动切片构造搬出 `useCallback`，并修掉跨房跳号（报告 §11.26）

- `handleAddClip` 里那段 `ClipSegment` 字面量（决定导出按哪条时间轴取内容）搬进
  `src/utils/manualClip.ts`：`countManualClips` / `buildManualClip` / `distributeManualIndexes`。
- **修掉一处真实缺陷**：公共轴批量添加原来用"按房间取的基数 + 跨房间的全局下标"编号，
  两间房各加一片时第二间会跳号（`[A,B,A]` → 旧 `[2,2,4]`，新 `[2,1,3]`）。
- 另两处口径收紧：mark 为 `undefined` 时不再建出 `start: undefined` 的切片（旧门控只挡 `null`）；
  单房与批量两条路径不再各写一份字段清单。
- `src/utils/manualClip.test.ts` 新增 **11 条**，**负控 9/9 全部被抓**
  （含"复现被修掉的跳号""不冻结墙钟快照""精度 `&&`→`||`"）。
- 第二条"守卫被顺带满足"的样本入档：`test_manual_clip_uses_per_room_counter` 原断言
  是被**房间查找**那行糊弄过去的，与它想守的计数器无关；已改为直接认那个计数器。
- `Workbench` 5,201 → 5,188 行（`wc -l`）。

### 修复（批量导出把状态写到了别的切片上）

现场形状：一次选中多条**手动切片**导出时，第 1 条的乐观更新会落进列表里**所有**没有
`clip_id` 的条目 —— 它们共用同一个 `job_id`，于是后端的 `clip_queued`/`clip_completed`/
`clip_failed` 按 job 认领时**进度与失败显示在错的切片上**，被跳过（无录制文件）的那条也会显示"排队中"。

根因：`handleExportMany` 的成功/失败分支都用
`c.clip_id === clip.clip_id || (同房间同入出点)` 判"是哪一条"，而手动切片**不带 `clip_id`**
（`ClipSegment.clip_id` 可选，`appStore.addClip` 也不补），于是该比较是
`undefined === undefined` ⇒ 恒真。同一判据在 `handleConfirmClip` 里其实写了
`c.clip_id &&` 防护 —— 一处防住、一处没防住，是"同一判据多份手抄各自漂移"的样本。

- 抽出 `src/utils/clipExportFlow.ts`：`isSameClipEntry`（两边都有 id 才认 id，否则退回
  "同房间 + 同入出点"三元组）、`buildExportRequest`（快照走 `export_clip_by_id` 且不传区间；
  手动路径冻结墙钟/录制锚点/`content_offset` + `use_room_marks:false`）、
  `normalizeExportedRange`、`markClipsQueued` / `markClipsFailed`。
- 新增 `clipExportFlow.test.ts` 13 条行为用例，**负控 11/11 全部被抓**
  （其中 F1 就是"把身份判定还原成修前写法"，被 4 处断言抓到）。
- 详见 `docs/reports/debt-audit-20260918.md` §11.27。

### 债务收口 · 第二十批：预览重建判据搬进 `utils/previewLifecycle.ts`（报告 §11.28）

- `shouldRebuildLivePlayer(prev, next)`（只有直播 epoch 真变化才重建 LivePlayer；
  `live ↔ recording_review` 切换不拆）与 `previewPhaseCopy(phase, t)`（阶段文案与提示）
  从 `components/VideoPreview.tsx` 搬进 utils。
- 原守卫是**否定式文本断言**（`"bilibili" not in 某段`）—— 把 `probing` 与 `refreshing_url`
  的文案对调、或把重建判据少写一个条件，三条断言都还是绿的。
- 新增 `previewLifecycle.test.ts` **9 条**，**负控 8/8 全部被抓**；"平台无关"升级为
  逐阶段扫输出里有没有平台名。
- 同时给出**定性结论**（§11.28-C）：VideoPreview 余下 ~10 条守的是 React ref 生命周期与接线，
  没有可纯化的输入/输出，属于"该保留文本守卫"的一类 —— 不做为了数字好看的假迁移。

### 债务收口 · 第二十一批：MSE 缓存收进 `MseCacheStore`；一条抖动测试改成确定性假时钟（报告 §11.29）

- `useWebSocket.ts` 的 MSE init/段缓存（4 个模块级字典 + 手写全局字节计数 + TTL/单房上限/
  跨房总字节上限）收进 `src/utils/mseCacheStore.ts`，时间从参数注入。新增 10 条行为用例，
  **负控 8/10 被抓**（另 2 条经核为等价变异：公开 API 造不出悬挂时间戳；clamp 属保险）。
- 搬迁中补回一处**会被丢掉的终止条件**：跨房总量超限的淘汰 `while` 必须只在"真的还有段"的
  房间里挑候选，否则一次无效 `clearRoom` 就能让循环永不终止。
- 修一条小泄漏：防乱序 `runtime_event` 的水位表 `_lastRuntimeEventByRoom` 此前从不清理，
  房间移除后条目永久滞留；现在随 `clearMseRoomCache` 一起删。
- 全量并发跑测时暴露一条**从前绿过的**时序用例在抖（§11.29-C）：
  1) 先把看门狗预算 300→1500ms —— 立刻用变异自测，发现**放宽预算把这条判据的防线搬空**，
     该改法作废（规矩：改时间/阈值参数后必须重跑变异）；
  2) 根因是用例前提过期 —— 远定位早已改成二分探测（个位数读盘），300ms 到不到点取决于机器；
  3) 最终改为 `vi.useFakeTimers()` 且**读盘延迟也走虚拟时间**，慢发生在被测代码的时间轴上：
     基线连绿 3 次（含两次与全量 pytest 并发），去掉"有进展就顺延"的变异被抓。

### 修复（测试）：一条永远不会失败的持久化断言 + 时序用例审计（报告 §11.30，任务 #23）

- `tests/test_persistence_coalesce.py` 原断言写成
  `assert not path.exists() or load_rooms(...) == [...] or True` —— 结尾 `or True`
  让它**恒真**，"写合并窗口内不落盘"从未被验证，配套的 `time.sleep(0.05)` 也不影响任何结果。
- 重写为 5 条不依赖挂钟的用例（注入假定时器）：取消旧定时器、只留一个 timer、`delay_sec` 透传、
  待写快照是最后一次且为拷贝、到点回调真落盘且不重复落、fsync 每 N 次一次。
  其中 fsync 那条必须**包一层**真 `save_rooms`（写计数在其内部自增，整个替换会让判据失去输入）。
- **负控 8/8 全部被抓**；同样 8 条变异在改前的用例下**全绿**（即原断言有效性的量级）。
- #23 审计结论：全仓 52 处真实 sleep 按"sleep 在断言里的角色"分三类 ——
  ①事件循环 yield（保留）②sleep 是被测阈值的输入（**必须改**，pytest 侧仅 1 处即本条，
  vitest 侧 1 处已在 §11.29-C 改为假时钟）③慢是夹具主动提供的刺激（误差方向只会更强，保留）。
  把 ③ 也变成可控需要生产代码接受注入时钟，属跨模块设计改动而非测试债，已写明不做的理由。

### 债务收口 · 第二十三批：人工确认切片的判据搬进 `utils/clipConfirmPolicy.ts`（报告 §11.31）

- `handleConfirmClip` 里三段纯判据搬出：`resolveConfirmBounds`（边界优先级
  拖动游标 > 公共轴 mark > 房间 mark > 切片自身；`end > start` 才有效）、
  `selectSyncTargetRooms`（同步范围 冻结目标集 > 同对齐组 > **空**，禁止退回全量已连接房）、
  `applyConfirmedClipUpdate`（先按房间拦，再按 key/id/几何认条目）。
- 新增 `clipConfirmPolicy.test.ts` **16 条**，**负控 13/13 全部被抓**
  （含"非精修也读 mark"= 批量确认串用残留 mark、"两边都缺 clip_id 时靠 `undefined===undefined`
  全命中"= §11.27 同型缺陷的第三条）。
- 移植过程中一处**我读错了原判据**：跨组的冻结目标会被 `isUsable` 剔掉后**照旧回落到同组**
  —— 核对后确认是原实现的既有语义（同组共享时间轴），于是改用例不改代码，
  并把这条"回落"显式命名，免得后来人当 bug 顺手改掉（§11.31-B）。
- 三条受影响守卫按判据改写，其中一条在 `test_cross_feature_guards.py`（又一个"守卫不止在守卫文件"的实证）。
- `Workbench` 5,156 → 5,140 行。

### 修复（一次添加多个房间会偶发「添加失败」）

后端编排线程的同步调用槽位上限是 8，且**满了立刻抛错**。而产品允许的房间数是 12
（`max_rooms`），添加/刷新等入口又都从线程池并发投递 ⇒ 用户"粘贴 8~12 条链接一次加完"
这类正常操作会瞬时打满槽位，被变成前端可见的添加失败（日志里是
`orchestrator too busy (8 pending, max 8)`）。现在满员改为**在本次调用的超时预算内排队等待**
（等到就执行，等不到才报 `too busy`），总耗时不变、异常类型与文案不变；
既有"槽位被占死必须拒绝"的用例仍成立。详见第二十四批与报告 §11.32-C。

### 债务收口 · 第二十四批：`rooms.json` 内部写路径接形状闸门 + 编排器满员不再甩错（报告 §11.32）

- **`save_rooms` 形状闸门**：WS 入口早有校验，但**内部自动保存路径**（controller 状态变化 ⇒
  `schedule_save_rooms` ⇒ `save_rooms`）此前不做任何形状检查，脏条目能直接写进这个
  "重启后恢复房间的唯一来源"。新增 `room_payload_violation()`，违规**整次拒绝 + 记 error 日志**，
  刻意不做静默过滤（丢条目本身就是数据丢失）。判据不能严于持久层真实契约：
  身份接受 `room_id` / `room_url` / **`url`** 三键之一非空（orchestrator 与 handler 快照两种历史写法，
  恢复路径读 `room_url or url`）—— 第一版只认前两键时被全量 pytest 当场打红两条。
  新增 4 条行为用例（该文件共 10 条），**负控 5/5 全部被抓**（顶层类型 / 条目类型 / 身份 /
  空白身份 / 闸门不影响写入；`{}` 空 dict 载荷才是顶层类型的判别输入）。
- **编排器满员即时拒绝 → 有界等待**：`call()` 过去在 `pending >= 8` 时**立刻抛** `TimeoutError`，
  而产品默认 `max_rooms = 12`，房间入口又由线程池并发投递 ⇒ "一次粘贴 8~12 条链接批量添加"
  这种正常操作会被变成用户可见的添加失败。改为等到本次调用的 deadline（条件变量与计数器同一把锁，
  不做两套真相），槽位等待与结果等待共用同一 deadline ⇒ 总耗时仍 ≤ `timeout`，
  `too busy` 文案与异常类型不变，既有的 `test_call_rejects_when_pending_full` 无需改动。
  新用例 `test_call_waits_for_a_slot_instead_of_failing` **以真·回退作负控**：
  同一用例在 HEAD 的即时拒绝实现下红、在本实现下绿。
  顺带把 `test_concurrent_add_and_list_rooms` 从"HEAD 上就红 3/3"（用 `git worktree` 检出基线复现）
  变成稳定绿；该用例依赖外网解析速度这一遗留点单独记录，未靠放宽断言绕过。
- **§7.3 仍开清单 14 行逐行回代码复核**：9 行早已落地但表格未同步、1 行经核不是缺陷、
  1 行本批收口、3 行仍需外部条件（真机标定 / 搬家实测不安全 / 文案本身）。
- **门禁命令口径纠正**：从根目录跑 `mypy .` 会把 gitignore 掉的 `artifacts/` 一起查并报 6 条，
  而 CI 与 `pyproject` 的口径是 `mypy --show-error-codes` + `files = ["lsc", "python-backend"]`
  ⇒ 本地复测必须用后者。

### 变更（持续分析入口不再提供「通用直播」）

「通用直播」这一档在**持续分析**下其实没有实现：后端能力闸早就把非「无畏契约 · 回合」的组合拒掉了，
但前端入口一直把它摆成可选项 —— 用户选它、点"开始"，才收到后端报错，甚至以为功能存在。
现在入口置灰该选项并提示改选（一次性/多房同步分析不受影响，通用档照旧可用）。
判据是 `src/utils/analysisProgress.ts:isContinuousAnalysisModeAllowed`，UI 与提交校验共用同一份。
（用户 2026-09-19 决策；见报告 §11.33-A）

### 债务收口 · 第二十五批：用户三项决策落地（#21 通用档、#16 发布编号、#15 授权边界，报告 §11.33）

- **#21**：如上"变更"条。判据进可测层 + vitest 3 条 + 接线守卫 1 条（守卫 135 → 136）。
  顺带撞到一条自己的老守卫逐字钉 JSX 排版（加 `disabled` 就红），改为不钉排版、只守"档位显式存在"，
  判据交给 vitest —— §11.21 的规矩第二次兑现。
- **#16 发布编号定线 1.x**：CI Release integrity 改成三条 —— ① tag 必须 `v1.x.y`；
  ② **倒退只在同线 `v1.*` 判定**（旧口径拿全标签比，会被已废弃的 v3.0.23 永久拦死 1.x）；
  ③ 跨线更低时 `::warning::` 提醒而不是失败。
  **执行时掉出两个新事实**：(a) 1.x 线上其实有 `v1.5.0`（2026-07-02），报告原先手写的
  "标签全集"漏了它和 `v1.0.0` ⇒ `v1.0.18` 连同线都不合（下一号定为 **v1.6.0**，
  本 CHANGELOG 顶部那节已改名）；(b) 版本号写在三处且两处是旧的
  （`package.json=1.0.17` / `pyproject.toml=1.0.16` / `package-lock.json=1.0.11`），
  旧守卫只比第一处 ⇒ "发版没同步"根本不可见。现在三处一起比、不等即拒绝发布，
  并把后两处同步到 1.0.17。**四条判定路径全部实跑验证**（把 YAML 里的脚本原样抽出来执行），
  不是读代码判断。已知并接受的后果：装过 v2/v3 的用户仍收不到 1.x 更新，
  要救他们得改更新判据（另一次独立改动）。
- **#15 仓库瘦身授权**：**记为已授权但未执行**。改写历史不可逆，前置条件按顺序写在 §11.33-C：
  本分支 24 批未提交改动必须先落 commit → 装 `git-filter-repo` 并做经校验的 `--mirror` 备份 →
  `gh auth login` 后核对远端 tag/分支 → 先比较"把数据集挪出去"（submodule / release 资产 / LFS）
  是否比改写历史代价更小。本轮未运行任何不可逆命令。

### 修复（停录超时不再被答成"停止录制失败"）

现场（录制 32 分钟后停录）：`16:17:22 下发停止 → 16:17:32 跨线程调用超时 → 16:17:34 录制才真正合段定稿`。
旧代码在异常分支无条件回 `success=False`，于是**录像完好定稿、用户看到"停止录制失败"**。
现在超时后先回查真实录制状态（8s 窗口，依据是实测"超时后 2s 定稿"）：确实还在录才答失败，
已经停好就答成功并留一条 warning。回查走**不经 `call()`** 的 `get_recording_status` ——
用 `get_room` 会在编排线程正忙时跟着一起超时，"读结果"变成第二个故障点。
顺带：停止响应里读取输出路径的那次 `get_room` 原先直接跑在 asyncio 事件循环线程上
（最坏把整个后端卡住 10 秒），现改为进线程池且读不到只回空路径。
（同一条判据启动侧早已修过、停止侧漏了，现已提成一份共用。）

### 债务收口 · 第二十六批：现场日志掉出的"假失败"收口（报告 §11.34）

- 触发方式：应用户要求边验边盯日志，watcher 抓到 `orchestrator call timed out` 与
  紧随其后的"其实成功了"证据链 —— 不是读代码想出来的。
- 同一批把 `read_room_recording_status` 提为模块级、启动侧闭包改为委托它（不留第三份手写判据）。
- 新增 `tests/test_stop_recording_false_failure.py` **10 条**，**负控 7/7 全部被抓**
  （不回查 / 一律乐观 / 不认房间移除 / 读不到当已停 / 绕过快照 / 退回丢口径 / handler 退回旧写法）。
  轮询预算与间隔都是注入参数，不制造"sleep 当生产阈值"的用例。
- **明确未闭环**（§11.34-D）：① 超时后 in-flight 的**返回值**仍拿不回来（只回查了状态），
  "结果未知无重试语义"这条只收了一半；② 持续分析 worker 在停录期间撞超时会被记成
  `持续分析异常 / 异常退出`（应为预期噪声降级），本轮未动以免把真异常一起静音；
  ③ 同一秒出现两条字节相同的 `kick worker`，待单独查。
- 顺带确认修好的部分**在现场成立**：`sidecar 已随录像定稿改名` 后紧接
  `已加载分析结果: <同一定稿名>`（§11.3）、退出后无 python/ffmpeg 残留进程（P0 退出链）、
  `rooms.json` 形状闸门 0 次误拦（§11.32）。

### 修复（持续分析被自家派生闸拖慢：吞吐 0.93× → 0.42× 的根因）

本分支第 8/17 批加的全局派生闸（`CLAUDE.md §7.4` 的"太多 FFmpeg 同时启动把机器压过临界点"）
误接到了短命令统一入口 `run_hidden` 上：`with spawn_slot(...)` 包住 `subprocess.run`，
**名额被持有到整条命令结束**，而闸自己的设计边界写的是"名额只覆盖启动瞬间"。
持续分析正好要发大量 ffprobe/抽帧调用，于是一次几百秒的抽帧就占满 2 个名额，
后面每个探测各白等 5s 才 fail-open。实测（名额被 2 条长命令占满、3 个短探测）：
**接闸 15.03s vs 不接闸 0.25s（≈59×）** —— 与现场 `scan_throughput` 腰斩、backlog 涨到
~580s、单场 3 次 270s 扫描超时对得上。现在短命令**不再占名额**（改为只计数，
`budget_snapshot` 新增 `transient_spawn_total` / `transient_by_category`），
进样启动/换线也改成"削峰不否决"（`fail_open=True`；此前硬拒会落成
`upstream ffmpeg start failed` + `failure=TIMESTAMP_DISCONTINUITY`，代价是丢一段录制）。
预览/录制/导出的启动封顶保持不变。详见报告 §11.35。

### 债务收口 · 第二十七批：把持续分析调回 main 的水平（报告 §11.35）

- 范围严格限定在"分析热路径不再被闸拖慢"；`main` 只读未动（`main` == HEAD == `dbeb707d`）。
- 定位用两个只读子代理并行比对 `git diff main` 的处理链/支撑链，回来逐条自核：
  analyzer 侧（回合合并、开放尾回合、入列判据、worker、窗口规划、OCR 阈值与采样率）
  **与 main 逐字节相同**；`lsc/config.py`、`resource_monitor.py` **零改动**；
  `sound_detector`/`observer_scoreboard` 在 main 上本就是死桩 ⇒ 都不是退化来源。
- `tests/test_process_spawn_budget.py` 8 → **11 条**，**负控 4/4 全部被抓**
  （短命令重新接回闸 / 不再计数 / 漏出 `spawn_category` / 进样改回硬否决）。
  断言之一是"**从未 acquire 过闸**"，而不是"acquire 不到也放行"——后者正是本次修掉的错误契约。
- 顺手删掉因此失效的死常量 `_TRANSIENT_GATE_SEC`，并改正一处误导注释
  （原写"capture/短命令路径用 fail_open"，短命令已不经过闸）。
- **明确保留的差异**（不悄悄留着）：`valorant_broadcast.py:2343/2812` 的
  `available_end is not None` 是防 `float(None)` 抛错，若日后发现尾部回合比 main 少，第一个查它；
  `mse_streamer.py:473` 预览启动 10s 硬拒不在分析热路径上，改它是独立决定。

### 质量门禁

- 后端 **pytest 2247 用例全绿**（本分支新增约 230 条，代表项：门禁守卫、派生预算、
  校验同源、注册表回收、新鲜度判据、停机链、平台可观测性与自检、广播序列号回归、
  导出/对齐纯逻辑单元 30 条、持续分析能力闸 4 条、广播节流契约 4 条、
  `align_preview_audio` 路由级 harness 12 条、导出链路由级 harness 13 条、
  取标量口径 `tests/test_coerce.py` 30 条；
  2134 → 2129 → 2126 → 2132 → 2166 → 2170 → 2182 → 2195 → 2225 → 2224 → 2228 → 2233 → 2234 → 2244；
  最后三步 +5 = 第二十四批（`rooms.json` 形状闸门 4 条 + 编排器槽位等待 1 条）、
  +1 = 第二十五批（持续分析入口类型判据的接线守卫）、
  +10 = 第二十六批（停录"假失败"回查链，负控 7/7）。
  中间的下降分别是第六批下线 5 条文子守卫、第八批删掉只测死代码的 `test_segmented_recorder` 3 条，
  第十六批下线 1 条（`isApproximateClip` 判定改为行为用例）；此前删除的
  只测死代码用例 54 条不复活：`test_mse_sender` 21、`test_core_export_service` 30、
  `test_valorant_observer_scoreboard` 8、`test_cancellable_ffmpeg` 4 等，活逻辑覆盖零损失）。
- 前端 **vitest 34 文件 / 412 用例全绿**（+1 预览冻结可诊断性、+3 稀疏检查点定位、
  +17 MSE 播放器行为用例、+8 一键对齐落点用例、+1 回看读盘上界、+1 发件箱溢出、
  +9 预览音频收尾链数值用例（负控 5/5，含一条"没牙"用例被负控抓出后补强）、
  +28 持续分析状态推导用例（负控 11/11）、+12 切片定位口径用例（负控 8/9，1 条等价变异）、+6 导出摘要聚合用例（负控 7/7）、
  +11 手动切片构造用例（负控 9/9，并修掉跨房跳号）、+13 导出请求与乐观更新用例（负控 11/11，修掉列表项串写）、
  +9 预览重建与阶段文案用例（负控 8/8）、+10 MSE 缓存有界存储用例（负控 8/10，2 条等价变异）
  并把 1 条靠 sleep 制造慢的时序用例改成假时钟（确定性）、
  +16 人工确认切片判据用例（负控 13/13）、+3 持续分析入口直播类型判据用例；
  4 条墙钟预算用例改为条件等待）；
  后端文子守卫 136 条全绿（第六批下线 5 条、第十六批下线 1 条，另有 4 条按新行为用例瘦身，
  其中 1 条经核实是被注释满足的，见 §11.23-C）；
  `tsc --noEmit`（`tsconfig.json` 与 `tsconfig.electron.json` 两个工程）均无错误。
- **新增门禁：Electron 主进程类型检查进 CI**。实测 `npm run build` 与 CI 的 typecheck job
  都只跑默认工程（`include: src`），`tsconfig.electron.json`（`electron/**/*.ts`）
  从 CI 到打包前**从未被类型检查** —— 窗口/托盘/后端子进程/单实例锁这条链没有静态防线，
  而 CLAUDE.md §13.2 一直写着"两个工程都要过"。当日实测该工程 0 错误，补门禁不引入存量债。
- **`ruff check .` 全仓 All checks passed**（口径从 `lsc/ python-backend/` 扩到含
  `tests/` 与 `scripts/`；pre-commit 钩子的 files 正则同步扩，三处同源同版本 `0.15.16`）。
- **`mypy --show-error-codes` 阻塞且覆盖 103 文件**（lsc + python-backend；随死模块与两个 dev 脚本
  移出由 105 → 104 → 102 → 103），Success；存量以 `pyproject` 的"模块 × 错误码"棘轮精确豁免，
  关掉全部豁免实测由 **355 → 282 → 245 条**；第十四批之后 `arg-type` 已归零、
  **`lsc/` 侧一条豁免都不剩**（15 处豁免全在 python-backend，剩 245 条纯签名缺失，
  需要 handler 层的 payload/返回契约才能收，见报告 §11.6 / §11.22）。
- 覆盖率合并口径 **62% → 64%**，CI 地板 55（`--cov-fail-under` 阻塞）。
- handler 侧真断言补齐：`export_handlers.py` 16% → **84%**、`alignment_handlers.py` 16% → **70%**
  （两条 P0 入口都建了路由级 harness：校验/失败形状/回调与广播/取消/状态查询形状）。

## v1.0.17 (2026-09-16)

### 变更（回放交互口径：能点的范围 = 真能回放的范围）

2026-09-15 真机日志（20:22:08 `[Workbench] seek 283.1s 超出直播缓冲 [308.1, 465.2] → 切换到本地文件回看`）
暴露的叠加问题：设置 `timeline_replay_seconds=300` 时真实 MSE 连续缓冲只有 157s，而放大预览条把
「设置窗口」（`liveEdge − 设置时长`）整段画成可点区域 ⇒ 点在画出来的窗口内、真实缓冲外的位置会被
`mseSeek` 判成缓冲外并切到本地文件回看通道，预览区一直显示「正在准备回看…」（一次普通点击白等数秒）；
条上还同时印着「回看设置 X · 实际可回放 Y」两个时长，与实际能点的范围并不一致。

- **放大预览条可点/可拖范围 = 真实可回放范围**：`computeExpandedPreviewWindow` 左端改为
  `max(真实缓冲起点 buf.start, liveEdge − 设置时长)` —— 用户设置**只作上限**，绝不把未缓冲的历史
  画成可点区域；指针拖动与方向键两处落点统一过 `clampSeekToRange` 收进缓冲内侧。
- **`mseSeek` 边界容差**：新增 `isWithinSeekRange`（`DVR_BUFFER_EDGE_TOLERANCE_SEC = 2s`），容差内的
  落点按缓冲内处理并 `clampSeekToRange` 收进缓冲内侧；分片边界/浮点误差不再把一次普通点击推给
  重量级的回看通道。更早的内容仍走本地文件回看（主时间线 hover 照旧提示「缓冲外·将从录制文件回看」）。
- **删除放大预览区的两个时长文案**：「回看设置 {configured} · 实际可回放 {available}」及其内存降级后缀
  一并移除（连同 `room-card__expanded-replay-info` / `expanded-unavailable` 死样式）；
  窗口计算不再返回 `configuredReplaySeconds` / `availableReplaySeconds`。主时间线紫标/文案不变
  （起点 = 真实缓冲起点、时长 = 真实可回放量，设置承诺窗口另画淡虚线）。
- **守卫**：`tests/test_frontend_stability_guards.py::test_preview_bar_seekable_range_equals_replayable_range`；
  夹具 `lsc-electron/src/utils/timelineWindow.test.ts`（两种窗口口径 + `isWithinSeekRange`/`clampSeekToRange`）。
  同时修正三条已失效的旧守卫（`const dvrStart = useMemo` → `dvrReplay` 重构名、`bufEnd - 0.5` 字面量、
  主时间线「紫标无文案」旧约定）。

### 新增（放大预览：缩小为窗口播放 = 原生画中画）

- **放大态控制条新增「窗口播放」按钮**，紧挨「缩小回网格」之后（顺序：静音 → 缩小回网格 →
  窗口播放 → 全屏 → 停止预览），与其它覆盖层玻璃按钮**同一设计语言**（同 small 尺寸、同圆角
  令牌、同毛玻璃），差异化为：画中画图标（外框 + 右下角实心小窗，刻意不用向内收拢的
  `ShrinkOutlined`／向外发散的 `FullscreenOutlined` 箭头语汇）+ 文字标签「窗口播放」+ 品牌青描边
  淡青底，激活后整块转品牌色。原「缩小」tooltip 由「缩小（窗口播放）」改为「缩小（回到网格，
  不改变播放）」，消除两个按钮的语义撞车。
- **功能走播放器原生能力**（`requestPictureInPicture`，Electron/Chromium 原生小窗可跨应用常驻，
  不需要把 MSE 分片再喂一份）：状态机在 `src/hooks/usePictureInPicture.ts`——画中画是 document
  级单例，故按 `document.pictureInPictureElement === 本房 video` 判定并监听 document 的
  `enter/leavepictureinpicture`（绑在 video 上会在播放器重建后失效）；取**当前通道**的 video
  （回看时跟随回看播放器）；卡片卸载时若本房仍在小窗里则 `exitPictureInPicture()` 收口；
  `requestPictureInPicture()` 需要用户手势且视频须已有画面，失败给出提示而不是静默。
- **一次点击 = 缩小 + 窗口播放**：进入小窗成功后顺带收起区域放大（同一路画面不该在卡片与小窗
  里各播一份）；退出小窗只退小窗，不重新放大。按钮位于自动隐藏控制条内，跟随向下隐藏行为。
- **守卫**：`tests/test_frontend_stability_guards.py::test_window_playback_button_uses_native_pip`；
  夹具 `lsc-electron/src/hooks/usePictureInPicture.test.ts`（支持性探测 / 进出切换 / 单例语义 /
  失败回报 / 卸载收口，共 7 条）。

### 变更（放大预览底部控制条：一体化 + 自动隐藏）

- **时间线 + 走带按键合成一块面板**：原先时间线一行、按键一行各自留白与底色，读起来是两段；
  现在合并为同一块玻璃面板（统一样式见 `Workbench.css` 的 `room-card__expanded-controls` 与
  `room-card__expanded-actions`）。
- **默认向下隐藏，鼠标经过/移动滑出，静止 2.5s 自动收起**：显隐状态机统一走新增的
  `src/hooks/useAutoHideControls.ts`；拖动时间线、画质下拉打开期间 `pinned` 钉住，键盘焦点在条内
  由 CSS `:focus-within` 兜底。隐藏态用 `translateY(100%)` + `opacity:0` + `pointer-events:none`
  三重收口（不可见、不可点、不挡画面；卡片 `overflow:hidden` 裁掉滑出部分）。旧「挂载即滑入且
  常驻」动画 `roomCardControlsSlideUp` 删除，并补 `prefers-reduced-motion` 关闭过渡。
- **显隐信号挂在 `<Card>`**（预览画面 + 控制条的公共祖先）：挂预览容器上时，指针从画面移向控制条
  会先触发 `pointerleave`，把条在用户伸手去点的那一刻藏掉。
- **守卫**：`tests/test_frontend_stability_guards.py::test_expanded_preview_controls_autohide_as_one_panel`；
  夹具 `lsc-electron/src/hooks/useAutoHideControls.test.ts`（默认隐藏 / 空闲收起 / 移动重置 /
  `pinned` 钉住 / 退出放大复位 / 卸载清定时器，共 6 条）。

## v1.0.16 (2026-09-13)

### 修复（全功能真机验收抓出的四处缺陷）

2026-09-13 上午 computer-use 全链路真机验收（EDG夺冠回顾 huya 29701502 broadcast 分支 +
Gus douyin 6096197105 LEGACY 分支），四个真实缺陷夹具先行修复：

- **设置缓存毒化（P0，连接全挂）**：`handle_save_settings` 在 `save_settings()`（内部已刷新
  `_settings_cache + _settings_cache_mtime`）之后又 `_settings_cache = None` 且不清 mtime ⇒
  下次 `load_settings()` 命中「mtime 相等」捷径**永久返回 None** ⇒ 之后 connect_room 等一切
  读设置的请求全部 `'NoneType' object has no attribute 'get'` 崩溃且不自愈（前端任何一次
  设置保存即触发）。修复：捷径加非空守卫 + 失效处同时清 mtime。
  夹具 `tests/test_settings_cache_poisoning.py`（改前 2 红）。
- **回看远目标定位判死**：本地文件回看的有界定位按头部码率外推，码率前低后高时大幅欠冲，
  顺序扫描追赶期间固定 8s 首帧死线把整个会话判死（`回看定位超时`），播放头钉死只能手动重试。
  修复：死线改为**停滞看门狗**——读盘/建索引/入队任一进展即顺延，真停滞才按原诊断口径报错；
  出画前读取块放大 4× 加速追赶。`localFileMseSource.test.ts` 新增双码率文件
  +节流读盘回归（改前红），原"读不到数据必须报错"用例保持绿（真停滞语义不变）。
- **草稿重叠去重未对账**（`skipped_unaccounted=4` ≠ 0）：收尾补扫合成出与既有切片重叠的
  重复候选，placement 阶段被 SegmentOverlap 拦下后只写 warnings 不进 `excluded_clips` ⇒
  响应残差字段违约。修复：该分支逐条进 `excluded_clips`（新 `reason_code=OVERLAP_DEDUP`，
  带 start/end），响应 `skipped` 明细与 `skipped_unaccounted` 恢复恒等 0。
  内容此前即无损失（草稿 6 段与审计精确出点逐一吻合、不变量审计 passed）。
  夹具 `tests/test_broadcast_draft_overlap_accounting.py`（改前红；红线：不放宽
  `clip_source_usable`/`_broadcast_gate_passed`）。
- **切片标签撞号（两层根因）**：其一，upsert（边界精修）重新入列时不记忆首发标签；
  其二（第二场真机复验才暴露的根因），`format_ai_round_clip_label` 的序号用的是分析器
  `round_index`（跨扫描批次会重复）而 per-room 单调计数器被 `_ = index` 丢弃 ⇒ 不同回合
  也撞号（现场两场各出现两条 R02）。修复：标签序号改用 per-room 单调计数器
  （`_resolve_ai_clip_label` 按 listed_key 记忆首发标签，upsert 复用；epoch 清理同步裁剪；
  无记忆 upsert 退回当前计数器；index<=0 兼容回退 round_idx）。副作用改善：导出文件名
  （`{主播}_R{NN}.mp4`）不再互相覆盖。夹具 `tests/test_ai_clip_label_upsert.py`。
- **平台抓取统一默认直连**：`build_opener` 默认 ProxyHandler 与
  `douyin_record._SSRF_SAFE_OPENER` 的 `ProxyHandler(getproxies())` 会隐式采用
  env/注册表系统代理——本机注册表代理指向已死端口（127.0.0.1:8780）时虎牙/抖音解析
  集体报 [WinError 10061]（文案"网络错误"，极易误判为房间问题）。修复：两处默认 opener
  显式空 ProxyHandler 强制直连，**显式 scoped proxy（network_context）路径不变且加测试钉住**。
  夹具 `tests/test_platform_fetch_direct_default.py`（哨兵代理 + reload，机器无关红绿）。
  行为变化：系统代理不再被平台解析隐式采用；需要代理的用户走设置内的显式代理配置。


## v1.0.15 (2026-09-11)

### 修复（赛事草稿「已定稿切片被静默丢弃」）

现场：20:45:21 导出草稿，请求 8 条只写入 3 条；`round-000105` 在 20:43:26 已由赛事审计定稿
（`audit=passed` / `end_by=broadcast_exclusion` / `end_quality=precise`）仍被跳过，且 5 条跳过
共用一句「未确认/近似定位/未通过赛事审计」。完整清单与回归夹具见
`docs/reports/broadcast-draft-silent-drop-plan-20260911.md` 与
`tests/fixtures/broadcast_export_case_20260911_2045/`。

- **终态权威快照跨会话保留**（`_last_authority_snapshots`）：收尾任务态被 pop 前把
  `listed_clips + rejected_round_keys + recording_id` 留下，导出侧在活跃任务之后回落读取。
  此前 pop 与导出相隔 1 秒，权威回落到 20:37 的旧分析 sidecar，把已定稿切片改回
  `pending_lookahead` 后按「未确认」跳过。新录制 epoch / 删房时清除。
- **扫描通路终态补投影**（`_project_scan_audit_terminals`）：扫描路径的审计结论此前只进
  `listed_clips`，`accepted/rejected` 账本与收尾 sidecar 都看不到（现场 8 条结论只有 4 条落盘）。
  现按与精修通路同一映射补齐、按 `round_key` 幂等，扫描结果已入列故同时计 `delivered`。
- **权威注册表接线**（实测发现）：`register_jianying_handlers` 的调用点从未注入
  `_continuous_tasks/_analysis_jobs`，两者在生产里一直是空 dict ⇒ `listed_clips` 权威补全
  与审计字段合并全是死路径。现注入三个注册表并加守卫。
- **跳过原因可辨**：新增结构化 `reason_code`（`END_NOT_FINAL` / `NEVER_AUDITED` /
  `NO_EXCLUSION_EVIDENCE` / `NOT_IN_AUTHORITY` / `REJECTED`），响应新增
  `skipped:[{round_key,label,start,end,reason_code,reason}]`，草稿结果弹窗逐条展示
  （请求/写入/跳过计数 + 明细）。
- **归档改名后落盘路径同步**（`_sync_analysis_save_path`）：改名后循环仍持旧路径，
  20:39:27 那次落盘写回 `…_录制中.analysis.json`，「至_」文件的分析快照被冻在 20:37
  （缺 `round-000135`），导出时它被判「不在权威集合」。现落盘前以房间当前录像为准。
- **收尾完成判定收紧**（C6）：`pending_audit` = 「待审计队列非空 **或** 仍有已入列切片
  无终态归属」；有界兜底对后者落 `manual_review` 终态（不删除、不放宽门禁），
  避免「队列空 + listed 无归属」让收尾无限重跑（现场 20:45:20 判定瞬间 135 刚出结论）。
- **回归夹具与不变量**：`tests/fixtures/broadcast_export_case_20260911_2045/`（真实 sidecar +
  请求原文 + 权威快照 + provenance）+ `tests/test_broadcast_export_authority_lifetime.py`（11 条，
  改前 included=3 / 改后 4）；`scripts/audit_continuous_analysis.py` 新增 no-silent-drop
  三条不变量（结论↔终态、listed 归属、草稿口径与跳过可辨），失败返回非零。
- **离线复算工具**（只读）：`scripts/valorant_vision/reaudit_broadcast_candidates.py`，
  报告 `docs/reports/reaudit-2045-20260911.json` 给出结论——135 即便给足 254s 后视素材，
  审计仍只能给 `next_prep/coarse` 出点（**不可定稿**，人工确认是正确出口）；
  076 实为无效候选（离线判 `rejected_no_stable_combat_start`）。
- **L3 真实环境验收（2026-09-12 08:44–09:01）**：16 分钟录制 + broadcast 持续分析，
  收尾正常收敛；夹具 C 三条不变量全部转绿（`docs/reports/live-verify-0901-20260912.json`），
  日志可见「扫描通路终态已补投影」「终态权威快照已保留」「未定稿切片落 manual_review」；
  两次草稿（程序自动 + 驱动）都带逐条跳过原因码；归档后不再产生旧名 `_录制中` sidecar。
  当轮修掉两个小问题：权威校验阶段的拒绝被判成 `NEVER_AUDITED`（切片 dict 仍带陈旧
  `pending_lookahead`）→ 判据补文案分支；墓碑原因取到入点门禁的 `"ok"` 导致「已拒绝(ok)」
  → `_rejection_reason()` 优先取审计结论。
- **草稿静默丢弃清零（2026-09-12 09:01 现场，round-000065 门）**：三层缺陷
  ①权威 `clip_id` 随边界派生，定稿后与请求里的旧 id 不一致 ⇒ stock `honor_clip_ids`
  把"刚精修好"的切片静默丢弃（现同时接受合并前的原始 id，真不在清单里也补逐条留痕）；
  ②`resolve_common_range` 优先读 `recording_start/end_sec`，而 reconcile 只改 `start/end`
  ⇒ 权威精修出点被前端旧值顶掉、草稿带进 12s 赛后内容（现按同一轴回填别名）；
  ③导出器 `clip_source_usable` 过滤只留本地计数 ⇒ 差额无法逐条对账（现逐条进
  `excluded_clips` + 逐条 warning，响应新增 `skipped_unaccounted` 残差字段，恒等 0 才算干净）。
  夹具 `tests/fixtures/broadcast_export_case_20260912_0901/` + 回归
  `tests/test_broadcast_draft_silent_drop.py`（修复前 kept=3/残差 1 → 修复后 kept=4/残差 0）。
- **同名草稿不再互相覆盖（2026-09-12）**：自动命名只精确到分钟，同一分钟内的两次导出会撞名
  （09:01:48 的 4 段自动草稿被 09:01:54 的 3 段手动导出顶掉）。现自动命名避让为 `_2`/`_3`…
  并给出「本次写入 … 以免覆盖上一份」告警；**显式命名仍覆盖**（前端"重试生成草稿"的既定语义）。
- **列表逐条标注「为什么没进草稿」（2026-09-12）**：持续分析切片在列表里显示
  `可导出 / 待审计 / 需确认 / 已排除 / 不可导出` 状态标签（含 tooltip 说明），
  色轨也同步区分（此前待审计的切片同样亮"可导出"青色，用户到导出才发现少了几条）。
  实测背景：审计吞吐 ≈2 分钟/条 > 收尾可用时间（91s），047/063 这类只能落 manual_review；
  离线复算证明拉长收尾救不回（135 给足 254s 后视素材仍只能拿到 `next_prep/coarse`）。
  状态码与后端 `_skip_reason_code` 同族并有 parity 守卫。
- **红线**：不放宽 `_broadcast_gate_passed`。出点未定稿 / 无排除证据 / 被拒的切片继续被拒，
  只是原因从一句聚合告警变成可定位的分类。

## v1.0.14 (2026-09-11)

### 修复（赛事切片导出被「必须人工确认」卡住）

现场：持续分析跑完，切片列表里的回合出点已由视觉审计定稿（`broadcast_exclusion` /
`end_quality=precise` / `audit=passed`），但点「导出」「导出全部」都被拦；必须先在列表里
点开该条、再点「确认」把状态改成 `user_confirmed` 才能导出，否则弹「该赛事切片边界仍在
审计/复核中」。根因是前端 `canExportClip` 只看聚合复核标记：持续分析的赛事切片入点仍是
coarse 的 OCR 战斗锚点，`broadcast_review_required`（判据含入点密扫证据）与
`boundary_review_required` 必然为 true，于是整条切片被锁死；而后端草稿门禁
（`_broadcast_gate_passed`）早已按「出点定稿即可入草稿」放行——两条门禁判据不一致。

- **前端导出门禁对齐后端**：`clipExportPolicy.canExportClip` 新增
  `hasAuthoritativeBroadcastEnd`（`audit=passed` + `end_quality=precise` +
  `end_review_required≠true` + 无时长异常 + `end_by ∈ {next_prep, broadcast_exclusion}`），
  命中即视为可直接导出/入草稿；出点定稿后后台只可能再改入点，导出文件最多「起得略早」。
  覆盖 `pending` / `refining` / `vision_confirmed` 三种会话态——`refining` 是用户点开切片
  进入精修（`begin_refine_clip` 广播）的会话态，不是「边界不可信」，导出用的仍是该条已入列
  的边界（预览弹窗显示的入出点即写入文件的范围）。被拒终态（`rejected_*`）依旧不复活，
  未定稿出点（`open_tail` / `next_combat` / `end_review_required` / 时长异常）仍需人工确认
- **两端判据钉在一起**：新增 `tests/test_broadcast_export_gate_parity.py`，
  前端 `BROADCAST_VALID_END_BY` 必须等于后端 `jianying_draft` 与 `room_handler` 的同名集合，
  并用真实运行样本（R01 出点定稿 / R04 未定稿）在两侧断言同一结论；前端
  `src/utils/clipExportPolicy.test.ts` 同步补真实样本用例

## v1.0.13 (2026-09-11)

### 修复（官方解说分支持续分析：审计存活性与结论交付）

现场：32 分钟真实会话里 `audit_terminal_total = 0`，`边界审计超过预算` 12 次、每次交付 0 条，
切片列表长期停留未审计的粗边界（纯回放片段、跨回合 9 分钟片段、半路截断）。实测单步
41.8s（冷）/21.4s（热）> 20s 墙钟预算，其中超长候选的门禁预取一次性解码 ≈514 帧 ≈23s。

- **取消路径结论不丢**：`audit_broadcast_rounds_with_outcomes` 新增 `outcome_sink`；审计被
  `cancel_check` 中断时，已定稿的拒绝结论照常交付（剪除已入列脏切片），未判定候选补发
  `pending` 保持批次完整（否则消费端会把残缺批次当整批终态、静默丢弃未审计子候选）
- **取消路径不交付半成品 accepted**：`accepted/manual_review` 在预算耗尽时降级留队，
  下一轮由审计缓存复现后走完整路径（保持「accepted 必带入点密扫」不变量）
- **在线微步骤一轮只推进一个分裂子块**：超长候选（>150s）分裂出的 4 块一轮跑完实测
  ≈40s 远超预算；其余子块以 `pending` 交回队列续扫。实测同一 551.8s 候选 4 轮收敛
  （9.2s / 8.4s / 8.3s / 6.0s），全部终态、计数不虚增
- **在线预取受媒体预算约束**：分裂块门禁预取窗口由 150s 截到 `max_media_step_sec`（18s），
  预取只是批量加速，判定窗口与语义不变
- **分裂块整段起扫（画面质量）**：超长候选切成的固定块只扫尾部 30s，块中部的回放/回合
  边界看不到 → 实测出现「切片跨两个回合 + 含 22s 回放」仍被判 passed。现改为分裂块从块头
  整段起扫（单次仍按 18s 微步骤），并补两道门：块内「回放后接下一回合满钟」不再被
  `_has_decreasing_combat_after` 硬否决；块头静态画面不得触发逐帧冻结兜底（避免整块被
  `no_active_span` 拒绝）。真实录像离线复现：同一 203s 候选 s0 由 `310.4-460.4/next_prep/coarse`
  变为 `310.4-414.25/broadcast_exclusion/precise`（截在回放起点 + 2.5s 结算尾巴）
- **审计任务卡死可观测**：新增「边界审计任务 N 秒未推进（疑似卡死）」节流告警（现场 21 分钟
  0 帧推理 0 交付时无任何日志，py-spy 抓到粗扫线程卡死在 rapidocr/onnxruntime 推理内、
  持有的共享 ONNX 信号量把审计一起锁死——属 onnxruntime 原生问题，本次只补可观测性）

## v1.0.12 (2026-09-10)

### 修复（持续分析 → 剪映草稿链路）

- **新录制 epoch 隔离**：持续分析启动按 `recording_id` 判定新 epoch，自动清理上一会话的
  `listed_clips` / 键位登记 / 精修冻结，旧切片不再混入新录制的权威快照
- **审计拒绝同步清理**：broadcast 审计拒绝终态立即从权威切片快照移除并广播
  `clip_confirm_status=rejected`（前端同步删除）；此前被拒回合残留为 pending_lookahead
  并混入剪映草稿（拒绝切片被收录、精修通过切片反被重叠跳过的根因）
- **剪映草稿权威校验**：`generate_jianying_draft` 对每个切片做
  `recording_id + round_key + 当前 sidecar` 三重校验；sidecar accepted 终态的
  边界/审计字段以 sidecar 为准，旧会话遗留/被拒切片直接跳过并给出原因告警
- **included_clip_count 口径修正**：按实际写入切片轨的段数统计（`placed_clip_count`），
  同轨重叠被丢弃的切片计入 skipped，不再出现「请求 8 / 包含 8 / 实际写入 7」
- **收尾覆盖账本同步**：收尾判定与 sidecar 落盘前从任务状态权威 payload 重建
  FinalizationJob；修复本地旧对象把 coverage 账本清空导致的
  `coverage_complete=true` 却反复「继续补扫」死循环
- **WS 1000 (OK) 降级为 DEBUG**：客户端正常关闭不再被记录为后端 ERROR
- **权威校验收紧**：删除「recording_id 一致即兜底放行」分支——`round_key` 必须命中
  `listed_clips` / sidecar `accepted` / `rejected` / `pending` / 分析结果之一才进入草稿，
  防止 `recording_id` 恰好一致的旧会话/脏切片混入
- **权威集合补全**：前端列表可能遗漏当前 epoch 已入列回合（断连重载、`clip_queued` 丢失），
  后端按任务 `listed_clips` 补建切片源并给出告警；补入项同样过全部门禁
  （rejected/pending 不复活），可传 `fill_authoritative=false` 关闭

### 修复（收尾卡死 / 导出门禁 / DVR 显示）

- **收尾无限补扫 / 卡死修复**：录制文件定格后，后视窗口结构性不足
  （`scan_end` 超出可用末尾）的候选不再无限滞留待审计队列——审计
  `finalize` 阶段直接按无帧判定终态（不再返回 `pending_lookahead`）；
  收尾时对「候选终点已到/超出文件末尾」的候选强制尝试一次终态审计；新增
  收尾补扫有界兜底 `_FINALIZE_TAIL_STALL_MAX_ROUNDS`，结构性无法定稿的候选
  强制定稿并广播，收尾必定收敛（此前会无限「继续补扫」，界面表现为卡死、
  无法停止，且不产出可验收切片）
- **DVR 紫线按配置时长展示**：主时间线紫线左界不再钳制到 MSE 连续缓存起点
  `buf.start`，改按用户设置的回放时长展示（超出缓冲的点击/拖动由 `mseSeek`
  自动切录制文件回看），修复「可回放时长明显少于设置值」
- **导出拦截提示优化**：赛事切片未完成审计/复核时，提示明确指引点击
  【确认导出】完成人工复核后导出
- **DVR 交互：播放头紧贴回放光标**：点击/拖动到 DVR 左界（紫线）以左时，播放头
  钳到紫线（不再落到紫线左侧）；紫线以左的媒体由录制文件回看（recording_review）
  承接，因此不会重演「画面卡死」；紫线右侧仍可实时查看缓存内容。紫线归零
  （录制/回放时长不足配置值）时不设下界，整段可用
- **预览时钟周期性重标定**：`recording_to_preview_delta` 由「首播标定一次」改为
  低频周期重采样（60s，`PREVIEW_CLOCK_REFRESH_MS`），跟踪网络抖动引起的缓冲深度
  变化，避免预览时钟长期漂移导致时间线显示的录制秒与后端真实时刻错位
  （重采样失败不改变已接受 delta，安全）
- **预览低延迟编码**：CPU 回退路径（libx264）补 `-tune zerolatency`——此前只有
  NVENC 路径有 `-tune ll`，软编预览因此明显落后直播；muxer 增加
  `-flush_packets 1`，分片生成后立即写出管道，不再做内部缓冲

### 清理（删除 PySide6 遗留死代码）

- **删除 `lsc/gui/`**（8 文件 / 1487 行）：`MultiRoomManager`（委托 `RoomOrchestrator`
  的 Qt 薄门面）、`RecordingController`、`common_workers`、`qt_compat` 与
  `multi_room/session.py` 兼容转发层。该包在 `__init__.py` 中已自述弃用，
  生产链路（python-backend）零引用，仅被测试引用
- **删除 `lsc/cli.py`**（283 行）：无 `__main__` / argparse 入口，唯一调用方是
  上述被删的 `common_workers`，零测试覆盖
- **测试同步迁移（无活逻辑覆盖损失）**：
  - 迁移到 `RoomOrchestrator`：`test_recording_reconnect_tick`(7)、
    `test_multi_room_manager`(17)、`test_stability_guards` 的并发用例(4)、
    `benchmark_heartbeat`（手动性能脚本）
  - 改导入源为 `lsc.core.session`：`test_category_flow`(8)、`test_synced_continuous_analysis`(14)
  - 清理冗余：`test_round2_thread_broadcast_guards` / `test_stability_latency_guards`
    中未使用的 `MANAGER` 源码读取
  - 删除测试自身（测的是被删代码）：`test_manager_shell_signals`(1)、
    `test_exporter` 的 `ExportWorker` 用例(1)、`test_orchestrator_event_parity`
    的 Qt 门面一致性对比(1)
- **配置/文档同步**：`pyproject.toml` 移除 mypy `lsc/gui/` 排除与 `PySide6.*` override；
  `requirements.txt` / `dependency_manager.py` / `CLAUDE.md` / `README.md` 更新过期描述
- **验证**：后端 16 个模块全部导入正常、WS 服务完整启动（端口回退正常）；
  全量测试 1766 passed（唯一失败为既有 safe-delete 环境问题）；
  改动文件 `ruff` 全过

### 新增（broadcast_mode 影子模式，切换前取数）

- **背景**：`OcrRoundFSM.feed(broadcast_mode=True)` 的赛事回放保护（忽略未伴随准备阶段的
  新交战钟）此前**仅被测试覆盖、未接入生产**。真实录像实测：3 个 19/23/26s 碎片回合
  **100% 由 `next_combat` 闭合**，而正常回合（46–232s）全部由 `next_prep`/
  `broadcast_exclusion` 闭合（详见 `docs/reports/replay-vs-nextcombat-experiment-20260910.md`）
- **影子模式**：新增环境变量 `LSC_VALORANT_BROADCAST_MODE_SHADOW`。开启后，`broadcast` 档位
  会用**同一批 OCR 标签**并行跑一份 `broadcast_mode=True` 的 FSM，产出两份回合列表的
  差异摘要（独有/缺失/时长变化/`next_combat` 计数）与累计统计，写入运行时状态并输出
  INFO 日志；**生效回合列表不受任何影响**
- **生效路径零改动**：生效 `fsm.feed(...)` 仍不传 `broadcast_mode`；影子 FSM 独立持久化于
  `state["ocr_fsm_broadcast_shadow"]`；回放标注、`round_key`、边界密扫仍只作用于生效列表
- **守卫**：新增 `tests/test_broadcast_mode_shadow.py`（24 条），含「影子开关默认关闭」
  「影子不改变生效结果」「`broadcast_mode=True` 全模块仅一处代码调用」等 AST 级源码守卫
- **验证**：真实录像端到端对照（同一时间范围跑影子关/开两次）→ 生效回合列表**完全一致**，
  影子摘要与累计统计正常产出；分析器相关套件 233 passed

---

## v1.0.11 (2026-09-01)

### 稳定性

- 对齐锚点按每路音频捕获结束时刻修正，避免多路采集结束时间不同导致时间线/导出/剪映 6–7 秒错位
- 录制媒体起点不再使用固定 2.5s 猜测，共享进样使用首次输出数据时刻
- 时间线拖出直播 MSE 缓冲后自动切换到录制文件回看，并支持从指定秒数起播
- 1x 时间线最左端恒定 00:00，拖拽/精修不改变左端点
- 持续分析 OCR 边界精修、回合合并保护、收尾扫描锚定优化
- 修复 stream_url_expiry 测试时间敏感问题

### Microsoft Store

- 自包含 AppX 版本更新至 1.0.11
- README 添加 Microsoft Store 徽章

---

## 旧版 v3.0.21（已降级存档）

### 稳定性（长期挂机重点）

- **录制重连后台化**：断流重连的 URL 刷新与 FFmpeg 探测不再阻塞编排线程（此前单房重连可冻结全部房间 20-60s）
- **编排线程防死亡**：全局 tick 任何异常不再杀死编排线程（此前会导致 12 路录制/预览整体冻结且无法自恢复）
- **导出挂死修复**：导出异常时完成回调必达 + 6 小时兜底超时（此前 2 次卡死即占满全部导出槽位）
- **OCR 抽帧子窗化**：持续分析追赶扫描内存尖峰从 ~330MB 降至 ~40MB
- **MSE watchdog 恢复上限**：断流房间自动恢复最多 3 次后提示手动处理（此前每 15s 无限重启预览进程）
- **心跳定时器泄漏修复**：WS 断连/重连循环不再累积定时器
- **广播链路加固**：慢客户端 1s 超时剔除、广播队列竞态不再杀死线程
- **backend-stdout 日志轮转**：挂机数天不再无限增长写满磁盘
- 一键对齐拦截分支增加诊断日志，按钮长按粒子卡死/误触发短按修复

### 功能

- **新手引导**：首次进入工作台弹出四步引导（此前组件存在但从未接入）
- 设置页"检查更新"正确显示新版本发布说明

### 修复

- 刷新按钮长按后粒子效果残留、按钮失灵
- 房间移除后播放头残留键清理、设置页定时器清理、播放器重启后数据饥饿检测失效

---

## 旧版 v3.0.0（已降级存档）

### 新增功能

- **持续分析（Valorant 回合切割）**
  - 音频能量 + 回合结束钟声分割战斗段
  - OCR（RapidOCR）识别购买阶段 / 胜负结算，校正权威边界
  - 相位调度器（buy / combat / post_combat / intermission）控制 OCR 预算
  - 录制结束后全文件 OCR 收尾精修
- **主房分析 → 副房映射**
  - 副房通过 `recording_start_mono` + `content_offset` 差值映射后 `clip_queued`
  - 映射失败时广播 `mapping_fallback`，前端 toast 提示
- **AI 回合待确认机制**
  - AI 高光默认 `confirm_status=pending`，不自动 FFmpeg 导出
  - 精修：拖时间线调入出点 → 确认 / 确认并导出
- **剪映草稿集成**
  - 分析完成后自动生成剪映草稿（`pyJianYingDraft`）
  - 草稿目录白名单安全校验
- **工作台 UI 统一**
  - 浅色 + 品牌色 `#31B3AE` 主题
  - Modal / 设置抽屉溢出修复
  - 分析进度与导出摘要
- **DVR 时间线**
  - 录制回看紫色标记左边界对齐
  - 离线文件 MSE 预览

### 优化

- **功耗优化**
  - OCR 采样间隔与相位预算，避免全时段满负荷扫帧
  - 预览路数压力感知降分辨率 / 帧率
  - 共享进样减少重复 CDN 拉流
- **性能优化**
  - 房间卡片布局简化（徽章归入头部与元数据行）
  - 录制队列位置提示
  - 预览画质降级横幅提示

### 安全修复

- WebSocket Origin + Token 双校验
- 全局状态并发锁保护
- IPC 监听器泄漏修复
- ffprobe 执行器隔离
- 输入大小限制

### Bug 修复

- 修复长时间录制稳定性问题
- 修复热路径消息断线队列策略
- 修复 `mse_init` 竞态（`request_mse_init` + `replay_init`）
- 修复录制源切换时播放器重建问题
- 修复切片导出墙钟快照精度

---

## v2.0.0 (较早版本)

### 新增功能

- 一键对齐直播间（多房间音频互相关对齐）
- 长时间录制稳定性修复

---

## v1.0.1 (2026-06-28)

### Bug 修复

- 修复安装后空白窗口问题（`app.isPackaged` 判断 + 5 秒超时检测）
- 图标改为白色背景（适配 Windows 各主题）

---

## v1.0.0 (初始版本)

- 多路同步录制（最多 12 路）
- MSE 实时预览（最多 4 路）
- 墙钟精确切片导出
- 平台适配（抖音 / B站 / 虎牙 / 快手 / 斗鱼 / 小红书 / 微博）
- 全局快捷键与设置页

---

## 安装与文档

- **下载**：[GitHub Releases](https://github.com/Lawrence7y/LSC/releases)
- **日志位置**：`%APPDATA%\lsc-electron\logs\`
- **问题反馈**：GitHub Issue
