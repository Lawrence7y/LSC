# LSC 直播切片系统 — 更新说明

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
