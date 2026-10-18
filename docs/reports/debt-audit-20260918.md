# 结构性债务与未收口缺陷审计（2026-09-18）

> 范围：**结构性债务** + **已记录但仍未收口的缺陷**。不含新功能设计。
> 分支：`audit/structural-debt-20260918`；工作树 `D:/Project/lsc-debt-audit-20260918`（HEAD = `dbeb707` / v1.0.17）。
> 方法：全部结论以命令实测或 `file:line` 为证。**事实**＝已在代码/运行输出中核验；**推断**＝本报告的解释性假设；**待验证**＝需要用户或下一次真机复现才能定论。
> 对照基线：本报告同时核验了 `CLAUDE.md`、`docs/SUBSYSTEM_ANALYSIS.md`、`docs/FEATURE-INVENTORY.md` 的自述，发现 **6 处文档与代码不符**（§7）。

---

## 0. 一页结论

| # | 发现 | 等级 | 依据 |
| :-- | :--- | :--- | :--- |
| 1 | **前端回归防线有一半是假的**：`tests/test_frontend_stability_guards.py` 142 个用例中 **141 个只对 TypeScript 源码文本做子串匹配**，零 `subprocess`/node/jsdom。它是全仓第 3 热文件（自 6 月 38 次提交） | P0 | §5 |
| 2 | **质量门禁现状是红的**：CI 阻塞项 `ruff` 在已提交 HEAD 上就有 6 处错误；`vitest` 一条真机事故回归守卫失败，而 CI 从不跑 vitest | P0 | §2 |
| 3 | **覆盖率口径高估 11 个点**：CI 只测 `--cov=lsc`（73%）；纳入 `python-backend` 后 **62%**。承载全部并发与导出的 `room_handler.py` 只有 **39%**（3,489 条语句从未执行），拆出去的 `alignment_handlers` 15% / `export_handlers` 16% | P0 | §2.3 |
| 4 | **两个上帝文件同时是变更最热的文件**：`room_handler.py` 10,970 行 / 55 次提交；`Workbench/index.tsx` 5,259 行（单函数体 4,919 行）/ 43 次提交 | P1 | §3 |
| 5 | **没有任何测试能发现平台改版**：9 个适配器的测试全部 mock 掉网络，CI 无网；错误码体系里**没有区分"流下线"与"我们的解析器过期"**；Cookie **过期检测事实上不存在**（更正：`invalidate()` 并非无人调用，resolver 期一直有，缺的是 **ingest 期**与"把失效显示给用户"这两半，见 §11.9-C） | P1 | §6 |
| 6 | **FFmpeg 进程总数无全局上界**：33 个 spawn 点 / 15 个文件；历次资源争抢事故的回答方式一直是收紧局部信号量而不是加中央准入控制 | P1 | §6.4 |
| 7 | **`git clone` 需要 2.08 GiB，其中 98.7% 是数据集**；训练脚本本来就用 `~/LSC/datasets/`（43 处）而不是仓库相对路径 ⇒ 搬走的摩擦很低 | P2 | §4 |
| 8 | **版本标签在 2026-09-01 倒退过**（v3.0.23 21:26 → v1.0.11 22:00），`compareVersions` 按三段数字比较 ⇒ 装过 v2/v3 的用户永远看到"已是最新" | P2 | §8 |
| 9 | 生产实际跑的是**共享进样**（`settings.json: shared_ingest_enabled=true` 覆盖 `LscConfig`），而 `CLAUDE.md §7.3.1` 写的是"默认独立双进程" | P1 | §7.2 |
| 10 | **预览时钟冻结仍无根因**（诊断已补：周期性 WARN；**并已阻断一个必然恶化的放大环节** —— 冻结期间回看读盘无界增长，§11.15），且宣称"可定位"不成立：健康快照只在 `_handleError` 构造一次，错误**之前**的滞后增长曲线在生产日志里不可见 | P1 | §7.1、§11.15 |

**处置进度（截至 2026-09-18 晚；五道门禁全绿：`ruff check .` / `mypy` Success /
pytest 2195 / `tsc` ×2 工程 / vitest 29 文件 307 用例）**

**已收口**

- **#2** 门禁由"存在但没在执行"变为真执行：ruff 归零 + 钩子真装 + vitest 进 CI +
  mypy 阻塞 + 覆盖率口径改正。**本批再补一条**：Electron 主进程工程从未被 CI 或
  打包前类型检查，现已进 CI（§11.13-E）。
- **#5（可观测的那一半）** `parser_stale` 分类与计数、Cookie 活性实测显示、平台自检入口、
  降级首次留痕（`lsc/platforms/diagnostics.py`）。
- **#6** 中央派生准入控制器 + 7 个派生点接闸。
- **#9** 文档按代码改正（共享进样为生产默认）。
- **#10 的相邻项** 远定位顺序扫描 → 稀疏检查点二分（§11.10，134 次读盘 → 4 次探测，含负控）；
  冻结进度周期性 WARN 已补。
- **范围外新发现的 P0**：`main.stop()` 每次抛 TypeError ⇒ 后端进程残留（§11.9-D）。
- **范围外新发现的 P1（用户可见的"永不产出"）**：持续分析选「通用」档时后端照启循环、UI 报进度、
  日志每轮 WARN，但恒 0 结果 —— 插件自报的 `realtime_continuous=False` 没有任何执行点。
  已加启动侧能力闸、兜底分支如实报 `scan_succeeded=False`、废弃包装删除（§11.18-D）。
- **§7.4 仍开项之一已修**：广播通道的 `_seq`「丢消息检测器」本身不可信（发号与入队不同步 +
  遥测占号 + 唤醒失败只有 debug）⇒ 现在缺号告警等于"真丢了关键消息"，并新增
  `broadcast_health()` 进 `system_stats`（§11.17，含 4/4 负控）。
- **范围外新发现的 P1（用户可触发崩溃）**：回看播放头一停（暂停 / §7.1 冻结），
  读盘以 ~8 MiB/s 无界堆进渲染进程 JS 堆 ⇒ 分钟级 OOM。已修 + 可执行用例 + 负控（§11.15）。
  这条同时把 §7.3 原表同一行的严重性改正为"出画后根本没有上界"。
- **范围外新发现的"纸面功能"**：一键对齐的 `applyOffsetWithDriftCorrection` 55 行
  自写入起零调用点，却被三份文档写成前端落点机制（§11.14）。已删死代码、文档改如实，
  并把真实落点抽成 `utils/previewAlignment.applyAlignmentSeek` 唯一入口 + 8 条可执行用例
  （负控 6/6）。

**部分收口（有明确剩余量与前置条件）**

- **#1 前端防线**：迁移方法与假件已建立，MsePlayer 一簇完成 —— 10 条可执行用例 +
  负控 6/6（同批变异下全量文子守卫 4 条完全无感），据此**下线 5 条**文子守卫
  （141 → 136）。第二簇：一键对齐落点从 `Workbench` 内联抽出为可测唯一入口（§11.14）。
  剩余 131 条待逐簇迁移，而**它是 #4 的前置条件**（§11.13-D）。
- **#3 覆盖率**：口径改正 + 地板 55 阻塞；`room_handler.py` 自身 39% 未提升。
- **#4 上帝文件**：`room_handler.py` 11038 → **10908**（凭证簇 6 条路由 + 自检路由外移）；
  settings 簇经实测搬家会静默中和 15+ 处 monkeypatch 且会写用户真实
  `settings.json`，**判定不安全**（§11.9-B）；`Workbench/index.tsx` 5259 行未动，
  前置条件即 #1。
- **mypy 存量**：非机械码清零，355 → 296 条 / 42 → 31 段豁免；过程中修掉一条
  **永不成立的 `PlatformAdapter` 协议**（§11.12-A）。

**不在"收口"范围、需授权或立项**

- **#7** 仓库瘦身（历史重写需明确授权）；**#8** 发布编号策略（新守卫会拦下 v1.0.18，
  现存最高标签 v3.0.23）需用户决策；**#5 的另一半**（没有任何测试能发现平台改版）
  需要真实录制 fixture / 线上探针，属独立立项；**#13 剪映 §7.1** 根因需真机标定，
  不做盲改。CI 历史仍需 `gh auth login`。
---

## 1. 复现方法（本次全部数据的来源）

```bash
# 门禁（主工作树）
ruff check lsc/ python-backend/                 # → Found 15 errors
ruff check lsc/exporter/jianying_draft.py python-backend/continuous_finalization.py \
       lsc/core/recording_layout.py lsc/analyzer/valorant_ocr_rounds.py \
       lsc/analyzer/valorant_observer_scoreboard.py   # → Found 6 errors（本次未改动 ⇒ HEAD 上就有）
mypy --show-error-codes                         # → Found 112 errors in 27 files (checked 85 source files)
cd lsc-electron && npx tsc --noEmit             # → 0
cd lsc-electron && npx tsc --noEmit -p tsconfig.electron.json   # → 0
cd lsc-electron && npx vitest run               # → 27 files, 273 passed | 1 failed

# 覆盖率（干净工作树，避免污染用户数据）
cd D:/Project/lsc-debt-audit-20260918
QT_QPA_PLATFORM=offscreen python -m pytest -q --cov=lsc --cov=python-backend --cov-report=term
# → 2140 passed, 1 skipped in 112s ; TOTAL 29874 stmts, 11487 miss, 62%

git count-objects -v -H                         # → in-pack 12876, 3 packs, size-pack 2.08 GiB
git log --name-only --pretty=format: --since=2026-06-01 | sort | uniq -c | sort -rn
```

---

## 2. 质量门禁：实测 vs 宣称

### 2.1 逐项对照

| 门禁 | 文档 / CI 宣称 | 实测 | 事实/推断 |
| :--- | :--- | :--- | :--- |
| `ruff check lsc/ python-backend/`（CI **阻塞**，`ci.yml:28`） | 绿 | **15 errors**；其中 **6 处位于本次未改动文件** ⇒ 存在于已提交 HEAD | 事实（远程 CI 结论 **待验证**：GitHub API 未认证限流，无法读 actions 历史） |
| `mypy`（`ci.yml:32`，`\|\| echo "::warning::"` 放行） | 注释冻结在 "87 errors in 22 files" | **112 errors in 27 files** | 事实。债 +29% 且注释已失效；注释里的 `issue #mypy-cleanup` 不是真实 issue 号 |
| `tsc --noEmit` 两个工程 | 无错误 | **无错误** ✅ | 事实 |
| `vitest run`（`package.json` 有 `test` 脚本） | CHANGELOG v1.0.18 称"27 文件/274 用例全绿" | **273 passed / 1 FAILED** | 事实。CI 两个 workflow 中**均无 vitest**（只有 `npm ci` + `tsc`） |
| `.pre-commit-config.yaml`（pin ruff v0.5.0） | 存在 | **钩子从未安装**，`.git/hooks/pre-commit` 不存在 | 事实 ⇒ 本地零强制，且 pin 的版本（0.5.0）与本机（0.15.16）跨 10 个小版本 |
| 前端 lint | — | **完全没有 eslint 配置**（无 `.eslintrc*` / `eslint.config*`） | 事实 |
| 覆盖率阈值 | — | 无 `--cov-fail-under`，`vitest.config.ts` 配了 v8 provider 但 `npm test` 不带 `--coverage` | 事实 |
| `macOS` 构建 | `PRODUCT.md:23` 称 Windows/macOS | CI 与 `electron-builder` 只有 win 目标（nsis + appx），无 macOS job | 事实 ⇒ 文档越界 |

### 2.2 那条红掉的用例

`lsc-electron/src/services/localFileMseSource.test.ts:659`
`定位窗口被 IPC 读上限截断时仍必须落到目标 fragment（2026-09-15 真机事故）` → `expected 0 to be greater than 0`（`medias.length`）。

- ~~单独跑也失败 ⇒ 不是用例间污染~~ **本报告初版的这条判断是错的，已更正**：真正的原因是**负载敏感**，
  不是污染也不是逻辑缺陷。证据链：只跑该文件时它失败过（当时后台有全量 pytest 在跑）；空闲时
  该文件 4/4 绿、全量套件 2/2 绿；`firstFrameTimeoutMs` 之外还有 `await wait(2000/2500/4000)` 这类
  **在空闲机上标定的墙钟预算**。远定位耗时 = 扫描区间 ÷（每轮一次 IPC 读 × `pollIntervalMs`），
  争抢下必然超过预算 ⇒ `medias` 仍为空。（注：8 个 node 忙等循环不足以复现，只有并发全量 pytest 才行。）
- 被测模块 `localFileMseSource.ts` 与测试文件本身本次都**未改动**，测试唯一的跨文件 import 是 `import type { MsePlayer }`（type-only，编译期擦除）⇒ **推断**：它是随 v1.0.17 一起进 `main` 的不稳定守卫，而非本地未提交改动引入。
- 讽刺点：这条守卫守的正是 §7.1 里"远定位落到字节 0 顺序啃 236MB"那个事故。守卫不稳定 + 该路径不在 CI ⇒ 同一条修复既没被验证、又随时可能被误当成回归。
- ✅ **已修（2026-09-18）**：改为条件等待 `waitUntil(条件, LOCATE_CEILING_MS)`（条件一满足立即返回，上限只是死锁兜底），并为这些用例显式设 `timeout`（vitest 默认 5s 本就压不住它们）。**断言一条没删、没放宽。** 验证：与全量 pytest 并发连跑 2 次全绿。

### 2.3 覆盖率：拆出去的模块没有被测试接上

`--cov=lsc --cov=python-backend`，2140 passed / 1 skipped：

| 模块 | Stmts | Cover |
| :--- | ---: | ---: |
| `handlers/room_handler.py` | 5,682 | **39%**（3,489 条未执行） |
| `handlers/alignment_handlers.py` | 226 | **15%** |
| `handlers/export_handlers.py` | 391 | **16%** |
| `handlers/room_utils.py` | 113 | **18%** |
| `dependency_manager.py`（首启装依赖：新用户最高风险路径） | 353 | **24%** |
| `main.py` | 270 | 27% |
| `handlers/analysis_handlers.py` | 784 | 46% |
| `lsc/core/orchestrator.py` | 2,541 | 59% |
| `server.py` | 398 | 58% |
| **TOTAL（含 python-backend）** | 29,874 | **62%** |
| TOTAL（CI 口径，仅 `lsc/`） | 19,338 | 73% |

0% 的 6 个文件共 ~700 语句，其中 **`python-backend/start.py` 是第三条并行的 WS 引导入口** —— 端口问题不止 `9876/19876` 两个变体（§7.6.a）。

**推断**：handler 拆分做完了，测试没跟着走。`alignment/export` 只有 15-16%，但 `CLAUDE.md` 把"一键对齐""导出队列"都描述成有守卫覆盖的核心路径 —— 实际那部分信心来自 §5 的文本匹配测试。

---

## 3. 结构性债务：规模 × 变更热度

自 2026-06-01 共 **246 次提交**（≈70/月）。把"行数"和"提交次数"放在一起，才看得出该先拆哪个：

| 文件 | 行数 | 6 月以来提交次数 | 排名 |
| :--- | ---: | ---: | :--- |
| `python-backend/handlers/room_handler.py` | 10,970 | **55** | 最大 + 最热 |
| `lsc-electron/src/pages/Workbench/index.tsx` | 5,259 | **43** | 前端最大 + 第二热 |
| `tests/test_frontend_stability_guards.py` | 2,003 | **38** | 见 §5：它是对前端源码的文本断言 |
| `lsc-electron/src/types/index.ts` | 721 | 25 | 契约集中地 |
| `lsc/core/orchestrator.py` | 4,447 | 24 | |
| `python-backend/server.py` | 671 | 22 | |

### 3.1 后端 `room_handler.py` 解剖（事实）

- **36 个注册**（1 × `on_connect` :5214 + 35 × `@server.on`）。
- `register_room_handlers`（:4595）是一个 **6,375 行的函数体**；其中 `_continuous_analysis_loop` 嵌套函数 **2,176 行**（:8556）、`_handle_mse_preview` 882 行（:6332）、`_continuous_valorant_worker` 867 行（:7689）。全文件 **7 个函数超 150 行**。
- **21 个模块级可变全局 + 8 把锁 + 18 个并发原语**：4 个 `ThreadPoolExecutor`（rec=6/bridge=8/ai=2/probe=4，共 20 线程）、3 个 `asyncio.Semaphore`、3 个 `threading.Semaphore`、7 `Lock` + 1 `RLock`。
  ⚠️ `room_handler.py:638` 有 `_onnx_inference_semaphore = _analysis_thread_semaphore` —— **两个名字指向同一个信号量对象**（同一槽位）。**推断**：意图是"单次分析与持续分析串行化 DirectML"，但用别名表达，任何后续读者都会以为是两个独立预算。
- **跨线程共享但完全无锁的状态**（事实：以下位置无 lock 保护，且被 WS 线程与 executor 线程同时触及）：`_settings_cache`（:4073-4076，用 `global` 在 :4096 改写）、`_manual_clip_counters`（:330）、`_ai_clip_counters`（:332）、`_last_authority_snapshots`（:381）、`_mse_live_phase`（:864，:1234 有 `.clear()`）。
- **只增不减**：`_refined_round_keys`（:7299，全文件唯一未上界的 clip 级注册表，每回合写 2 键）；`_manual_clip_counters`/`_ai_clip_counters`（删房不清理）；`timeline_service.py:131 _clip_snapshots`、`timeline_service.py:129 _invalidate_listeners`（只 append，永不移除 ⇒ **推断**：泄漏 + 重复通知）。
- **注册顺序耦合是真实存在的**：`export_handlers` 在 :10912 注册后才在 :10926 通过 `get_queue_export()` 取出并注入 `timeline_handlers`（:10957）—— 交换这两行会传进 `None`。这种依赖没有任何断言守着。
- **可分性最高的簇**（推断自交叉引用密度）：settings + 平台凭证（230 行 / 8 路由，只触 `load/save_settings`）、传输层 seek/mark/review（245 行）、Valorant 常量墙（100 行纯常量）、`_build_continuous_status_payload`（290 行只读投影）。**最不可分**：持续分析编排（≈5,700 行）。
- 一处需要纠正的传言：`_deferred_export_jobs`（:556）**是活代码**（:7517 append、:7590/7598-7599 消费、:7636 回队），`CLAUDE.md §11.5` 的"条目已失效"结论**正确**。

### 3.2 前端 `Workbench/index.tsx` 解剖（事实）

- `export default function Workbench()` 起于 :340，**函数体一直写到文件尾 = 4,919 行**。
- 内部计数：`useState` 15、`useRef` 15、`useMemo` 10、`useEffect` **26**、`useCallback` **54**、`useAppStore(` 15、`message.*` 调用 **113**。
- WS 分发**不是** switch：**27 个 `on('<type>', …)` 内联闭包**散在 5 个 effect 里（:885-1004 / :1007-1114 / :2022-2156 / :3532-3732 / :3735-3991）。
- **没有一个"过大的 useEffect"是元凶**；问题形态是 26 个 effect × 54 个回调用手工维护的依赖数组串起来。**推断**：这才是"改一处塌别处"的来源，也解释了 43 次提交里有多少是修补交互回归。
- 组件契约：`<RoomCard>` 收 **28 个 props**（:4897-4927），`<ControlBar>` 约 **45 个**。
- 9 个前端文件超 800 行（Workbench 5,259 / `electron/main.ts` 2,182 / `mediaSourcePlayer.ts` 1,410 / `RoomCard.tsx` 1,384 / `Settings/index.tsx` 1,204 / `VideoPreview.tsx` 1,031 / `Timeline/index.tsx` 1,031 / `ControlBar.tsx` 946 / `useWebSocket.ts` 895）。

---

## 4. 仓库与磁盘卫生（事实）

| 指标 | 值 |
| :--- | :--- |
| tracked 文件数 | 9,068 |
| 其中 `datasets/valorant_phase/` | **8,395（92.6% 的文件数）** |
| tracked 内容总字节 | 1,452,015,265 B，其中 `datasets/` **1,433,270,636 B = 98.7%** |
| `.git` | 2.4 GB；pack **2.08 GiB**（1.45 GB + 0.79 GB + 1 MB 增量） |
| 超过 10 MB 的 tracked 文件 | **0 个**（最大 blob 617 KB） ⇒ 问题不是单个大文件，是 8,395 个中等 blob 在历史里被重复打包 |
| 本次为建分支新签出的工作树 | 1.4 GB（几乎全是数据集） |

**`.gitignore` 策略自相矛盾**：:153-154 把 `datasets/valorant_phase/` 注释掉，标题写"included for model reproducibility"；而 :169-170 给出的理由是"materialized frames and machine-specific paths; keep the **builder/schema** in Git instead of the generated artifacts" —— 这段话对 v1 目录**逐字同样成立**。即：同一个理由，后来的 4 个数据集被 ignore，第一个（最大）被保留。

**搬走的数据可行性（推断，但有强证据）**：`scripts/` + `docs/` 里引用该数据集共 108 处，其中 **43 处形如 `Path.home()/"LSC"/"datasets"/"valorant_phase"`**（如 `scripts/valorant_vision/build_hard_dataset.py:20-22`），仅 12 处仓库相对路径（主要是 `build_phase_manifest.py:44`）。⇒ 训练脚本本来就按"树外数据目录"写；阻碍只有那一处 repo-root 假设。且 `package.json` 的 `publish.provider = github` 已经提供了 Release 资产通道。

**未被 `.gitignore` 覆盖、一次 `git add -A` 就会污染的**：`.mypy_cache/`(19 MB)、`.ruff_cache/`、`artifacts/`(3.8 MB)、`data/rooms.json.orig`(1.05 MB，8 月 14 日的备份残留)。仓库根目录并存 **12 个 AI 助手配置目录**（磁盘 ~402 MB，其中 `.mimosa/` 242 MB）—— 在这种工作环境下这是个现实的误提交面。

---

## 5. 本轮最重要的发现：测试有效性（事实 + 推断）

`tests/test_frontend_stability_guards.py`：**2,003 行、142 个顶层定义，其中 141 个是"读 TypeScript 源文件 + 断言子串存在"**（文件内 `read_text` 出现 215 次；全文零 `subprocess`、零 node、零 jsdom）。

它守的是什么：

```python
# 典型形态（test_mse_caches_have_ttl_and_room_cleanup:122-132）
assert "export function clearMseRoomCache" in source
assert "delete _mseInitCache[roomId]" in clear_body
```

- **能挡**：重命名、误删函数、忘记接线。这几类确实是本项目反复踩的（`rooms_updated` 覆盖 uiState、双基线 CRF、时间线不绑房间）。
- **不能挡**：函数存在但返回错值。改后绿≠行为对。
- **副作用（推断）**：它把"实现文本"编码进了测试。变量改名、抽出 helper、`??` 换 `||` 都会让 141 条断言成片变红 —— 于是**重构成本被抬高，而行为回归信号仍然是 0**。它与 `Workbench/index.tsx`（43 次提交）、`room_handler.py`（55 次）一起构成全仓前 3 热文件：**这三者的耦合就是"每次改前端都要同步改文本断言"的税**。
- 同类问题在 vitest 侧也有一例：`src/hooks/useWebSocket.mseCache.test.ts:60-65` 用 `readFileSync` + `expect(source).not.toContain(...)`。
- CHANGELOG 把新增守卫一律记作"+N 条用例"，因此 **2137 这个规模数字被高估了它所能提供的信心**（后端真实行为测试仍是强的：`orchestrator`/`valorant_*`/`continuous_analysis_guards` 都是真跑逻辑）。

前端真实覆盖的另一面（事实）：vitest 共 27 文件 4,368 行 = 生产代码的 13.1%。
`RoomCard.tsx` 1,384↔**0**、`Settings/index.tsx` 1,204↔**0**、`VideoPreview.tsx` 1,031↔**0**、`AnalysisProgress.tsx` 701↔**0**、`previewAudioAligner.ts` 620↔**0**。
`Workbench/index.test.tsx` 用真 `render()` 但 **mock 了 20 个模块并把 RoomCard/ControlBar/AnalysisProgress 换成 `<div data-testid>`** ⇒ 8 条用例里 6 条渲染断言验的是桩。
`appStore.test.ts` 只有 **2 条**，且**不测** `preview_channel / review_path / review_offset_sec` —— 恰是 `CLAUDE.md §8.8` 规定"整表替换禁止覆盖"的那几个字段。
`timelineCoords.test.ts` 只测 16 个导出函数中的 **2 个**，四个公共轴换算函数零覆盖。
**`electron/`（2,182 行主进程，含全部 IPC 与安全边界）测试数为 0** —— `vitest.config.ts` 的 `include` 只有 `src/**`。

### 5.1 三套手工同步的字段清单（事实）

`src/store/appStore.ts` 里对"哪些字段属于前端本地"的表达是**三处独立硬编码**：
- `:205-225` `setRooms` 逐字段重建 **8 个**（`preview_mode` / `preview_review_start_sec` / `preview_phase` / `mse_error` / `mse_reconnecting` / `preview_frame_data` / `recording_to_preview_delta` / `preview_clock_epoch_id`）；
- `:253` `updateRoom` 的 `uiFields` 只有 **4 个**；
- `:144-150` `roomsShallowEqual` 的 `healthKeys` 又是 **21 个**。
另有 6 个专用 action（:307/:328/:343/:351/:362）各自直写 `uiState`。
`RoomCard.tsx:155-238` 的 memo 比较器再手写 **27 个字段判断**；`ClipList`/`Timeline`/`VideoPreview`/`AnalysisProgress` 未 memo。
**推断**：这是 §8.8 那条"禁止覆盖"事故的结构性成因 —— 清单加第 9 个字段时必须记得同时改 3-4 处，而唯一能发现漏改的是人工回归。

### 5.2 IPC 与路径防线（事实，已亲自复核）

- `electron/main.ts` 注册 **33 个 ipcMain 通道**；`_isSafePath`（定义 :1136，实现是认真的：realpath AND 语义 + 大小写不敏感前缀 + 可执行扩展名黑名单）**只在 2 个通道被调用**（:1396 `open-path`、:1417 `show-item-in-folder`）。
- 另外两套平行实现：`local-media:info/read`（:1270/:1285）走 `electron/localMedia.ts:121 resolveAllowedMediaPath`；`read-log-file`（:1696）走"3 个文件名白名单"。⇒ **三条手工保持同步的路径防线，没有单一咽喉点**。`local-media:allow-root`（:1251）允许渲染进程追加白名单根目录。
- `preload.ts` 189 行是 `contextBridge` 平铺透传，**全仓无 zod / 无 schema / 无运行时校验**。
- 需要澄清（我否决了下级结论）：`install-dependencies`（:1993）的渲染进程入参被 `options?.includeAi !== false` 收敛成**一个布尔**再传给 `installDependencies(includeAi)`，所以"接受渲染进程任意 options 对象"的说法不成立。真实问题是：**渲染进程可无校验地触发一次以用户完整环境运行的 pip/git/FFmpeg 安装**（`CLAUDE.md §11.2` 已把这条列为"文档化例外/风险接受"）。

---

## 6. 外部依赖与资源模型

### 6.1 没有任何测试能发现平台改版（事实）

`tests/test_platform_adapters.py` 在 `fetch_url`/`fetch_json` 层把 9 个适配器**全部 mock**（100 处 `monkeypatch`/`patch`，页面夹具是固定快照）；`test_platform_adapter_contract.py` 只有 3 条且只查"能力字段存在"；`ci.yml:95` 的 pytest 无网。唯一能发现真实失效的是 `scripts/platform_acceptance.py`（需真网 + 真 FFmpeg，`acceptance.py` 1,362 行仅被它引用）。

⇒ **推断**：适配器层是"产品唯一入口"（`FEATURE-INVENTORY §1` 自己给的定级），却是全仓唯一一处**外部变化不会让任何自动化信号变红**的地方。抖音/B站/虎牙签名规则改版的发现路径是：用户报障。

两个高危单点（事实）：
- `lsc/platforms/douyin.py:502-517` 用 `spec_from_file_location` 动态加载 `scripts/douyin_record.py`（499 行）。打包后靠 `package.json` extraResources 同时映射 `../lsc` 和 `../scripts` 才活着；**对 ruff / mypy / `ci.yml` 全部不可见**。
- `lsc/platforms/huya.py:85-131 build_huya_live_query` 是 JS 签名的 Python 复刻（base64 解 `fm` → 随机 uid 位旋转 :111-112 → 两次链式 MD5 :116-119）。:104/:108 静默回退到原始 anti-code ⇒ **失效症状是"能连上、GOP 有限、然后 EOF"，而不是报错**（这正是历史上最难查的那类）。

### 6.2 错误码体系无法区分"流下线"与"解析器过期"（事实）

legacy 面只有 4 个码（`lsc/platforms/base.py:16-19`），而 `ERROR_PARSE_FAILED` 是 catch-all（`base.py:503`，`douyin.py:494`），并被 B站的 JSON 形状变化大量使用（`bilibili.py:134,143,145,160,166,220,227`）、斗鱼（`douyu.py:101,145,149,204`）。**没有 `parser_stale` / `schema_changed` 这一类**。
更细的 `FailureKind`（含 `AUTH_EXPIRED`/`CDN_FORBIDDEN`/`RATE_LIMITED`，`lsc/platforms/failure.py`）**只在重连层无条件接入**（`orchestrator.py:4039`），解析层用不到。

### 6.3 Cookie 过期检测事实上不存在（事实）

`get_douyin/bilibili/huya_cookie_status`（`lsc/platforms/cookie_helper.py:453,467,512`）只返回 `{"configured": bool, "count": len}`；`cookie_helper.py` 内 `expir` 出现 **0 次**。UI 因此只能显示"已配置 N 项 / 未配置"（`Settings/index.tsx:953-956`）。
真正的 `CredentialStatus` 枚举存在（`platforms/models.py:347`，`resolver.py:512`）但**只在 V2 路径填充**（`orchestrator.py:2088-2092`），而 V2 默认关 ⇒ `room.credential_status` 恒为 `session.py:33` 的默认 `"NOT_CONFIGURED"`。
（我复核了 `runtime_health.py:130-138`：那里对"支持匿名访问的平台 + 无 Cookie"改写为 `AVAILABLE`，**上面有明确注释说明是有意设计**，避免把每个公开链接画成橙色 —— 这不是伪造，别当 bug 报。真正缺的是"配了 Cookie 但已失效"这一维。）

### 6.4 FFmpeg 进程总数没有全局上界（事实）

**33 个 `subprocess.Popen` / `create_subprocess*` 调用点分布在 15 个文件**。单房可能同时持有：上游 ingest（`shared_ingest.py:1511`）+ 录制（`capture.py:487`）+ 分段写（`segmented.py:138`，**2026-09-18 更正：该模块无任何生产 import，已删除，见 §11.16-B；分段写的真实实现是 `shared_ingest.build_segmented_recording_command`**）+ 预览 muxer（`mse_streamer.py:464`）+ 瞬时 probe + OCR 抽帧 ffmpeg。
已存在的显式上界只有：`MAX_ROOMS=12`（`orchestrator.py:68`）、`MAX_CONCURRENT_PREVIEWS=4`（:69）、启动并发 `Semaphore(2)`（`room_handler.py:1205`）、导出 2（`export_handlers.py:39`）、磁盘 2 GiB/8 GiB（:71、`recording_service.py:770`）、`_MAX_SEGMENT_BYTES=512KB`（`mse_streamer.py:47`）。
**这些预算从不互相乘算或交叉记账**；所有信号量的值都是 **1 或 2，即"串行化"而非"总量封顶"**。probe 也没有全局闸：`_probe_executor`（:1203）只服务文件信息 ffprobe，V2 的 `max_connect_concurrency` 是 per-room（12 房 × 2 = 24 路无协调并发连接）。
`CLAUDE.md` 记录的资源争抢事故：`§7.2.2`（2026-09-15 长预览冻结，:385 明确点名"持续分析争抢导致的追赶失败"）、`§11.3`（2026-09-01 22:56 全房断流）、`§8.6.1`（2026-09-11 20:45）。
**推断**：这三起事故同形（缺乏全局准入），而代码的应对一直是收紧局部信号量。`runtime_health.build_room_health` 是 per-room 的，只有 2 个调用点（`room_handler.py:4328,5097`）—— 项目里**没有中央预算控制器**。这恰好是 §7.1 那条"根因仍未定论"的最可能落点。

### 6.5 V2 解析管线：不是死代码，是会腐烂的未发布活代码（事实）

`platform_pipeline_v2_enabled = False`（`lsc/config.py:222`，:219-221 注释"默认双关，生产走 legacy"）。严格受门的 LOC：`resolver.py` 958 + `probe.py` 566 + `lease_manager.py` 280 + `models.py` 467 = **2,271 行**。
**没有任何 UI 开关**：`Settings/index.tsx:844` 只暴露 `shared_ingest_enabled`，`platform_pipeline_v2` 在 `lsc-electron/src` 中出现 **0 次**；唯一开启方式是手改 `lsc_config.json`（该键在白名单 `config.py:316-321`，但仓库内的文件只写了 3 个键、不含 V2）。
好消息：**适配器没有实现两套接口**，`resolver.py:411-540` 自称"compatibility bridge"，调 legacy `adapter.parse()` 再重规范化 ⇒ 无解析逻辑重复。坏消息：V2 的 candidate/probe/lease 语义是**针对适配器从未被设计去填的 `StreamInfo` 形状**断言的。**推断**：一旦有人打开这个开关，2,271 行只在测试里跑过的代码会直接面对真实流量。

---

## 7. 未收口缺陷逐条：分诊结果

### 7.1 预览时钟冻结 —— 根因仍开，且"已经能定位"不成立（P1）

- 无任何根因修复落地；`CLAUDE.md:384-385` 的 🔻 声明成立。
- 现场再冻结时 `debug.log` 里会有的：① `mediaSourcePlayer.ts:1249` 的 `[MsePlayer] ERROR: … | <_healthSnapshot()>`（字段见 :1229-1242）；② 两条无条件 `console.warn`（:422 play() 失败、:806 音频编解码回退），它们以 level 2 通过 `main.ts:1090` 的过滤。
- **关键限制（事实）**：健康快照**只在 `_handleError` 里构造一次**。500ms 卡死检测的 `Stall detected`、`updateend` 时的 `readyState/buffered/currentTime`（:820-825、:1324）全走 `_log`，而 `_log` 受 `_debug`（:185 默认 false，只能由 `VideoPreview.tsx:355` 的 `localStorage lsc.mseDebug='1'` 打开）压制，`shouldSkipRendererConsole` 还会丢 INFO。
- ⇒ **推断**：`CLAUDE.md` 承诺"新的错误快照是下一次定位的依据"不成立 —— 它记录的是**死亡瞬间**，不是致死过程。现场那条"滞后从 -113s 涨到 -158s/3min"的曲线在生产日志里不可见。
- **最小闭环动作**（推断，成本很低）：把已有快照按固定周期以 `console.warn` 输出（例如每 5s，且仅在 `backlog`/滞后超阈值时），并把 `_debug` 的开启条件加一条"滞后连续 N tick 增长"。这不需要新架构，且直接对上 §6.4 的全局预算假设 —— 有了曲线才能证伪/证实"争抢导致追赶失败"。

### 7.2 生产实跑共享进样，文档写的是反的（P1）

- 事实：`lsc/config.py:210 = True`；`python-backend/settings.json:18 = true`；根目录 `lsc_config.json:2 = false`。
- **谁赢**：`register_room_handlers` 启动即 `_apply_shared_ingest_from_settings(load_settings())`（`room_handler.py:4599`）覆盖 `LscConfig` ⇒ **settings.json（True）赢**；`lsc_config.json` 的 false 只在无 `settings.json` 时生效。
- ⇒ `CLAUDE.md §3.2 / §7.3.1`"默认独立双进程"在生产配置下**不成立**；`FEATURE-INVENTORY:105` 的猜测方向对、结论保守。
- 连带影响（推断）：`shared_ingest.py` 不产出 DVR 镜像 ⇒ 本机 `dvr_output_path` 常为空 ⇒ 本地回看实际永远读 `record_output_path`。但注意 `CLAUDE.md §8.8` 的"`dvr_output_path` **恒为空串**"**是错的**：`capture.py:333-359` 在 `dvr_mirror_enabled`（`config.py:213 = True`）时会预检可写并追加镜像，`orchestrator.py:2615/2539/2922/2930` 经 `_present_dvr_mirror`（:2889，磁盘存在才返回）读回，消费方 `recording_handlers.py:282`、`room_handler.py:4375/5110`；`tests/test_dvr_mirror_and_review_removal.py` 正在守这条链。
- **这是 P1 的理由**：整个 §7.3 章节的时序、CRF、并发结论都建立在"独立双进程"上，而生产不这么跑。**待验证**：用户机器上真实的 `settings.json`。

### 7.3 其余仍开项（事实，按影响排序）

| 项 | 证据 | 影响 |
| :--- | :--- | :--- |
| ~~远定位仍无稀疏检查点索引~~ **已修（§11.10）**：`localFileMseSource.ts` 改为字节轴二分（`probeBoundaryTime` + `MAX_LOCATE_PROBE_ROUNDS`；30 分钟录像 ≈ 7 轮） | 已收口，含负控 |
| ~~回看队列内存上界只在**出画前**成立~~ **已修，且初版把严重性写小了**：出画后**根本没有上界**（~8MiB/s）| `_trimQueueBeforeTarget` 首行 `if (this._sawMedia …) return` 关掉修剪，而原有的"该停了"判据写在**读完之后**、前瞻门只挡喂段不挡读盘 ⇒ 播放头一停，每个 poll 仍读 4MiB | **P1 已收口，见 §11.15（含负控与新用例）** |
| 剪映候选去重 §7.1 根因未修 | L1 `INTERIOR_BOUNDARY`（`jianying_handlers.py:225,317`）、L2 `_dedupe_overlapping_rounds`（`room_handler.py:2122/2180/2209`，钩子 :7385）已落地，`OVERLAP_DEDUP` 真实（`jianying_draft.py:666`）；但 `valorant_broadcast.py:324-329` 强终态快路径（`strong_terminal_count>=2 and run_count>=2`）命中即 `return run_start`，**无"回退到更长游程"机制** | 回合起点被锁在回合中段的闪变帧上，去重只能掩盖 |
| ~~一次性分析的 sidecar 有被孤儿化的窗口~~ **已修（§11.3）**：`analysis_handlers.py:553-559 / 712-716` 在落盘前重取 `record_manifest_path or record_output_path`，注释即写明分析期间录像会定稿改名 | 已收口 |
| ~~`is_analysis_stale` 单看 mtime 判新鲜度~~ **已修（#14）**：`persistence.py:372` 现在是 mtime 变化 + 体积相同 ⇒ 视为无害触碰（复制/搬移/touch 不再静默丢掉分析）；旧 sidecar 没有 `video_size` 时保持严格判据 | 已收口 |
| ~~`flush_pending_room_saves` 无生产调用点~~ **已修（#1）**：`main.py:448` 在退出路径调用 `flush_pending_room_saves(fsync=True)` | 已收口 |
| 断连期非白名单消息静默丢弃 —— 初版判断有误（`useWebSocket` 已弹提示），**残余的队列溢出丢消息也已收口（§11.16）**：发件箱满不再 `shift()` 丢最旧还返回 true，改为 warn + `return false` | 已收口 |
| ~~`save_rooms` 浅校验 3 字段后整体落盘~~ **已收口（§11.32）**：WS 入口校验之外，内部自动保存路径（`schedule_save_rooms → save_rooms`）也接了 `room_payload_violation` 形状闸门 —— **整次拒绝、绝不静默丢条目** | `persistence.py:107` + `tests/test_save_rooms_validation.py` 10 条（负控 5/5） | 已收口 |
| ~~worker 超时用两层嵌套 `wait_for(shield(...))`~~ **经核为有意设计**：`room_handler.py:7662-7673` 注释说明超时后仍须持锁等到线程经 `cancel_check` 退出，以避免双 OCR 并发压垮 DirectML；真正的嵌套超时缺陷已在 #14 修掉 | 不是缺陷 |
| ~~`repair_recording` 同步 `timeout=600` 占 executor 槽~~ **已修（#14）**：`recording_handlers.py:288-306` 走专用 `repair_executor`，不再占用 6 槽的桥接池 | 已收口 |
| ~~`dependency_manager` 用 `assert proc.stdout is not None`~~ **已修**：全文件已无 `assert proc` 形态（`-O` 下 assert 被移除的规范违背已消除）| 已收口 |
| ~~两套分段录制并存~~ **已收口**：生产沿是 `shared_ingest.build_segmented_recording_command` + `_finalize_segmented_manifest`；`recorder/segmented.py` 的 `SegmentedRecorder` **无任何生产 import**（CLAUDE.md §5 早就是这么标的）⇒ 是死实现而非「两套都在跑」，已删（§11.16-B） | `recorder/segmented.py:138` vs `shared_ingest.py:877`附近 | ~~分叉风险~~ 已消除 |
| settings 双写源部分收口 **（仍开，属 #12 拆分线）**：`main.ts` 仍走独立 `app-settings.json` | 迁移未完成 |
| i18n 中文未过 `t()` —— 棘轮经实测放弃（§11.11）；但本行原附带的「守卫是 vitest ⇒ 永不在 CI 跑」**已失效**：vitest 已进 CI（#2，双 OS）| 剩文案本身，不再是门禁缺口 |

### 7.4 `SUBSYSTEM_ANALYSIS.md` 全量分诊（41 条风险，逐条对代码核过）

**已修 13 / 文档描述错误 7 / 仍开 16 / 无法判定 9。**（§11.18-G：`_RoomsThrottle` 与 drain coalesce "双实现"经核是误判，已改为文档描述错误并补 4 条可执行断言）（第九批：`broadcast_hub` 丢消息检测器不可信 §11.17；第十批：持续分析能力闸 + 导出簇死副本 + handler 覆盖率 §11.18）
已修的代表：预览 CRF 双基线（`mse_streamer.py:175-180` 现与共享进样同源）、播放器终态无自愈（`mediaSourcePlayer.ts:1124-1213` + `previewRecovery.ts`）、`align_rooms` 死码、双通道对齐、`create_clip_snapshot` 越界校验（`timeline_handlers.py:71`）、16 条重复死路由、`_export_total/_completed` 无 batch 隔离（`export_handlers.py:48/102`）、`output_dir` 默认值（全仓 8 处统一 `~/LSC/output`）、`PYTHONPATH`/`stop.signal` 文档已按代码改正。
仍开的集中在：**编排底座**（双 WS 入口与端口、`call()` 超时"结果未知"无重试语义、loop 关闭时广播静默丢 `broadcast_hub.py:65-67`、`_RoomsThrottle` 与 drain coalesce 双实现 :4487-4500）、**分析器非纯函数**（`detect_valorant_rounds_ocr` 读写调用方 state）、**取消语义**（watchdog 强杀后 cancel 是否误报，无法判定）。

> 41 条之外新掉出来一条（不计入上面的分诊计数）：`call()` **满员即时抛错**而不是有界等待，
> 使"一次添加 8~12 个房间"这种正常操作变成用户可见失败。已收口，见 §11.32-C ——
> 它是被一条 **在 HEAD 上就红 3/3** 的用例带出来的，不是靠读代码想出来的。

---

## 8. 发布工程（事实）

- ~~标签全集：`v1.0.11 v1.0.16 v1.0.17 v2.0.0 v3.0.0 v3.0.21 v3.0.22 v3.0.23`。~~
  **2026-09-19 更正：这条"全集"本身就漏了两个标签。**实测 `git tag -l 'v*'` 全集为
  `v1.0.0(06-28) v1.5.0(07-02) v2.0.0(07-05) v3.0.0(07-16) v3.0.21 v3.0.22(08-14) v3.0.23(09-01 21:26) v1.0.11(09-01 22:00) v1.0.16(09-13) v1.0.17(09-16)`。
  漏掉的 **`v1.5.0`** 直接改变了结论：即便回到 1.x 线，`v1.0.18` 也是**同线倒退**，
  永远发不出去 —— 下一号必须是 **≥ v1.5.1**（本分支定为 **v1.6.0**）。
  教训入型：**"全集"类断言必须由一条命令现场生成**，手写清单一定会旧。
- **2026-09-01 21:26:49 打 `v3.0.23`，同一天 22:00:43 打 `v1.0.11`** ⇒ 版本号倒退。当前 `package.json` 为 `1.0.17`。
- **2026-09-19 决策：发布线定为 1.x**（v2/v3 是 2026-07 起的编号实验，9/1 已实际回到 1.x 并连发到 v1.0.17）。
  CI 的 Release integrity 据此改成：① tag 必须是 `v1.x.y`；② **只与同线（`v1.*`）比**是否倒退
  （旧口径拿全标签比，会用废弃线把 1.x 拦死）；③ 跨线更低时 `::warning::` 响亮提醒而不是失败。
- **顺带掉出的新事实：同一个版本号写在三处，且其中两处是旧的** ——
  `lsc-electron/package.json=1.0.17`、`pyproject.toml=1.0.16`、`package-lock.json=1.0.11`。
  旧守卫只比 `package.json`，所以"发版没同步"这件事**不可见**（lock 已经落后 6 个版本）。
  现在三处一起比，不等即拒绝发布；本次已把 `pyproject` 与 lock 同步到 `1.0.17`（见 §11.33-B）。
- 后果（推断，机制是事实）：更新检测用 `compareVersions(local, remote)` 比 `[major,minor,patch]` 数值（`CLAUDE.md §9.5.2`）。任何装着 v2/v3 构建的用户点"检查更新"都会得到"已是最新版本"，且 `releases/latest` API 返回的是按日期最新的 v1.0.17 ⇒ 升级通道对这部分用户永久关闭。
- `v1.0.12 ~ v1.0.15` **无标签**（提交 `24aad30` 是给 README 补写这几个版本的"版本历史补齐"）⇒ 基于 tag 的发布流程没有被一致执行；`ci.yml` 的 release job 只在 `v*` tag 上跑，这几个版本是如何发出去的 **待验证**。
- `electron-typecheck`/`test` job 在 tag push 时也会跑（无 ref 条件），`release` 有 `needs` ⇒ 这部分是对的。
- 无崩溃/错误上报是**有意的隐私决策**（`PRIVACY.md §2`，全仓无 Sentry/Bugsnag/AppCenter），因此 §7.1 那类问题只能靠现场日志。

---

## 9. 优先级建议

### P0（本周期内，先做，成本都是小时级）

1. **让 vitest 进 CI**，并把 `i18n/coverage.test.ts`、`localFileMseSource.test.ts` 这类真行为守卫跑起来。当前那条红用例正是这条门禁缺失的直接产物。
2. **修 `localFileMseSource.test.ts:659`**，或按 §7.3 的"稀疏检查点索引"正式解决；无论哪个，都不要把守卫删掉来变绿。
3. **把 `ruff check lsc/ python-backend/` 恢复到 0**（15 处，10 处可 `--fix`），**安装 pre-commit 钩子**（配置文件已经写好了，只是从没生效过）。
4. `.gitignore` 补 `.mypy_cache/`、`.ruff_cache/`、`artifacts/`、`data/*.orig`。
5. 把 `alignment_handlers.py:15%` / `export_handlers.py:16%` 提到能守住既有红线，或**明确接受**并在 `CLAUDE.md` 写下"这两个模块只有文本守卫"。**待验证**：哪一个是想要的口径。

### P1（一个迭代，需要设计）

6. **全局 FFmpeg/资源准入控制器**（§6.4）：一个中央记账点，替代"再收紧一个 `Semaphore(1)`"。它同时是 §7.1 时钟冻结最可能的根因所在，且做完后可以直接把"争抢"这个假设变成可观测事实。
7. **预览时钟可诊断性**（§7.1）：周期性 WARN + 滞后增长自动开 `_debug`。
8. **`room_handler.py` 拆分**：先切 §3.1 里"可分性最高"的四块（settings+凭证 230 / 传输 245 / 常量墙 100 / 状态投影 290），每块都有明确边界。**前置条件**：先做 P0.1 和 P2.10，否则拆完只剩文本守卫。顺手清掉 `:6189` 那段误导注释、给 `_onnx_inference_semaphore` 别名加断言或合并命名。
9. **平台适配器失效可发现性**（§6.1/§6.2）：加 `parser_stale` 类错误码 + 设置页一键"适配器自检"（复用现成 `scripts/platform_acceptance.py` 的 1,362 行），并给 Cookie 加"是否真能拉到流"的活性判定（现在只有 `configured/count`）。
10. **决定 V2 的命运**（§6.5）：要么给它 UI 开关 + 进 CI 的一次真网冒烟，要么降级为"实验分支"并从 `CLAUDE.md §4.3` 的错误码表旁边写清楚。2,271 行悬着是最差状态。
11. **`shared_ingest_enabled` 口径统一**（§7.2）：改 `CLAUDE.md` 还是改生产默认值，这是一个产品决定，需要用户拍。

### P2（技术债，择机）

12. 数据集出仓（Release 资产或 LFS + `LSC_VALORANT_DATA_DIR`，脚本已是树外约定）；`git filter-repo` 是历史重写，**需用户明确授权**。
13. 死代码 ≈1,626 行（`mse_sender` 96、`export_service` 449 + `lsc/core/__init__.py` 死 barrel 36、`cancellable_ffmpeg` 可移部分 93、`error_stats` 106、`sound_detector` 299、`frame_capture` 262、`valorant_observer_scoreboard` 224、`generic_plugin` 空 stub 11）。**注意**：`CLAUDE.md §11.5` 把 `scene_analysis.py` 归入"遗留分支"是**错的** —— 它被 `generic_plugin.py:32` 和 `room_handler.py:1428/3807/3811` 生产 import（覆盖率仅 6%）。清理前须先确认那几个"兼容包装"自身有没有调用者。
14. 发布版本序列修复（避免再出现 v3→v1 倒退；补齐缺失 tag 或改写 §9）。
15. 前端 lint（eslint）从 0 建立；`Workbench/index.tsx` 按 §3.2 的 5 个簇抽 hook。

---

## 10. 遗留待验证清单

1. 远程 CI（GitHub Actions）当前是否真的红 —— 本机未认证、API 限流，未取到 run 历史。**（`gh auth login` 后 `gh run list --repo Lawrence7y/LSC` 即可）**
2. 用户机器上 `settings.json` 的 `shared_ingest_enabled` 实际值（决定 §7.2 的影响面）。
3. `alignment_handlers`/`export_handlers` 的低覆盖率是"测试没接上"还是"这些闭包本就只能由 WS 端到端驱动"。
4. `set_content_offset` 双写路径：前端是否仍在调用（`SUBSYSTEM_ANALYSIS` 标"待核实"，本轮未定论）。
5. watchdog 强杀 FFmpeg 之后取消语义是否误报失败（`SUBSYSTEM_ANALYSIS` 原样遗留）。
6. v1.0.12–15 当时如何发布（无 tag，`ci.yml` 的 release job 不应产出）。
7. `localFileMseSource.test.ts:659` 是随 v1.0.17 就红、还是环境相关 —— 需在 CI 或第二台机器上跑 vitest 定论。

---

## 11. 执行状态（2026-09-18 修复批次，分支 `fix/debt-20260918`）

验证方式统一为：改完跑 `ruff check lsc/ python-backend/` + 全量 `pytest` + `vitest run` + 两个工程的 `tsc --noEmit`。
**本节的"最终"数字是第一批（2026-09-18 上午）的快照**：ruff All checks passed / pytest 2083 /
vitest 275 / tsc 0。后续 13 批的累计与最新门禁实测值以
CHANGELOG 的「质量门禁」小节为准（`arg-type` 与 `no-untyped-def` 的收敛过程见 §11.6 → §11.12 → §11.22）。

| 条目 | 状态 | 证据 |
| :--- | :--- | :--- |
| P0-1 ruff 15 → 0 | ✅ 完成 | `ruff check lsc/ python-backend/` → All checks passed |
| P0-1 pre-commit 钩子安装 + 版本对齐 | ✅ 完成 | `.git/hooks/pre-commit` 已生成；本地/钩子/CI 三处同为 ruff 0.15.16；`ruff-format` 移除并注明 99/109 文件的 blast radius |
| P0-2 vitest 进 CI（双 OS） | ✅ 完成 | `ci.yml` 新增 `frontend-tests` job，并加入 `release.needs` |
| P0-2 覆盖率含 `python-backend` + 地板 | ✅ 完成 | `--cov=python-backend --cov-fail-under=55`（实测合并 62%） |
| P0-2 mypy 注释过期数字 | ✅ 更正 | 112/27 实测值入注释；假 issue 引用去掉。**mypy 本身仍非阻塞**（112 条未清） |
| P0-3 不稳定守卫 | ✅ 完成 | 4 处墙钟预算 → 条件等待 + 显式 timeout；并发下 2/2 全绿；断言未放宽 |
| P0-4 `.gitignore` | ✅ 完成 | 7 条路径 `git check-ignore` 全部 OK；`git status` 未跟踪只剩 3 个合法文件 |
| P1-1 5 条假 skip | ✅ 完成 | 改为直接属性访问（`grep pytest.skip` 在该文件为 0）；19 条用例照常跑 |
| P1-1 `room_handler.py:6189` 误导注释 | ✅ 完成 | 改写为"唯一实现 + `server.on` 抛 ValueError" |
| P1-1 别名信号量 | ✅ 完成 | `_onnx_inference_semaphore` 删除，acquire 点用真名 + 注明复用同一槽 |
| P1-1 `flush_pending_room_saves` 无调用点 | ✅ 完成 | 接入 `main.py stop()` 末段 + 1 条接线断言 |
| P1-1 `assert proc.stdout` | ✅ 完成 | 显式判断 + `_emit_error` + `return False` |
| P1-2 shared_ingest 文档口径 | ✅ 文档已更正 | §3.2 + §7.3.1 改为"生产实际是共享进样"。**代码默认值是否要改回，是产品决定，仍待用户拍** |
| P1-2 `dvr_output_path 恒为空` | ✅ 完成 | §8.8 更正为"非恒空；共享进样路径通常为空" |
| P1-2 `handle_align_preview_audio` 陈旧段 | ✅ 完成 | §5.3 更正 |
| P1-2 WS 端口第三入口 | ✅ 文档已建档 | §2.2 加入三条入口说明。**代码尚未收敛为单一事实源** |
| P1-2 `output_dir` / `_deferred_export_jobs` / `scene_analysis` 三处文档错 | ✅ 完成 | §11.5 表与另注已更正；`scene_analysis` 明确标注**不得删除** |
| P1-3 预览冻结可诊断性 | ✅ 完成 | `_warnFrozenProgression()` + `_frozenSinceMs` 累计量 + 1 条真行为测试 |
| P1-3 死代码清理 | ✅ 完成 | 删 6 模块 + 1 类 + 空 barrel，净减约 1,565 行实现 / 约 1,000 行测试；`pytest` 2083 全绿 |
| §7.4 全局资源上界**建档** | ✅ 文档完成 | 新增 `CLAUDE.md` §7.4（上界清单 + "33 个派生点无总量封顶"事实 + 新派生点约束）。**中央准入控制器的代码实现仍未做** |

### 未完成（明确遗留，勿当成已收口）

1. **mypy 112 条**：仍为 advisory。清零后才能转阻塞。
2. **全局 FFmpeg/资源准入控制器**：核心与部分站点已落地（见 §11.1）；`capture.py` / `shared_ingest.py` / `exporter/clip.py` / ~~`segmented.py`~~（非生产路径，已删，§11.16-B）/ `probe.py` / `room_utils.py` **仍未接线，那些派生点至今无闸**。这是 §7.1"根因仍未定论"最可能的真正修复点。
3. **稀疏检查点索引**（P1-2 第 2 步）：远定位仍会顺序扫描；同时 `_trimQueueBeforeTarget` 只在 `_sawMedia` 之前修剪，出画后无字节上界断言。
4. **`room_handler.py`（10,970 行）/ `Workbench/index.tsx`（5,259 行）拆分**：未动。
5. **剪映候选 §7.1 根因**（`valorant_broadcast.py:324-329` 强终态快路径无"回退到更长游程"）：未动。
6. **平台适配器可观测性**：`parser_stale` 类错误码、Cookie 活性判定、适配器自检 —— 全部未动（§6.1/§6.2/§6.3）。
7. **V2 管线命运**（2,271 行无 UI 开关）：未决定。
8. **数据集出仓**（98.7% tracked 字节）：需历史重写，属需用户授权的动作。
9. **`i18n` 269 行硬编码中文**、**eslint 缺失**、**版本标签倒退修复**：未动。

### 11.1 追加：全局派生闸（§6.4 / 新 §7.4 的代码落地）

已在 `lsc/utils/process_launcher.py` 建出中央记账点：`spawn_slot(category, timeout_sec)`
（拿不到名额抛 `SpawnBudgetExhausted`）、`note_child` / `forget_child`（存活分类账）、
`budget_snapshot()`（可观测出口）。上限经环境变量可调，默认
`MAX_CONCURRENT_SPAWNS=2`、`MAX_LIVE_CHILDREN=48`。

三条刻意的设计边界，勿在后续改动中破坏：

1. **名额只覆盖启动瞬间**。长生命周期进程在 `Popen` 返回后立即 `note_child` 记账并释放名额 ——
   否则一个录制进程永久占闸，12 房直接死锁。
2. **存活超限只 WARNING，不强杀**。杀掉正在录制的进程造成的数据损失比争抢更糟；
   硬拒绝只发生在"还没开始"这一步。
3. **`run_hidden` 路径 fail-open（5s 预算）**。它的调用方含编排线程与 asyncio 线程上的短探测，
   硬等 30s 会直接撞上 §11.3"禁止占用编排线程"红线（编排线程一冻，全部 `orchestrator.call` 一起超时）。
   所以短命令语义是"削峰 + 留痕"，长生命周期站点才是硬封顶。放行次数计入
   `failopen_transient_total`，不是静默降级。

已接线站点：`run_hidden`（一处覆盖 `ocr_detector` / `scene_analysis` / `valorant_ocr_rounds` /
`gpu_ffmpeg` / `recording_repair` / `room_handler`）、`MseStreamer` 预览启动与两条 teardown。
`budget_snapshot()` 已挂进 `get_system_stats` 与 `low_tick` 的系统快照广播 ——
下次预览冻结时，"当时有多少 FFmpeg 在跑"第一次成为可查事实。

**未接线（仍是无闸派生点，遗留）**：`recorder/capture.py:487`（注意它已自带一把信号量，
:270 有"从未成功 acquire 过不得 release"的约束，接线要先读）、`shared_ingest.py` 上游与两个 sink、
`exporter/clip.py:240`、`recorder/segmented.py:138`、`platforms/probe.py`、`room_utils.py`。

### 11.2 本轮新增的反模式证据（"重构税"第 3、4 次实证）

为让 mypy 看得见 `available_end` 非空而在 `valorant_broadcast.py` 两处 `if` 追加显式收窄
（语义完全不变，因 `lookahead_incomplete` 的定义 :1943 已含 `available_end is not None`），
以及把派生闸挂进 `collect_system_stats(extra=...)`，各打断**一条源码文本断言**：
`test_finalize_audit_does_not_return_pending_lookahead` 与
`test_room_handler_system_stats_include_ingest_diagnostics`。两者已改为断言不变量而非整行排版。

连同 §11 早先那两条（`_finalize_now` 分支合并打断 2 条），本轮共 **4 次**：
一次语义等价的改动被文本守卫判为回归。这组数据本身就是把 141 条文本守卫逐步换成
行为守卫的理由，也是给 `room_handler.py` / `Workbench/index.tsx` 做拆分前必须先解决的事。

### 11.3 追加：一次性分析 sidecar 孤儿化（§7.3 那条已修）+ 一处自我更正

**已修**：`analysis_handlers.py` 的 `analysis_storage_path` 在分析**启动时**捕获、几分钟后落盘时
才使用；期间录像定稿改名（`_录制中.mp4` → `_至_….mp4`）就会把 sidecar 写到已不存在的录像名上，
永不被 `get_analysis_result` 读回。两处落盘（单房 `:552`、多房 `:703`）现在都在写前重取一次
房间当前路径（取不到才沿用捕获值），语义与持续分析侧的 `_sync_analysis_save_path` 一致，无新增管线。

**自我更正（本审计第 5 处）**：报告初版把"断连期非白名单消息静默丢弃"列为缺陷，**不成立** ——
`useWebSocket.ts:859-865` 对 `send()` 返回 false 会弹警告。真正的残余只是一个窄口径漏口：
入队成功的消息返回 `true`，而队列满时 `websocket.ts:467` 会 `shift()` 丢掉最旧的一条，
该条用户已被告知"已发出"。**未改代码**（改动需引入逐消息回执，属新机制而非修缺陷），只更正结论。

### 11.4 派生闸接线的边界与判断（为什么没把 33 个点全接上）

本轮接了 `run_hidden`（短命令统一入口，一处覆盖 6 个模块）与 `MseStreamer` 预览启动/teardown。
**录制与导出路径刻意未接**，理由是设计问题而非偷懒：

- `recorder/capture.py:start()` 的 `try` 里只显式处理 `FileNotFoundError` 等启动异常，
  闸抛的 `SpawnBudgetExhausted` 会以未处理异常冒到调用方；`exporter/clip.py` 同形。
  接闸之前必须先回答**"一次被拒绝的录制启动如何报给用户、是否自动重试"** ——
  这是产品决策，不是清理。仓促接上会把"争抢导致卡顿"换成"点录制没反应/直接失败"，
  对正在直播的用户是更严重的回归。
- `capture.py` 有 **4 处** `self._process = None`（:636/:684/:728/:783），没有单一 teardown 咽喉点；
  逐处补 `forget_child` 正是本报告批评过的"多份手工同步清单"反模式，一旦漏一处就是永久占额，
  最终表现为"录到第 N 路突然全录不了"。
- **可行的下一步**：对录制/导出只闸"启动瞬间"（`with spawn_slot(...): Popen(...)`，
  `with` 自身保证归还，零泄漏风险），不做存活记账 —— 这已经足以消掉"扎堆启动"这个失效模式；
  存活计数交给 `MAX_ROOMS`/导出信号量本身。接每个站点时同步把它已有的异常分支扩一条
  `except SpawnBudgetExhausted`，并保持既有的"启动失败即 `_set_status(ERROR)` + return False"形状。

`shared_ingest.py`（上游 + 两个 sink）、`platforms/probe.py`、`room_utils.py` 同理待接（原列的 `segmented.py` 已随死模块删除，§11.16-B）。

### 11.5 窄口径缺陷批次（第二轮）与两处"文档误判"结论

**已修**（逐项带行为测试，全量 pytest 2106 passed / ruff 0）：

| 缺陷 | 修法 | 测试 |
| :--- | :--- | :--- |
| `save_rooms` 比 `add_room` 松（无 URL 结构校验、无房间数上限 ⇒ 可绕过 `MAX_ROOMS`） | 抽出 `_room_url_shape()` 纯结构判据，单条与批量入口**共用一份规则** | `tests/test_save_rooms_validation.py` 6 条 |
| `repair_recording`（同步、上限 600s）挤占 6 槽 `rec` 池 | 新增 1 槽 `_repair_executor`，并纳入关停清单 | 全量回归 |
| 删房不清账本（`_reset_epoch_scoped_clip_state` 从未被 remove 路径调用；两个标签计数器全仓无 pop） | 单一咽喉点 `_forget_room_registries` 委托既有清理 | 2 条，红→绿双向验证 |
| 分析新鲜度只看 mtime ⇒ 复制/搬移/touch 即丢弃数小时成果 | sidecar 增 `video_size` 作第二证据；缺该字段的旧 sidecar 维持严格 | `tests/test_analysis_freshness.py` 6 条 + 改写 1 条旧断言 |

**经核查判定不改代码（本审计第 6、7 处自我更正）**：

1. `wait_for(shield(fut))` 两层嵌套**不是缺陷**。注释明确："超时后仍须持锁等到线程经
   cancel_check 退出，避免双 OCR 并发压垮 DirectML"、"不得 cancel 协程后提前释放 semaphore"；
   停止路径为 grace(`_SCAN_ABORT_GRACE_SEC`) → 硬上限(`_SCAN_ABORT_HARD_SEC`) → 强制释放，
   逐级 WARNING/ERROR（`room_handler.py:10670-10695`）。拆掉即违反 DirectML 单上下文约束。
2. `useWebSocket` 断连"静默丢弃"不成立（已在 §11.3 记录）。

**明确遗留（已定位、故意未改）**：`autoLaunch` / `minimizeToTray` 同时存在于
`app-settings.json`（Electron，实际生效方）与 `settings.json.appSettings`（后端），
`locale` 与 `language` 同概念双键名。收敛需改持久化格式并做迁移，风险是用户设置丢失；
建议方向：以 main 进程为唯一写入方，后端 `appSettings` 只作展示镜像，并统一成一个键名 + 一次性迁移。

### 11.6 mypy：改为"阻塞 + 按错误码精确豁免"的债务棘轮

审计原判："`mypy` 非阻塞，注释数字过期（87 → 实测 112/109）"。落地方式不是硬清 108 条，
而是**先把门禁变成真的**：CI 去掉 `|| echo ::warning::`，存量以
`[[tool.mypy.overrides]] disable_error_code = [...]` 按模块 × 按错误码豁免（由 mypy 输出自动生成，
26 个模块 / 108 条）。相比 `ignore_errors = true`，这保留了检测能力：豁免模块里出现
**新的错误码类型**仍会红。已用 `name-defined` 探针实测确认该性质。

清零顺序（按条数）：`lsc.platforms.acceptance` 17、`lsc.core.orchestrator` 14、
`lsc.analyzer.valorant_broadcast` 14、`platforms.probe` 7、`platforms.douyin` 7、`platforms.registry` 6。
提醒：其中若干 `float(Optional)` 是假阳性（外层 `try/except TypeError` 或布尔变量隐含收窄），
应通过**显式收窄**消除，不得塞 `or 0.0` 之类的假默认值掩盖。

另一个仍未解的问题：`python-backend`（20,108 行，含 `room_handler.py`）因目录名带连字符
不是合法包名，**至今完全不在 mypy 检查范围内**，配置注释也只是把它记为"可后续接入"。

### 11.7 第三轮：发布守卫、解析失效分类、mypy 棘轮

| 项 | 状态 | 验证方式 |
| :--- | :--- | :--- |
| `parser_stale` 错误码（§6.2 的"无法区分流下线与解析器过期"） | ✅ 已修 | `registry.parse_stream` 单一集中分类；4 个探针用例 + 确认 `offline` 判定路径不受影响 |
| 发布完整性守卫（§8 版本倒退 / 无 tag 发版） | ✅ 已加 | 本地把 4 个分支逐个复演，含用真实 tag 重现 `v1.0.17 < v3.0.23` 倒退并成功拦下 |
| mypy 转**阻塞** + 按模块×错误码精确豁免（§2.1） | ✅ 门禁已真 | 探针：向被豁免模块注入未豁免的 `name-defined` 即报错；`hidden_run_kwargs` 返回类型修正 109→108 |
| 平台自检入口 / Cookie 活性检测（§6.1/§6.3 的 b、c） | ⬜ 未做 | 需要一次真实网络探测的产品化入口，见 task #11 |
| 版本号编号决策（重新向前编号 or 清理 v2/v3 tag） | ⬜ 待用户决定 | 守卫会拦住下一次按现编号的发版 |

**全门（2026-09-18 第三轮收尾）**：`ruff` All checks passed / `mypy` Success / **pytest 2109 passed** / vitest 275 passed / 两个 `tsc` 无错误。

### 11.8 派生闸覆盖面的最终状态

| 站点 | 是否已闸 | 依据 |
| :--- | :--- | :--- |
| `run_hidden`（短命令统一入口：OCR 抽帧 / ffprobe / gpu 探测 / 修复） | ✅ | 一处覆盖 6 个调用模块；fail-open + 5s（编排线程约束） |
| `mse_streamer`（预览，12 房 × 4 路上限） | ✅ | 启动闸 + `note_child`/两条 teardown 注销 |
| `shared_ingest._launch_process`（上游 + 录制/预览 sink + 换源，**最大扇出**） | ✅ | 4 个调用点均已有 `except Exception` 兜底；只闸启动、不做存活记账；6s < `orchestrator.call` 10s |
| `recorder/capture.py:487` | ⬜ | 它自带 stderr 池 acquire/release 纪律（:269 注释），接闸要与那套语义核对；未动 |
| `exporter/clip.py`（240 是 CPU 回退路径，598 是主路径） | ⬜ | 两处失败形状不同，需分别核对；未动 |
| ~~`recorder/segmented.py`~~（已删）/ `platforms/probe.py` / `room_utils.py` | ⬜ | 未动（segmented 已出清单） |

结论：`§6.4 / §7.4` 的"总量无界"已从**完全无界**收敛为"**扇出最大的三条路径有界**"。
这不是清零，但覆盖了三次已记录事故中最可能的触发面（多房同时起流 + 分析短命令风暴 + 预览）。

## 11.9 第三批（2026-09-18 下午）：门禁范围、拆分第二批、可观测性，以及一个每次退出必触发的挂起缺陷

验证口径与前面一致：每条都写"改了什么 / 怎么证明 / 还剩什么"。全批结束时
`ruff check .` + `mypy` + `pytest` + `tsc ×2` + `vitest` 五道全绿
（**2134 passed / 275 frontend tests / 105 mypy files**）。

### A. 门禁范围：三道"看起来有、实际管不到"的闸

| 闸 | 之前的真实覆盖面 | 证据 | 现在 |
| :--- | :--- | :--- | :--- |
| `ci.yml` ruff | 只有 `lsc/ python-backend/` | 实测 `ruff check .` 报 **52** 条，其中 **24 条在 tests/**（从未被任何门禁检查过）、**13 条在 scripts/**（被 `--exit-zero` 明放） | ✅ 改成 `ruff check .` 全仓一条命令，52 条清零；顺带删掉 `--exit-zero` 那步 |
| `core-guard.yml` 的 `lint-core-python` | **常红** | 末步 `ruff format --check lsc/core/ lsc/analyzer/ python-backend/handlers/` 实测 **34/41 文件会被重排** ⇒ 只要动核心路径该 job 必红；且 `pip install "ruff>=0.5"` 未钉版本 | ✅ 删除该 job（ruff 已由 ci.yml 全仓覆盖；`tsconfig.electron.json` 那条独有检查保留），删除理由与复现数字写在工作流头部注释 |
| `mypy` | 只有 `lsc`，**python-backend 整个不在范围** | `pyproject` 原注释写的原因是"目录名含连字符不是合法包名"。**这条原因是错的**：真正的阻塞物是 `python-backend/__init__.py`（内容只有一行 `# Python backend package`）——该目录从来不是包（靠 `sys.path` 平铺导入 `handlers.*`），有这个文件反而逼 mypy 按包解析并报错 | ✅ 删掉那个残留文件 + `mypy_path`/`files` 配好 ⇒ **79 → 105 个文件**，`--show-error-codes` 阻塞 |

**"路径清单"本身就是第二份要手工同步的表**（§5 的同一类问题）。所以三处都往
"一条命令覆盖全仓"的方向收，而不是再补一份清单。

`scripts/valorant_vision/` 那批离线训练/评估脚本报的 **13** 条里有两条是真问题：

- `eval_source_dataset.py`：本地 `gates` 表算出的 `all_passed` **算了但没人用**，
  释放裁决来自另一张表 `check_all_gates`，而打印的 5 行 PASS/FAIL 用的是本地表
  ⇒ 两张表分叉时会出现"逐行 PASS 但结论 FAILED"（或反向）。已改成
  `gates_passed = all_passed and not failures` 并让两种分叉各打一种结论行；
  原来那段"本脚本的 all_passed 只是为可读性保留"的注释与代码不符，已更正。
  （核对过：`check_all_gates` 是本地表的超集，所以这次收紧今天是空操作，
  它是**绊线**不是行为变化。）
- `recover_and_backfill_broadcast.py`：返回注解引用了从未导入的
  `JianyingDraftResult`（靠 `from __future__ import annotations` 才没在运行时炸）。
  已补导入。

### B. 拆分第二批：6 条凭证路由

`room_handler.py` **11038 → 10874 行**。这一簇此前每个处理器各自在函数体内
`import`（**11 处**），既是 `CLAUDE.md §11.4` 禁止的写法，也让真实依赖对读者和
静态检查都不可见——AST 扫描会把它读成"这一簇零外部依赖"。全部提到模块级后落到
`handlers/credential_handlers.py`（192 行），注入面只 `server` + 一个 logger。

搬家带来的**唯一**测试改动暴露了一个通用陷阱：三条
`test_*_cookie_save_refreshes_invalidated_provider` 原本 patch 的是
**源模块**（`cookie_helper.save_*`、`credentials.get_default_credential_provider`），
这在函数级 import 下能生效（每次调用现查），改成模块级 `from X import Y` 后
绑定发生在导入期，patch 源模块就**悄悄失效**了。已把 patch 目标改成新模块自身
的绑定（`handlers.credential_handlers.*`）。这正是 §5 说的"测试看着在跑、其实
什么都没测"。

**没有继续做的部分（附实测理由，不是回避）**：settings/host 那一簇（
`get_settings`/`save_settings`/`check_dependencies`/`get_system_stats`）依赖
`load_settings`、`SETTINGS_FILE`、`_settings_cache*` 三个 room_handler 模块全局。
现有测试有 **15+ 处**直接 `monkeypatch.setattr(rh, "SETTINGS_FILE" | "_settings_cache" | "load_settings", …)`。
把 settings store 搬去独立模块会造成两种事故：(1) patch 失效 ⇒ 测试静默测空气；
(2) 更糟的是 `SETTINGS_FILE` 的 patch 只改到 room_handler 的绑定上，
`save_settings` 仍按真实路径写 ⇒ **单测直接写用户真实的 `settings.json`**。
前置条件仍然是 §11.6 说的"先把文本守卫与 monkeypatch 目标换成显式注入"。

### C. P2-2 平台失效可观测性

新增 `lsc/platforms/diagnostics.py`：按 `(platform, kind)` 累计的降级计数 +
`degradation_snapshot()`，接入 `get_system_stats`。三个降级源接进来：

1. `parser_stale`（§11.7 已分类，现在有了计数）；
2. **虎牙 SDK 改写回退**（`huya.py` 原来有 4 条 `return raw` 的静默回退路径，
   而 raw 恰是"只推一段 GOP 就 EOF"的那个坏形状——虎牙改版的表现是"预览卡几秒
   没了"，日志里查不到原因）。现在每个原因告警一次并计数；
3. **凭据实测失效**（见下面的自我更正）。

同时把 `collect_system_stats(extra=…)` 从两个调用点（`low_tick` 周期广播、
`get_system_stats` 按需查询）收成**一个** `_system_stats_extra()`——
两份 dict 各写一遍必然分叉，`platform_degradation` 第一次就只进了按需那条，
顶栏周期刷新看不到。

**我在这条上判断错了一次，记录如下**：我最初写"
`CredentialProvider.invalidate()` 全仓**零调用点**、`reason` 被 `del` 掉"，并照此
在 `recovery_policy` 里新写了一个 `note_credential_auth_failure`。这是**错的**——
`resolver.py` 里早有 `_invalidate_credentials_on_auth_failure`，被三处调用
（resolve 期与选线路期），`reason` 一路带到 `invalidate()`。是
`test_platform_probe_and_resolver` 三条既有用例在我给共享判据加了
`capabilities.credential_kinds` 预筛之后变红，才发现既有契约长这样。已修正为：

- 把那个私有助手**升格为 `credentials.invalidate_credentials_on_auth_failure`
  唯一入口**，计数放进去；`recovery_policy` 删掉自己那份实现，只在
  `mark_failed_candidate`（10 个调用方共用的 ingest 期收口）补上
  **"ingest 期也调用它"** ——这才是真正的缺口：resolve 期早有人管，录制/预览
  中途才暴露的登录失效此前没人标记。
- `invalidate()` 的 `reason` 不再被丢弃，改成存 `(时刻, 原因)`，新增
  `invalidation_info()`；Settings 三条 `get_*_cookie_status` 与
  `get_platform_credential_status` 因此能同时报"配了没"和"还好不好"
  （`credential_status` / `credential_invalidated_at` / `_reason`）。
- 展示层收敛在 `CookieStatusLine`：**只有确实配过** Cookie 才把 INVALID 显示成
  "实测已失效"，避免给不需要凭据的平台（direct/huya）报假信号。

**P2-2c 平台自检**：`lsc/platforms/acceptance.py` 那套控制面验收此前只有开发者
跑 `scripts/platform_acceptance.py` 才碰得到。新增 `run_platform_self_check`
路由 + Settings「平台自检」面板，固定
`record_dir="" / recording=False / preview=False`（即 acceptance 自己文档里标注的
"CI 可安全执行"那一档：不启 FFmpeg、不落盘），跑在 `run_in_executor(None, …)`
（复用循环自带线程池，不另造池），同一时刻只允许一个在飞，URL 判据
**注入** `room_handler._room_url_shape` 而不是自带一套。超时后线程无法强制取消
这件事在模块头写明了，`run_acceptance` 内部各步自带 deadline。

### D. 范围一开就掉出来的真缺陷：退出挂起（P0 级）

`main.stop()` 原来是：

```python
asyncio.run_coroutine_threadsafe(self.server._server.close(), self._loop)   # ← 每次退出必抛
```

实测链条（三条都可复现，见 `tests/test_backend_shutdown.py`）：

1. `inspect.iscoroutinefunction(WebSocketServer.close)` 为 **False**（同步方法）；
2. 于是传进去的是 `None`，`run_coroutine_threadsafe` 抛
   `TypeError: A coroutine object is required`；
3. 紧邻的 `except RuntimeError: pass` **抓不到 TypeError** ⇒ 异常冲出 `stop()`，
   排在它后面的 `self._main_stop.set()` 永远不执行；
4. 主线程停在 `self._main_stop.wait()` 上 ⇒ **退出应用后 python 后端进程残留**。

这解释了长期存在的"打包前要手工杀残留进程"现象（记忆里也记着这条操作步骤）。
修法：改成 `call_soon_threadsafe(srv.close)`，并把 ws 那两步整体包进
`try/finally`——**清理链后半段（放行主线程、join、最终落盘）必须无条件执行**；
两处 `except` 从 `RuntimeError` 放宽到 `Exception` 且带日志（不是 `: pass`），
`flush_pending_room_saves` 那步同理由 `OSError` 放宽。

新增 6 条用例。其中 `test_stop_schedules_server_close_on_loop_thread` 做过**负控**：
把旧写法塞回去 ⇒ 该用例变红（另一条"不变量"用例仍绿，因为它只保证
`_main_stop` 被 set；一条钉机制、一条钉不变量，是有意分工）。

这条用例最初还把同 session 里 9 个无关用例毒红了——`stop()` 会关掉
**模块级线程池**（日志 `executors_shutdown: 5`），后续跑 executor 的用例全部变成
`RuntimeError: cannot schedule new futures after shutdown`。已加 autouse fixture
隔离。**教训：新增任何触碰全局生命周期的测试，必须当轮就跑全量套件**，
单独跑该文件是绿的。

### E. 顺带清掉的类型层真问题（非机械）

| 位置 | 事实 | 处置 |
| :--- | :--- | :--- |
| `resolver.py` 探测结果循环 | 上一行用 `getattr(probe, "failure_kind", "")` 容错，下一行直接 `probe.failure_detail` | 统一容错取值 |
| `acceptance.py` 选线路 | 往 `Mapping[str, ProbeResult]` 里塞 `{id: None}`，而 `select_stream_lease` 会对 values 取属性 | 缺探测就不放进字典 |
| `orchestrator.py:2200` | 同一表达式里两次 `probes.get(...)`，mypy 无法收窄 | 提到语句前取一次 |
| `controller.py` 导出异常兜底 | `ExportResult` 按 4 个位置参构造，`""` 落进了 **int 字段 `clip_index`**，`title` 也被丢掉 | 改关键字构造；本路径回调不读这两字段，所以故障此前不可见 |
| `error_messages.py` | 声明 `dict[str, str]`，而自己的 docstring 写着"suggestion 或 None" | 改 `dict[str, str \| None]` |
| `valorant_broadcast._split_merge_geometry_ok` | 用 `None in (…)` 判空，mypy 不收窄 ⇒ 9 条 `float - None` | 改逐个 `is None`（运行时语义不变） |
| `room_handler` 三处 | `frozenset` 分支里调 `reg.discard()`（谎报支持）；`ingest` 可能 None 却取属性；`state` 声明可空却在末尾下标写 | 收窄判据 / 局部变量 / 提前 `isinstance` |
| `persistence.load_analysis_results` | 顶层不是 dict 的侧车会让后续所有 `.get()` 崩 | 边界上判形状 |
| `jianying_handlers` | 同一个名字 `snap` 隔 230 行指两种快照类型 | 改名 `clip_snap` |
| `alignment_handlers` | 手写两处 `try: float(x.get(k)) except: 0.0`，与 `room_utils.safe_float` 重复 | 复用 `safe_float`（jianying 那处**不**复用：它 except 时要 `continue` 丢掉整条 clip，换默认值会把"缺失"伪装成 0 秒拉偏均值） |

我自己的一次**误修**也被门禁拦下：把上面 `probes.get` 改成海象表达式
`(p := …).x if p is not None else ""`——三元表达式的**条件先于两个分支求值**，
这是 NameError。`ruff F821` + `mypy used-before-def` 同时报出来，改成提到语句前。

### F. 这批之后 mypy 的真实欠债

关掉全部豁免实测：**355 条 / 42 文件**，其中 `no-untyped-def` **273** 条
（纯签名缺失，占 77%），`arg-type` 45、`assignment` 17、`call-overload` 12、
`no-any-return` 7。crash 类错误码（`operator` / `union-attr` / `no-redef` /
`dict-item`）已归零。棘轮按"模块 × 码"收紧过一轮（豁免段 43→42、4 段码表变窄），
并在 `credential_handlers.py` / `selfcheck_handlers.py` 这两个新模块上
**不留任何豁免**——新代码必须是全量受检的。

---

## 11.10 第四批：P2-1 稀疏检查点定位（§7.3 第一条关闭）

**原判**（§7.3）："`_locate` 只做『索引已覆盖→跳』，否则 `_seekToFileTime` 码率外推 +
16MB 窗找边界，之后仍顺序读" ⇒ 小时级录像 + 更远目标仍是最坏顺序扫。**成立，已修**。

**根因**：外推用的 `bytesPerSec` 只由**已扫到的头部区间**算出（`noteRate` 取
entries 首尾两点）。前段低码率 / 后段高码率的录像里，这个斜率与落点附近的真实
码率差两个数量级 ⇒ 字节位置被严重低估 ⇒ 差额全靠顺序读盘。

**为什么当初记为"需要新的 tfdt 解析、风险高"**：这条前提**是错的**——
`fmp4Box.readTfdtSeconds` 早就是 `export`，splitter 每段都在用它算 `tPtsSec`。
核对后直接复用，本批改动的解析部分**零新代码**。教训记在这里：
"这需要新解析"必须是查过的结论，不能是估计。

**实现**：
1. `probeBoundaryTime(buffer, baseOffset, track)` = `findFragmentBoundary` + 沿 box 链
   ≤3 步找 moof + `readTfdtSeconds`。全部复用主路径判据。
2. `_seekToFileTime`：第一枪仍用外推（等码率一发命中 ⇒ 今天的快路径与三条
   事故守卫行为不变），随后把每个探测点经 `_noteProbeAnchor` 登记进索引，
   并在**字节轴上二分** `[lo, hi]`，至多 `MAX_LOCATE_PROBE_ROUNDS = 4` 轮。
   二分与码率无关，所以变码率不再致命；4 轮 ×16MB 窗口是成本上限。
3. 接受条件**单向**：`0 ≤ 目标 − 落点时间 ≤ 20s`。晚于目标会真跳过内容
   （用户看到"画面直接没了那一段"），早一点只是多读几段。
4. 窗口几何不变（右端仍收敛在外推位置），因为 `_locateWindowStart` 是唯一算法点
   —— 2026-09-15 那条"向右扩会偏晚 ~1000s"的守卫继续成立。
5. `estimateByteOffset`：目标被两锚点夹住时改用**局部两点插值**，右侧无锚点才
   退回全局外推。原两条用例（纯外推 / 目标在首条目左侧）行为不变。
6. 检查点存在模块级 `_indexCache` 里 ⇒ 跨会话、跨多次 seek 复用。

**实测（负控在案）**：夹具 `400 段 ×1KB + 300 段 ×512KB`，目标文件轴 1650s。
- 轮数=1（等价于改前）：落在字节 **423,088** / 157,728,548，出画前需 **134** 次读盘；
- 轮数=4（现状）：落在 **116,829,404**，**4** 次探测出画，读盘次数 <60。
把 `MAX_LOCATE_PROBE_ROUNDS` 改回 1，新增的落点/读盘上界用例即变红。

**仍未收口的相邻项**：回看队列的内存上界只在**出画前**成立
（`_trimQueueBeforeTarget` 首行 `if (this._sawMedia) return`）——本批没动它，
因为二分反而让"出画前堆整段扫描区"的窗口变小了。§7.3 那一条保持原状。

**门禁**：`ruff check .` All checks passed / `mypy` Success(105) /
`pytest` 2134 / `tsc ×2` 0 错 / `vitest` 27 文件 **278** 用例（+3）。

---

## 11.11 试过但**主动放弃**的一件事：中文硬编码的 CI 棘轮

思路是对的：`coverage.test.ts` 只守"`t('…')` 里的文案要有英文"，
**压根没包 `t()` 的中文对它完全不可见** ⇒ 英文界面中英混排而 CI 是绿的。
于是想加一条"每文件未包 `t()` 的中文字面量行数 ≤ 基线"的单调棘轮。

实测后放弃，理由不是工作量而是**这个检测器不可信**：
用正则扫 `src/**/*.{ts,tsx}`（排除注释行、`console.*`、`t('…')` 入参）得到
**1197 行 / 48 个文件**，而按人工口径逐文件核对是 **~261 行 / 33 个文件**。
差额主要是 `types/index.ts` 的接口文档串、`case '中文':` 这类比较字面量、
`store` 里的内部状态描述 —— 都**不进界面**。

一个数字里 4 倍于真值的噪声，会让"基线又涨了"变成没人理的红灯。
**这与 §11.9-A 删掉 core-guard 常红 format 步骤是同一条判断**：
宁可没有这道闸，也不要一道教人忽略红灯的闸。

要真正关闭这条路，前置条件是二者之一（都已登记为后续工作，不在本批）：
1. 用 TypeScript Compiler API 走 AST，只对"进入 JSX / 作为 UI 文案传出的字符串"计数；
2. 或者按文件做一轮人工 sweep（需要真机看英文界面，本环境跑不了 Electron UI）。

---

## 11.12 第五批：把 mypy 剩下的**非机械码清到 0**，并修掉一条永不成立的协议

沿用 §11.9-D 的成功经验（"打开范围 ⇒ 掉出真缺陷"），把这轮的目标定成
**把所有非 `no-untyped-def` / 非纯噪声的错误码清成 0**，然后把棘轮按模块×码
重新收紧。做法：临时注释掉全部 `disable_error_code` → 拿到真实清单 → 逐条分诊
→ 修 → 重算并裁剪豁免。

**结果**：真实残留从 **355 → 296 条**（42 → 31 文件），且只剩两类：
`no-untyped-def` 273（纯签名缺失）与 `arg-type` 23（`float(d.get(k))` 类噪声，
外面都有 try/except 或 `or 0` 兜住）。以下错误码现在全仓 **0**：
`operator` `union-attr` `no-redef` `dict-item` `call-overload` `no-any-return`
`assignment` `import-untyped`。豁免段 42 → 31。

### A. 这批挖出来的真东西：`PlatformAdapter` 协议对 9 个适配器全部不成立

`lsc/platforms/base.py` 里协议写成可变属性：

```python
platform: str
display_name: str
capabilities: Any
```

而 `BasePlatformAdapter`（9 个适配器都继承它）把它们实现成 `@property`。
mypy 的规则是"协议成员声明为可变属性时，只读 property 不算实现"，于是
**9 个内置适配器没有一个能通过 `PlatformAdapter`**：

```
Protocol member PlatformAdapter.capabilities expected settable variable,
got read-only attribute
```

这条不是"一个报错"，而是**整条契约失效**：任何标注 `PlatformAdapter` 的参数
都拿不到静态检查保护，而且**新写一个漏了 `parse()` / `can_handle()` 的适配器
也不会被拦** —— 平台层是产品唯一入口（`FEATURE-INVENTORY §1` 的 P0），
它的接口契约原来是装饰性的。

- **负控已做**：把 `capabilities` 改回可变属性再跑 mypy ⇒
  `registry.py:52: Incompatible types … [assignment]` 立刻复现（该模块豁免表里
  不含 `assignment`）。所以这条守卫是真的，不是写在注释里的愿望。

修法：三个成员改成 `@property` 声明（协议里允许，兼容 property 与普通属性两种实现）。
连带收益：`resolver` 里三处 `adapters: Iterable[object] | None` 是**因为协议不可用
才被放宽成 `object`** 的（`get_adapters` 的形参类型接不住它），现在改回
`Iterable[PlatformAdapter] | None`（配 TYPE_CHECKING 导入，运行时不求值，
已验证不成环），3 条 arg-type 一起消失；
`registry._DEFAULT_ADAPTERS: tuple[PlatformAdapter, ...]` 这行标注从此变成
**编译期守卫** —— 谁把 property 改回可变属性，CI 立刻红。这比写一条
"检查协议"的用例更直接，因为用的就是真实标注。

### B. 其余逐条分诊（每条都决定过"是真缺陷还是噪声"）

| 位置 | 判定 | 处置 |
| :--- | :--- | :--- |
| `orchestrator._finish_v2_lease_refresh`：往 `dict[str, StreamInfo]` 里写 `StreamInfo \| None` | **可能真** —— 目前靠 `success = bool(info is not None …)` 兜住，但这条推导链在函数里跨了 8 行 | 把隐含条件重述进 `if`，让"不写 None"变成可证明的；行为不变 |
| `orchestrator` 合并落盘三个状态位 | 类上没声明，全靠 `getattr(self, …, None)` 兜底读 ⇒ 首次赋值把 `_save_rooms_timer` 钉成 `Timer`，之后 `= None` 就是"把 None 赋给 Timer" | 类级声明可空类型（顺带让属性集合可见） |
| `resolver`：`probe` 既是 `for probe in probe_results.values()` 的循环变量，又被重新赋成 `.get()` 的可空结果 | 命名复用导致的真误读风险（与 §11.9 的 `snap`、`jianying` 同一类） | 改名 `chosen_probe` |
| `jianying_draft`：`usable` 先是房间列表、后是布尔 | 同名两型，今天没炸只因为后面再没读过列表 | 改名 `clip_usable` |
| `probe._run_cancellable` 返回 `SimpleNamespace` 冒充 `CompletedProcess` | 与 `subprocess.run` 分支共用一个变量，两种形状混用 | 返回真实的 `CompletedProcess` |
| `ingest_supervisor.health()` 声明 `dict[str, object]` | 12 处 `int(health.get(k, 0) or 0)` 全被判 call-overload；诊断袋本来就没法给类型 | 放宽成 `dict[str, Any]`，并在注释里说明它与 `budget_snapshot()` 同类 |
| `timeline.mapped_ms` 三分支混 int/float | 精度不致命但类型漂移 | 局部声明 `float` |
| `assets._probe_segment` / `persistence` / `cookie_helper`(DPAPI) / `frame_provider` 的 Any 返回 | 边界值，无静态信息可保 | 显式转形或 `cast`，逐处写明为什么 |
| `ocr_detector` 的 `# type: ignore[import-untyped]` 挂在被导入的名字上 | **这条 ignore 一直是无效的**（mypy 报在语句起始行） | 移到 `from … import (` 行 |
| `url_policy`：`list[_NoRedirectHandler]` 接不住 `ProxyHandler` | 我上一轮把它改成 `list[object]`，只是把错误从 `append` 挪到 `build_opener` | 用公共基类 `list[BaseHandler]` —— 这才是 `build_opener` 的真实契约 |
| 17 处 `context = dict(network_context or {})` | 对外契约 `Mapping[str, object]` 是**刻意的**（redaction-safe，跨 resolver/probe/lease 一致），不该为了消错去放宽参数 | 只把**本地拷贝**标注成 `dict[str, Any]`，参数类型不动 |

### C. 我自己在这批里犯的一次错，被门禁拦下

给 `weibo.py` / `candidate_health.py` 补 `from typing import Any` 时，我用了
"在最后一个顶层 import 之后插入"的启发式 —— `weibo.py` 那个 import 是跨行的
`from .base import (`，于是新行插进了括号**内部**，直接语法错误，
42 个测试文件收集失败。`mypy [syntax]` + `pytest` 当场拦下。改成"先删后按锚点插"
并复跑全量才继续。**记在这里是因为：批量脚本改 AST 相邻的东西时，
"每步之后跑一次全量"不是仪式感，是唯一能挡住这类自伤的手段**（与 §11.9-D
那次把海象写进三元表达式被 ruff F821 拦下是同一类）。

### D. 为什么停在这里，而不是把 296 条清零

- **273 条 `no-untyped-def`**：139 条在 `room_handler.py`，绝大多数是
  `@server.on(...)` 处理器。给它们补签名是纯机械 diff（`data: Any, -> dict[str, Any]`），
  一次 100+ 文件的改动会把 blame 冲干净，而**类型上并不能保证正确性**
  （WS 边界本来就是未类型化的 JSON）。要做得有价值，得连带把 handler 的入参
  形状定成 TypedDict/dataclass —— 那是一个独立的立项，不属于"债务收口"。
- **23 条 `arg-type`**：分诊下来全是同一形状（异构字典取标量 + try/except 兜底）。
  正确的收法是抽一个跨层共用的 `safe_float/safe_int`
  （`room_utils.safe_float` 已有同样语义，属重复实现），23 处一次改完；
  到处写 `cast` 则是把噪声藏起来，不做。
- 这两件事**都仍在豁免表的监视下**：新增的 `no-untyped-def` 不在豁免码表里的文件
  会直接红；已有文件出现别的码也会红。所以"没清零"不会变成"继续变烂"。

## 11.13 第六批：文子守卫 → 可执行用例（先建替身，再下线）

§5 的结论是这批的靶子：`tests/test_frontend_stability_guards.py` 141 个用例里
**137 条只做源码子串匹配、0 条断言行为**。这类守卫的失效方式不是"漏报"而是
**双向失真**——重命名变量就红、逻辑写反却绿，而且它让"这块有测试"变成假话，
于是没人再去补真测试（§3 的"重构税"就是它收的）。

做法定成一条硬顺序，避免净损失覆盖面：**先写可执行用例 → 负控证明它有牙 →
才删对应文子守卫**。

### A. 替身：`lsc-electron/src/services/mediaSourcePlayer.test.ts` 9 → 26 条

新增 10 条直接跑真实 `MsePlayer`（假 `video` 元素 + `play()` 永不 settle 的
"hanging" 假件，这才是 media clock 冻结的真实形状），覆盖 goLive 落点、空缓冲、
idle 早退、`getBufferedRanges` 退化区间、`_playExhausted` 置位/清除、停滞强扫
与预算清零。另 7 条把 §7.2.2 的"sink 重建阶梯最后一层"从文子断言变成真调用。

### B. 负控（临时脚本逐条改坏源码 → 跑 vitest + 全量守卫 → 自动还原并校验字节一致；
脚本按 §4 仓库卫生口径用完即删，锚点与结果全在下方表里，可照表复现）

逐个把**真实行为**改坏，看谁变红：

| 变异 | 可执行用例 | 文子守卫（136 条全量） |
|---|---|---|
| goLive live 沿 `bufEnd-0.3` → `bufEnd-2.0` | RED 抓到 | RED（靠子串） |
| 停滞检测去掉 `\|\| this._playExhausted` | RED 抓到 | GREEN **盲** |
| 播放头前进后不清零强扫预算 | RED 抓到 | RED（靠子串） |
| `getBufferedRanges` 不再过滤 `end===start` | RED 抓到 | GREEN **盲** |
| goLive 空缓冲也写 `currentTime` | RED 抓到 | GREEN **盲** |
| `stop()` 不再发 backpressure resume | RED 抓到 | GREEN **盲** |

**6/6 变异被可执行用例抓到**；同一次变异里文子守卫有 **4 条完全无感**。
那张 RED 也不是功劳：它只是因为断言了字面量，改个写法照样盲。

第一版负控**抓到过自己的假用例**：去掉 `_playExhausted` 或条件后原用例仍绿，
因为我把播放头放在 `119.9`，同时满足了 `ct > bufEnd - 0.3` 这个出界条件——
即"两条路径都能触发强扫"时，测试几何没把被测分支单独隔离出来。补了一条
**几何上只有 `_playExhausted` 能触发**的用例（缓冲 `[100,120]`、播放头 `110`
在区间内、`currentRange` 存在），并配同几何的 `_playExhausted=false` 反证
（不许强扫）。这才是表里第 2 行变红的原因。

### C. 下线：5 条被替身完全覆盖的文子守卫

`test_mse_player_go_live_always_seeks_to_buffer_end`、
`test_mse_player_go_live_empty_buffer_waits_for_next_segment`、
`test_mse_player_stall_recovery_force_seeks_when_play_exhausted`、
`test_mse_player_marks_play_exhausted_after_retries`、
`test_mse_player_exposes_buffered_range`（141 → 136，原地留一行指针注释）。
`test_media_source_player_source_open_and_cleanup_are_single_lifecycle_paths`
**保留**：它还断言 `sourceopen` 只注册一处，替身没覆盖这一条——不顺手多删。

### D. 剩余口径与为什么不在本批继续

136 条文子守卫按目标文件分布：`Workbench/index.tsx` 42、`VideoPreview.tsx` 10、
`useWebSocket.ts` 4、`previewAudioAligner.ts` 4、`AnalysisProgress.tsx` 4、
`ClipList.tsx` 4…… 每条约等于"读懂那段组件逻辑 + 造一个可运行的假件"，
不是批量替换能推的。**但顺序不能反**：`Workbench/index.tsx` 的 5259 行拆分
（§12/P2-3）一旦动手，42 条按源码文本写的守卫会集体变红——那时为了绿灯去
改守卫，就等于让守卫给重构让路，正是 §5 担心的事。所以 P2-3 的前置条件是
这条迁移线，而不是反过来。本轮把方法、假件与负控脚本立起来，就是为了让
这条前置条件可执行、可验收。

### E. 这一批里我自己的两处失误，以及顺手抓到的一条真门禁缺口

- **失误：新用例的 tsc 错误**。加完 MSE 用例后我只跑了 vitest（288 全绿）就准备收工；
  收尾重跑五道门禁时 `tsc --noEmit -p tsconfig.json` 报了 2 条 **TS2740/TS2322**
  （`vi.fn(() => new Promise<never>(...))` 赋给 `play`、`async () => { throw }` 推导成
  `Promise<never>`）。vitest 用 esbuild **只剥类型不校验**，所以"测试全绿"对这类错误
  完全无感。修完两行后两个工程都干净。**教训写死在流程里：改完 `*.test.ts` 必须跑
  `tsc`，不能只看 vitest。**
- **缺口：Electron 主进程从不做类型检查**。顺着上一条查 CI 时发现，
  `electron-typecheck` job 和 `npm run build` 的第一步都是裸 `npx tsc --noEmit`
  ⇒ 解析到 `tsconfig.json`（`include: ["src"]`），而主进程工程
  `tsconfig.electron.json`（`include: ["electron/**/*.ts"]`）**CI 不跑、打包前也不跑**；
  `vite build` 用 esbuild 转译主进程，同样不做类型检查。也就是说窗口/托盘/
  后端子进程/本次新加的单实例锁这一整条链，静态防线为 0，而 CLAUDE.md §13.2
  一直宣称"两个工程都要过"（§2 那张"宣称 vs 实测"表又多一行）。
  当日实测该工程 **0 错误**，所以直接在 CI 补 `npx tsc --noEmit --project
  tsconfig.electron.json` 一步即可关闭，不引入存量债；job 名同时改为
  `Typecheck (renderer + Electron main)`，避免名字继续误导。

## 11.14 第七批：一键对齐的落点逻辑——一个"纸面功能"和一条被证伪的猜测

顺 §11.13 的迁移线走到 `previewAudioAligner.ts`，结果这一簇挖出的不是守卫问题，
而是**一个从来没能生效、却被三份文档写成机制的功能**。

### A. 事实链（每条都有可复现出处）

1. `applyOffsetWithDriftCorrection` + `cancelDriftCorrection` + 模块级 `WeakMap`
   共 55 行，**全仓零调用点**：
   `grep -rn "DriftCorrection" lsc-electron/src lsc-electron/electron` 只命中定义所在文件。
2. `git log -S applyOffsetWithDriftCorrection --all` 只有两个 commit
   （`6da1013e` v2.0.0、`5597fa0a`），两处都是 `+` 定义行 —— **历史上也从未被调用过**，
   不是"后来把调用删了"。
3. 三份文档按"前端就是这么落的"描述它：`docs/PROJECT_DESIGN.md §11.4`、
   `docs/SUBSYSTEM_ANALYSIS.md` 数据流一句"前端据此 applyOffsetWithDriftCorrection"、
   `docs/reports/MODULE_AUDIT_REPORT.md` ⑤ 甚至把它列为**亮点**。
   ⇒ 与 §11.12-A 的 `PlatformAdapter` 同一类：**纸面存在、运行时不成立**。
4. 真实落点一直是 `Workbench/index.tsx` 里内联的 8 行 seek —— 那段代码
   **在全仓任何可执行用例都跑不到**（组件回调，无渲染测试），只被一条文子守卫
   以 `split("if (offset < 0.05)")` 当作切分锚点。

### B. 一条我自己提出来、又自己证伪的猜测（记录以免它变成"报告里的假缺陷"）

看到 `if (offset < 0.05) { alignedCount++; return }` 的第一反应是：**负偏移会被当成
"已对齐"整间跳过**（缺 `Math.abs`），而 `lsc/editor/audio_aligner.py` 的
`offset_sec = lag / sample_rate` 里 lag 可正可负 ⇒ 像是 P1 级漏对齐。

顺着数据源核到底：`align_audio_map` 的两条出口
（`audio_aligner.py:461`、`:510`）都做 `max(0.0, raw - slowest)`，把"最慢房间"归零，
**返回给前端的 offsets 恒 ≥ 0**，`alignment_handlers` 原样透传。
⇒ 该漏对齐**当前不会发生**，这条猜测作废。

留下的真实问题是弱一档、但同样要收口的两点：
- 前端的 `offset < 0.05` 依赖一条**跨语言且无人记录的不变量**（后端保证 offsets ≥ 0）。
  后端哪天放开有符号偏移，前端就静默丢掉前向修正，且没有任何测试会红。
- 落点逻辑内联、不可测；而它做的事（移动用户正在看的播放头）出错直接影响观感。

### C. 收口方式：抽成唯一入口 + 可执行用例 + 负控，不盲改行为

- 新增 `lsc-electron/src/utils/previewAlignment.ts`：`applyAlignmentSeek(video, offset)`
  返回 `'seeked' | 'negligible' | 'no-buffer' | 'seek-failed'`。
  判据顺序刻意定为"**先判要不要动，再判有没有数据**"：偏移本身可忽略时房间就是对齐的，
  不该因为播放器没挂上而报"未对齐"。落点 `currentTime - offset` **按符号求差**（不取绝对值）
  并夹在 `[首段起点, 末段末端]` 内 —— 在后端不变量下与原实现逐位一致，
  不变量哪天放开也不会静默丢修正。
- `Workbench/index.tsx` 的内联 8 行改为一次调用；`alignedCount` 现在只计
  `seeked | negligible`。**唯一的行为改动**：setter 抛异常时不再计入"已对齐"
  （原实现 `try{}catch{}` 之后照样 `alignedCount++`）—— 报出来的数量必须真的动过播放头。
- 新增 `previewAlignment.test.ts` 8 条；**负控 6/6 全部抓到**：去掉 `Math.abs`、
  去掉 bufStart 下限、去掉 bufEnd 上限、不 `play()`、吞掉 setter 异常仍报 `seeked`、
  把 no-buffer 判在 negligible 之前。
- 文子守卫 `test_workbench_alignment_response_does_not_count_low_confidence_zero_offsets`
  的切分锚点从 `if (offset < 0.05)` 改指调用点（守卫的语义主张未变）。
  这是"迁移期改锚点"，不是"改守卫迁就重构"：先有可执行用例替身，再动锚点。

### D. 那个"亮点"功能怎么处理

**没有**顺手把残余差修正接上：`playbackRate ±5%` 平滑追赶会作用在用户正在看的 9 路预览上，
与停滞自愈（`_playExhausted` 强扫）相互作用，**在有多房间真机流之前无法验证**，
判断依据同 §13（剪映边界需真机标定，不做盲改）。处理方式是把纸面承诺改如实：
55 行死代码删除、原地留 4 行说明、`PROJECT_DESIGN §11.4` 改写为"当前未实现"并写明
影响面（**导出不受影响**，导出补偿走后端 `content_offset` 数值，不经过播放头）、
`MODULE_AUDIT_REPORT.md` ⑤ 标注本节作废。真要做时的落点也写清了：接在
`applyAlignmentSeek` 之后追加一层。

## 11.15 第八批：**回看读盘在播放头停住时无界增长（实测 ~8MiB/s）**

这一条是本报告全部结论里唯一"初版把严重性写小了"的，值得单独记。

### A. 起因：想把 §7.3 第二条改成"已收口"

§7.3 的原文是：

> 回看队列内存上界只在**出画前**成立 | `_trimQueueBeforeTarget` 首行 `if (this._sawMedia) return`；
> 测试断言的是出画后**单点采样** `< 2MiB`，不是峰值

我想给"出画后的峰值"补一条真用例（顺手把那条结论关掉）。假件形状很直接：
`createFakePlayer` 的播放头只在**首个分段落位**、之后由测试显式推进 —— 这正是
"用户暂停"和 §7.1"时钟冻结"的精确复刻。

### B. 实测结论：**不是"没收紧"，是根本没有上界**

| 阶段 | 读游标 | 队列峰值 |
| :--- | :--- | :--- |
| 修之前（删掉 `_pump` 顶部判据跑同一用例） | 每个 poll 前进一块 | 400ms 内 262,292 → **584,060 且仍在涨** |
| 修之后 | **完全不动** | 262,292（=出画前那一个探测块），与文件长度无关 |

生产口径换算（两个常量都在 `localFileMseSource.ts` 里）：
`DEFAULT_CHUNK_BYTES = 4 MiB`、`DEFAULT_POLL_MS = 500`，出画后每轮读 `1×` 块 ⇒

> **播放头一停，读盘就以 ~8 MiB/s = 480 MiB/min 往渲染进程 JS 堆里堆。**
> 用户暂停回看 30 秒 ≈ 240 MB；4 分钟 ≈ 2 GB ⇒ 崩溃。

机制（`_pump` 的 `for(;;)` 在**一次** pump 里通常只跑一轮就 `return`，靠
`_schedule()` 每 500ms 重入）：

- 原有的"该停了"判据 `if (queue.length > 0 && _isAheadOfPlayback()) return`
  写在**读完之后**，所以它只能结束本轮，挡不住下一轮再读一块；
- 前瞻门 `_drainQueue` 只是"不喂"，**不会让读盘停下**；
- `_trimQueueBeforeTarget` 首行 `if (this._sawMedia) return` —— 出画后修剪直接关闭。

三条各自都"有道理"，合起来就是**无界**。这也是为什么 §7.3 当初把它写成
"上界只在出画前成立"仍然偏轻：出画前的路径有 `_canStartFeeding` 的切片 +
修剪兜底，反而是**出画后**才是裸的。

### C. 修复（3 行，放在读盘之前而不是之后）

```ts
if (this._sawMedia) {
  this._drainQueue()                       // 先喂：播放头推进时必须在这里重新开窗
  if (this._queue.length > 0 && this._isAheadOfPlayback()) return
}
```

"先 drain 再判"不是风格问题：我第一版把判据直接放在循环顶部（不 drain），
用例的 C 阶段立刻证明它**会把回看钉死** —— 队列里剩的段永不喂出、读盘也永不恢复，
`bytesRead` 在播放头推进后仍然纹丝不动。**是这条新用例把我自己的错抓出来的。**

用例三段式：A 播放头冻住 ⇒ 读游标平 + 峰值 ≪ 文件长度；B 再采样一轮 ⇒ 仍平；
C 播放头追到队列之外 ⇒ 读游标必须重新前进。负控：删掉那 3 行 ⇒ A 的峰值断言变红
（584,060 > 524,288）。

### D. 与 §7.1（预览时钟冻结根因未定论）的关系

这条**不能宣称是根因**，但它给了 §7.1 一个此前没被记账的放大器，而且是**正反馈**：

> media clock 冻结 → 播放头不动 → 前瞻窗口不再打开 → 读盘每 500ms 堆 4 MiB →
> 渲染进程内存压力/GC → 解码与调度更慢 → 冻结更深。

现场那条"滞后从 -113s 涨到 -158s/3min"的曲线，形态与"积压持续增长"一致。
**待验证**（不要当结论用）：下次再冻结时看 `debug.log` 里渲染进程的内存增长速率；
如果每个 500ms 台阶 ≈4MB，就是这条路径（现已阻断）。
所以 §7.1 的处置从"根因未知"改成"**已知一个必然恶化作答已修，根因仍开**"。

## 11.16 第八批续：两条"小但真"的收口 + 一次对自己旧结论的复核

### A. 断连发件箱溢出：从"静默丢最旧"改成"拒绝最新并返回 false"

`websocket.ts` 的 `send()` 在断连期把命令压进 100 条队列，**满了就 `shift()` 丢掉最旧的一条，
却仍然对当次调用返回 `true`**。§7.3 初版把这条记为"真实但低危的漏口"，本轮复核后认为值得修，
理由不是概率而是**契约方向错了**：

- 被丢的那条更早的消息，调用方**已经拿到过 `true`**（`useWebSocket` 据此不弹警告），
  这正是 §5 定义的"静默失效"本体；
- 丢最旧还会破坏命令顺序 —— 例如 `add_room` 被挤掉而后继的 `set_settings` 留下。

改法（`websocket.ts` 的 `send()`）：队列满 ⇒ `console.warn` + `return false`，已排队的保持 FIFO。
调用方原有的 `DISCONNECTED_SEND_WARNING` 分支因此自然生效，不需要新协议。

用例：`websocket.test.ts`「断连期间发件箱已满：拒绝最新一条并返回 false，不得挤掉更早那条」
—— 把上限压到 2，断连期发 3 条，断言第三条返回 false，且 auth 之后 flush 的顺序是
`auth, get_rooms{n:1}, get_settings`。负控：把实现改回 `shift()` ⇒ 用例变红
（`expected true to be false`）。

### B. `recorder/segmented.py`：两套分段录制，其中一套是死的

§7.3 的"两套分段录制并存 / 分叉风险"经核不是"两套都在跑"：

- 生产沿是 `shared_ingest.build_segmented_recording_command`（:879）+ `_recording_segmented`（:404）
  + 收尾 `_finalize_segmented_manifest`；`orchestrator.py:2845/2863` 的 `segmented=True` 全走这条；
- `lsc/recorder/segmented.py` 的 `SegmentedRecorder` **没有任何生产 import**
  （`grep -rn "SegmentedRecorder" lsc python-backend` 只命中模块自身、`recorder/__init__.py`
  的再导出、以及它自己的测试），而 `CLAUDE.md §5` 里它本来就被标为"➖ 非生产路径"。

处置与前几批同类项一致（`mse_sender` / `export_service` / `frame_capture` / `sound_detector`）：
删除模块 + `tests/test_segmented_recorder.py`（3 条只测死代码的用例不复活），
`recorder/__init__.py` 去掉再导出。mypy 覆盖 105 → 104 文件、pytest 2129 → 2126，五道门禁全绿。
**连带收益**：§11.1 / §11.4 那张"派生闸待接线"清单少一项 —— 给一个不可能被走到的 spawn 点
接预算闸，本来就是假工作量。

### C. 顺手复核了自己前几批的"已收口"（一处不成立已在 §11.15 改正，其余成立）

逐条回到代码核，不靠记忆：

| 旧结论 | 复核结果 |
| :--- | :--- |
| 一次性分析 sidecar 孤儿化已修 | ✅ `analysis_handlers.py:552/710` 落盘前重取 `room.record_*_path`，注释写明原因 |
| `is_analysis_stale` mtime 误判已修 | ✅ 已迁到 `persistence.py:372`，`tests/test_analysis_freshness.py` 4 条 |
| `flush_pending_room_saves` 无人调用 | ✅ 现在 `main.py:448` 退出路径以 `fsync=True` 调用 |
| `save_rooms` 浅校验 | ✅ 校验在 `room_handler` 的 WS 入口，与 `add_room` 共用 `_room_url_shape`（`tests/test_save_rooms_validation.py`） |
| worker 嵌套 `wait_for(shield(...))` | ✅ `grep "wait_for(asyncio.shield"` 全仓 0 命中 |
| `dependency_manager` 用 assert 兜 stdout | ✅ `grep "assert .*stdout"` 0 命中 |
| 回看队列出画后有上界 | ❌ **严重性写小了**：根本没有上界，已修（§11.15） |

这张表存在的意义：**报告里每一条"已收口"都必须能被一条 grep 或一个测试复现**，
否则它就只是上一次会话的一句自我安慰。

## 11.17 第九批：广播通道的"丢消息检测器"自己不可信（§7.4 仍开项收口）

§7.4 的"仍开 18"里，`broadcast_hub` 那条当初记的是"loop 关闭时广播静默丢
（`broadcast_hub.py:65-67`）"。核到底之后，问题比这条描述更结构性：
**后端唯一能证明"前端有没有漏收广播"的仪器（`_seq`）本身是坏的。**

### A. 三处叠加，使仪器失效

| 缺陷 | 后果 |
| :--- | :--- |
| `self._seq += 1` 与 `put_nowait` 之间无同步（生产者是编排线程 + 一批 executor 线程） | 队列顺序可与序列号顺序不一致。前端 `useWebSocket.ts:408-413` 只在 `_seq > last + 1` 报警 ⇒ 倒序到达既不报警，还把 `last` 拉低，**掩盖真缺口** |
| 可丢弃遥测（`system_stats`/`rooms_updated`/`mse_segment`）也占号 | 队列满时**设计内的丢弃**变成假缺口告警，真告警淹没在噪音里 |
| `notify_broadcast` 的 `except RuntimeError: _log.debug(...)` | debug 在生产被过滤 ⇒ "消息进了队列但没人会被叫醒去取"完全不可见 |

### B. 收口方式（两条不变式，写进 `CLAUDE.md §2` 的广播条目）

1. **`_seq` 只发给关键消息**（`type ∉ _DROPPABLE_TYPES`）；
2. **取号 + 入队在同一次 `_seq_lock` 持锁里完成**，入队失败不提交号段 ⇒
   队列内顺序 == 序列号顺序，前端的 `> last + 1` 判据从"可能假阳性 + 可能漏报"
   变成"缺号 == 真丢了关键消息"。

驱逐路径（`_enqueue_preserving_terminal`）与扩容路径（`_expand_broadcast_queue`）
整段持锁 —— 后者尤其重要：原先换队列期间别的线程还能往**旧**队列塞，
那条消息随旧队列一起蒸发，且它已经拿过号，前端看到一个查不到原因的缺口。

饱和慢路径从"`put(block=True, timeout=5)`，超时即整条丢"改成
"扩容 + 有界重试 5s ⇒ 最后做一次**不带号**的阻塞入队并 ERROR 留痕"：
宁可这条不参与缺号判定（无 `_seq` 前端会跳过），也不静默丢关键终态。
唤醒失败计数 + 首次 WARN（复用 §11.9 的 `lsc/platforms/diagnostics` 注册表），
新增 `broadcast_health()`，经 `_system_stats_extra(bridge)` 进 `system_stats.extra.broadcast`
⇒ "前端说没收到"时可分辨 *后端没发 / 队列丢了 / 没唤醒*。

### C. 验证

- 新用例 `tests/test_broadcast_seq.py` 6 条：并发 8 生产者 × 25 条下
  **队列里的序列号必须严格递增且连续**；遥测不占号；满队列丢遥测不产生幻影缺口；
  唤醒失败计数且只 WARN 一次；唤醒路径任何异常不得逃回生产者线程；快照可 JSON 序列化。
  并发用例把 `queue.Queue.put_nowait` 换成"先 `time.sleep(0)` 再入队"来稳定复现
  修前的窗口 —— 不加这一步，GIL 下竞态很少发生，用例等于碰运气（这类"测不到"的
  并发守卫与 §5 的文子守卫同罪）。
- 负控 4/4 抓到：给所有消息发号 / 发号与入队不同锁 / 唤醒失败退回 debug 不计数 /
  去掉兜底 `except`。
- 门禁：ruff 干净、mypy Success（104 文件）、pytest **2132**（+6）。

### D. 一处刻意不做

`_expand_broadcast_queue` 里"新队列放不下就丢最旧"的分支，在持锁后**结构上不可能触发**
（`new_max ≥ old_max`，且持锁期间无并发写入）。留着作防御性兜底并加了计数，
但它没有用例覆盖 —— 写在这里，避免以后有人以为那条路径被测过。

## 11.18 第十批：死副本、一个"永不产出"的模式，以及两个 handler 模块的覆盖率

### A. `room_handler.py` 的搬家残留（P2-3 顺带收口）

导出簇与分析簇此前分两次往外搬，但**原地的副本没删**：模块级函数 `room_handler.py` 内零引用、
全仓也没有 `from …room_handler import X` / `room_handler.X` 指向它们。清扫做法与判据：

- 用 `ast` 取精确行区间（**不用**括号/缩进启发式 —— 第一版脚本用括号计数，遇到多行
  docstring 会把边界算错，直接作废重写）；
- 三重安全判据：本文件内除 `def` 外零 Load 引用 + 其它文件里该名字只能以
  "自身定义"形式出现 + 无 `room_handler.NAME`/`import NAME` 形式引用；
- 结果：**13 个函数 / 123 行** + 3 个死全局（`_export_total`/`_export_completed`/
  `_export_batch_id`/`_export_stats_lock`）删除，`lsc`+`python-backend` 的 mypy 覆盖
  104 → 102 文件（另有两个 dev 脚本移出，见 §E），`pyproject` 里两条
  `run_and_export`/`run_full_export` 的失效豁免段一并删掉。

刻意**没删**的两个，理由写在这里以免被当成遗漏：

| 名字 | 为什么留着 |
| :--- | :--- |
| `_refresh_audit_queue_state` | 生产无调用点，但 `tests/test_broadcast_draft_failclosed.py` **直接 import 并断言它的行为** ⇒ 删它等于删一条有效断言，需要单独判断该行为是否还该守 |
| `format_manual_clip_label` | 是"与前端 `clipNaming.ts` 保持同步"的 parity helper，被文子守卫 `test_python_handler_clip_naming_helpers` 要求存在；后端侧目前无调用点 |

### B. 导出批状态：两份副本，活的那份少了赋值

`_notify_export_overall` / `_reset_export_batch` / `_export_job_states` 在
`room_handler.py` 与 `export_handlers.py` **各有一份**。真正被调用的是 `export_handlers`
那份，而它**丢了 `_export_batch_id = batch_id`** —— 于是广播里的 `batch_id` 永远是初值
空串。新写的行为用例第一次跑到这段就把它打红（`expected 'batch-7' == ''`），修完转绿。

顺带查实：`_reset_export_batch()` 本身**没有任何调用方** ⇒ `_export_total` 恒为 0 ⇒
`export_overall_progress` 这条协议消息实际上从来没发出过，而 `docs/spec-websocket-protocol.md`
把它列为正常消息、前端也没有任何监听者。处置：修 helper 自身的缺陷 + 把"当前不可达"写进
协议表（与 §11.14 的"纸面功能"同一手法：不留假承诺）。真要做总体进度需要先定
"批量导出"协议（一次提交带 batch_id/total），那是产品决定，不在债务收口范围。

### C. §9-P0.5 关闭：两个 handler 模块从"只有文本守卫"变成有真断言

审计 §2.3 记的 `alignment_handlers.py 15%` / `export_handlers.py 16%`，实测口径
（`--cov=python-backend`）：

| 模块 | 修前 | 修后 | 新增断言 |
| :--- | :--- | :--- | :--- |
| `export_handlers.py` | 16% | **33%** | `_resolve_export_range` 全部 5 条分支（exact / approximate / 预览延迟兜底 / 部分快照特例 / AI 直通）、状态表 LRU 淘汰、批计数、预设回退与自定义覆盖、`_parse_fps`/`_safe_float`、并发上限只认 1/2 |
| `alignment_handlers.py` | 16% | **23%** | `_epoch_ms_to_mono`（保住两路 5s 采集差 ⇒ 现场"错位 6~7 秒"的根因修复）、`_clear_room_alignment`、`_settings_output_dir` 三种坏状态兜底 |

新文件 `tests/test_export_alignment_units.py` 29 条。剩余未覆盖的是**需要 WS 会话/编排线程**
才能跑到的 handler 主体 —— 那部分要么补 handler 级 harness（下一批），要么按 §9-P0.5 的
另一个选项在文档里明确"只有文本守卫"。本批选择"先提升能提升的"，不假装红线已达。

### D. P1 新发现：**"通用场景"持续分析永不产出**（已修，用户可见）

证据链（每条都可在代码里复现）：

1. 前端"通用"档发的是 `mode='scene'`（`Workbench/index.tsx:3466/3504`），不是 `'generic'`；
2. 后端粗扫 `_do_scan` 的分支条件是 `if game == 'valorant' and _mode == 'valorant_round'`
   （`room_handler.py:7588`）—— 不满足就落到下一句
   `return _detect_rounds_by_audio_rhythm(...)`；
3. 那个"兼容包装"的函数体是 `_log.warning(...); return []`（注释自己写明"已随纯 OCR 简化移除"）；
4. `GenericAnalyzerPlugin.capabilities().realtime_continuous = False`，且其 `scan_window()`
   也直接 `return []`，注释"Continuous scene still uses handler audio-rhythm path until
   explicitly migrated" —— 而那条路径正是恒返回 `[]` 的包装。

⇒ 选"通用"启动持续分析后：**循环照常跑、进度照常报、每轮一条 WARN、结果恒 0 条**。
更糟的是它返回 `scan_succeeded=True`，于是未覆盖窗口被记成"扫过但没有回合"。

修法（两处，最小且互锁）：

- **启动侧能力闸**（`analysis_handlers.handle_start_continuous_analysis`）：
  非 `valorant + valorant_round` 组合、或插件自报 `realtime_continuous=False` ⇒
  返回 `{'success': False, 'error': '当前只有「无畏契约 · 回合」模式支持直播中持续分析…'}`；
- **粗扫兜底**改为 `return {'rounds': [], 'scan_succeeded': False}` + 首次 ERROR
  （走 `note_degradation` 计数），并**删掉** `_detect_rounds_by_audio_rhythm` 包装 ——
  窗口保持未覆盖，绝不再伪装成"扫过了"。
- 用例：`tests/test_continuous_analysis_capability_gate.py` 4 条（scene 被拒、
  valorant+scene 也被拒、valorant_round 不被误伤到下一道校验、能力声明本身）。

**这是本批唯一改变用户可见行为的地方**：原先能"启动"的通用持续分析现在会立刻收到一条明确错误。
产品侧若要彻底干净，应在 UI 里对持续分析入口禁用「通用」档 —— 那需要用户确认，已记为遗留。

### E. 两个一次性脚本会随安装包发布

`python-backend/run_and_export.py`(130 语句) 与 `run_full_export.py`(117 语句) 是硬编码
"test video" 的开发实验，**0% 覆盖**且无人引用，却位于 `extraResources: from ../python-backend
→ to python-backend, filter **/*` 的打包范围内 ⇒ 每个用户的安装包里都带着它们。
已移到 `scripts/` 并加进既有的脚本排除清单（`recover_and_backfill_broadcast.py` 同款），
运行时行为零变化。

### F. 计数口径更新

mypy 关掉豁免后的实测残留由 **296 条 / 31 文件** 降到 **282 条 / 30 文件**
（`no-untyped-def` 273→259、`arg-type` 23 不变；随两个 dev 脚本移出 `python-backend` 减少）。
非机械错误码仍为 0。测量口径提醒：用临时 config 剥掉 override 段时会连第三方
`ignore_missing_imports` 一起剥掉，因而会多出 5 条 `import-untyped` 假象 —— 记账时按
`no-untyped-def` + `arg-type` 两类算。

## 11.18-G 补：§7.4 的"节流双实现"经核是误判，外加一次我自己的越界删除

**误判更正**：报告把 `_RoomsThrottle`（`room_handler.py:4422`）与 `server.py` 的
`drain_merge_broadcasts` 记成"两套并存的合并实现，分叉风险"。核到底它们是**不同层次**：

| 机制 | 位置 | 省掉的是什么 |
| :--- | :--- | :--- |
| `_RoomsThrottle`（300ms 窗口 + `_flush` 补发） | 生产侧（要不要再生成一条 `rooms_updated`） | 重复构造 `_rooms_list(manager)` 与队列压力 |
| `drain_merge_broadcasts`（同 key last-value） | 发送侧（队列里已有的重复塌成最后一条） | 同一 tick 内的多次 WS 发送 |

`_broadcast_rooms(force=True)` 也不是"取消待发任务 = 丢更新"：它清 pending、取消 `_flush`
之后**立刻广播当前房间表**（注释里的 #103 就是为这个），语义上是超集。
⇒ 这一条从"仍开"改为"误判"，并按 §11.5/§11.9-E 的先例记在这里。

**顺带把这层的契约钉成断言**：该节流此前**零可执行覆盖**（只有 `test_ws_scheduling.py`
里一条走完整注册链路的用例）。新增 `tests/test_rooms_throttle.py` 4 条：
首条立即发 / 窗口内挂 pending 不各发 / 超窗口恢复立即发 / 合并是布尔不是计数。
测试用 `monkeypatch.setattr(rh, 'time', SimpleNamespace(monotonic=...))` **只替换模块内的
`time` 引用**，不动全局 stdlib —— 直接 patch `time.monotonic` 会污染整个测试进程。

**我自己的一次越界删除（被全量用例当场拦下）**：清扫死代码时我把
`_RoomsThrottle.mark_pending` 也当死方法删了 —— 而我的死代码判据只覆盖
"**模块级**函数"（本文件零引用 + 全仓无按名导入），方法级不在范围内。
`tests/test_ws_scheduling.py:151` 直接调用它 ⇒ 全量 pytest 立刻红。已恢复该方法并补上
注释说明用途。**教训写死**：方法/属性级的删除需要单独的判据（含 `getattr(obj, name)` 与
测试直调），不能沿用模块级扫描的结论；以及本条之所以能被发现，只是因为
"改完整整跑一遍全量"没有打折（同 §11.12-C、§11.9-D 的教训）。

## 11.19 第十一批：给 `align_preview_audio` 建路由级 harness（§9-P0.5 的另一半）

§11.18-C 说过：handler 侧剩下没覆盖的是"需要 WS 会话才能跑到的入口主体"。这一批就把
`alignment_handlers.handle_align_preview_audio` 的 harness 立起来 —— 它是 P0 入口
（写 `content_offset` / `align_group_id` / 建公共时间轴），此前**输入校验与失败分支
一条可执行用例都没有**。

### A. 覆盖构成变化

`alignment_handlers.py` 覆盖率 **23% → 70%**（12 条路由用例，0.74s）：
房间数 <2 / >64 硬拒、房间重复、采样率越界（4k/96k）、两路采样率不一致（必须硬拒而不是
静默跳过）、>15s 上限、坏 base64/字节数不是 float32 整数倍 ⇒ 跳过该路但整体报
"有效音频不足 2 路"、计算超时（`_ALIGN_COMPUTE_TIMEOUT_SEC`）、
可信不足 ⇒ 清 offset/group 并按 `precision='buffer_only'` 报失败、
以及 **happy path 端到端**：注入 0.5s 真实时移 ⇒ 返回 offsets 差值 ≈0.5s、
两房 `align_group_id` 一致、`content_offset` 与返回值一致、`timeline_ready` 已入广播队列。

### B. 建 harness 时的两个真收获（都不是代码问题，但值得记）

1. **夹具本身会让"高精度错解"看起来正常**：第一版用 3Hz 方波脉冲造两路信号，
   `align_audio_map` 返回 `-0.1667s` 而 **score=0.989**。核下来是夹具含糊 ——
   周期信号的互相关在 1/3s 整数倍处同高，估计器锁到 0.5s 的混叠解。
   换成"随机位置宽脉冲 + 低电平噪声"后峰唯一，实测差值精确回到 0.5s。
   这条记下来是因为它正是 §5 反对"文子断言"的同一件事：**测试跑绿不等于测到了东西**；
   顺带提醒 —— 直播音频里如果存在强周期成分（BGM 节拍），互相关也可能锁到混叠解，
   这是 §7.3 剪映边界那条同族的、**需要真实标定数据**才能评估的问题（不当 bug 报）。
2. **落盘目录必须钉住**：happy path 会 `bind_rooms_to_bundle(rooms, _settings_output_dir())`，
   真实现直接在 `~/LSC/output` 建目录。harness 用 `monkeypatch` 把它指到 `tmp_path` ——
   这类"测试写用户真实目录"的坑与 §11.9-B（settings 簇搬家不安全）同源。

### C. 顺带修的一处真缺陷（在我自己新写的 harness 里暴露）

`_FakeBridge` 少实现 `queue_broadcast` 时，处理器的失败分支是
`except Exception: _log.warning("写入房间表/广播 TimelineContext 失败")` —— 也就是说
**广播通道抛错会被降级成一条 warning，对齐结果照样返回 success**。这个吞异常本身是
`# noqa` 之外的窄口径合理设计（对齐已成功，不该因广播失败而失败），但它提醒：
这条路径上 `broadcast` 失败 = 前端不会收到 `timeline_ready`，而响应里带了 timeline ——
前端两条路都认，所以不构成缺陷；已在 §11.19-A 的用例里断言"确实入了队列"。

## 11.20 第十二批：`previewAudioAligner` 收尾链的数值行为迁成可执行用例

§11.13 的迁移线走到第 4 簇（预览音频采集的收尾链）。这一簇的特点是**全是数值**：
静音阈值、峰值归一化目标、降采样窗口、base64 分块 —— 正是"文子守卫最没用、
而行为用例最便宜"的那类代码。

- 新增 `lsc-electron/src/utils/previewAudioAligner.test.ts` 9 条：阈值两侧各一条
  （`1e-6` 丢、`1e-4` 留）、归一化后峰值落在 `(0.4, 0.7)`、正常电平不被放大、
  直流常量保持常量、**48k 奈奎斯特纹波被 7 抽头窗压到 <0.3**、同采样率恒等、
  base64 与 `atob` 往返、8192 分块边界长度。
- 负控 **5/5**：阈值放宽 1e-5→1e-3、归一化目标 0.5→0.05、把 `peak` 换成 `rms`、
  **去掉低通（窗口退化成单样本）**、分块 off-by-one 丢尾字节。
- 下线 2 条文子断言：`test_preview_audio_aligner_rejects_near_silent_rms` 里
  `"peak < 1e-5 || rms < 1e-5"` 与 `"0.5 / peak"` 两个子串断言删除，
  **保留** `silent_audio` / `captureWithScriptProcessor` / `createScriptProcessor`
  三条结构断言并写清为什么它们还只能是文本（要跑通 Web Audio 采集才能断言）。

**负控又一次抓出"没牙的用例"**：第一版对 `downsample` 只有"直流常量"一条，
把低通窗改成单样本后用例全绿 —— 直流在任何抽样下都不变，测不到防混叠这件事。
补了交替 ±1 的纹波用例后才抓到。这与 §11.13-B（`_playExhausted` 那条）同型：
**同一条代码路径下，"断言通过"和"测到了东西"是两件事**，只有变异测试能区分。

顺带两条测量教训（当天第三次）：断言里的期望值不要用 `Math.ceil((n/3)*4)` 这种
先除后乘的浮点式（少一个字符），base64 长度按 `4*Math.ceil(bytes/3)` 算；
Python 侧 `100.0 + 0.3` 与"跨过 300ms 窗口"的 `>=` 判定会因浮点误差假失败，
夹具里明确超出再断言。

## 11.21 第十三批：导出链路由级 harness（`export_handlers` 33% → 84%）

§11.19 立了 `align_preview_audio` 的路由 harness，这一批把同一套办法用到导出链上 ——
它是全系统里"错了用户立刻看得见"的那条路（切片导出），而此前**只有纯函数被覆盖**：
`queue_export` 的校验、worker 取号持 semaphore、`manager.start_export` 的三种失败形状、
`on_done`/`on_progress` 回调、`clip_completed`/`clip_failed` 广播、任务状态表 ——
全部没有可执行断言。

### A. 结果

- `export_handlers.py` 覆盖率 **33% → 84%**，`alignment_handlers.py` 70%；
  合并口径（CI 的 `--cov=lsc --cov=python-backend`）**62% → 64%**，地板仍是 55。
- 新增 `tests/test_export_route_flow.py` 13 条：入点≥出点 / 房间不存在 / exact 与
  approximate 两条口径真的按快照与预览延迟兜底走 / FFmpeg 失败 ⇒ `clip_failed` 且
  状态 `failed` / `start_export` 返回空 clip_id ⇒ 把 controller 的
  `_last_export_error` 原样带给用户 / controller 缺方法 ⇒ `AttributeError` 被改写成
  "导出接口缺失或不兼容" / 进度回调写过 42 之后终态必须覆盖成 100 /
  取消排队中与执行中两条路径 / 状态查询端点的形状（非列表报错、未登记 id 不得凭空造状态、
  300 个 id 截到 100） / NVENC 不可用时预设回退 `libx264` 且竖屏裁剪仍生效。

### B. 建 harness 时踩到的两件事（都写进了文件头注释）

1. **一个用例只能一个事件循环**：worker 池由 `_ensure_export_queue_impl` 在**当前**循环里
   `create_task`；`asyncio.run` 收尾时会把它们取消。最初把"提交"和"等状态"分在两次
   `asyncio.run`，表现为"任务状态永远不出现" —— 是夹具错。
2. 夹具的 teardown 曾把 `_export_semaphore` 置 `None`，于是下一个用例的 worker 撞
   `async with None` → 被 worker 的兜底 `except Exception` 吞成一条"导出异常：'NoneType'…"。
   这既是夹具 bug，也**顺带证明**了那层兜底的价值：worker 不会因为一个坏状态把整条
   导出通道打死。现在 teardown 恢复成模块导入时的初态。

### C. 为什么这比"再迁移几条文子守卫"更值

同一条链上，文本守卫只能证明"这行代码还在"；上面 4/5/6 三条断言的是
**错误信息会不会原样到达用户**与**进度条会不会卡住** —— 恰好是历史导出事故的形状。

## 11.22 第十四批：mypy 的 `arg-type` 23 → 0，并且把 `lsc/` 侧的豁免**全部清空**

### A. 做了什么

`§11.6` 留下的两类机械债，这一批关掉了一整类、并实质推进了第二类：

| 口径 | 批前 | 批后 | 怎么量的 |
| :--- | ---: | ---: | :--- |
| `arg-type`（关掉全部豁免实测） | 23 | **0** | 见下方"复现测量" |
| `no-untyped-def` | 259 | **245** | 同上；剩下的 245 条**全部在 python-backend** |
| `pyproject.toml` 里的 mypy 豁免段 | 21 处 `disable_error_code` | 15 处，且**`lsc/` 一条不剩** | `grep -c disable_error_code` |
| 门禁 | ruff 0 / mypy Success / pytest 2195 / tsc 0 / vitest 307 | ruff 0 / mypy Success / **pytest 2225** / 未动前端 | 五门禁全跑 |

**复现测量**：mypy 没有"临时停用全部豁免"的开关，做法是把 `pyproject.toml` 的
`[[tool.mypy.overrides]] … disable_error_code` 段落剥掉存成一份临时配置再跑
（`--config-file`），按错误码 `grep -o "\[[a-z-]*\]$" | uniq -c` 统计。
剩下的 `import-untyped 5` 不是债 —— 那是 CI 不装 numpy/onnxruntime stub 的豁免被一并剥掉所致。

### B. 为什么不是写 23 个 `cast`

`cast(float, x)` 只会把"这里可能是 `None`/字符串/NaN"这件事从工具视野里挪走，
而这类字段恰恰是**平台改版最先坏的地方**（§4 的 parser-stale 场景）。
真正的形状是"同一个 3 行 `try/except` 手抄了 20+ 份，且各份判据不一致"，
所以收成一个模块 `lsc/utils/coerce.py`：`try_float` / `safe_float` / `try_int` / `safe_int`。

顺带清掉的是**已经成形的三份副本**：`handlers/room_utils.safe_float`、
`handlers/export_handlers._safe_float`、`handlers/room_handler._safe_float`
（第三份还要经 `register_analysis_handlers(safe_float=…)` 注入给分析簇）。
现在三处都指向同一个实现，`room_handler` 的注入参数保留（夹具仍要塞假件）。

### C. 口径变化（有意为之，且被测试钉住）

新口径比被替换掉的实现**更严**：

1. `NaN` / `Inf` 一律回退。旧实现全部放行 —— `float('nan')` 成功，
   于是 `nan` 会静默进时间轴与打分；`float('inf')` 进 `min/max` 还会悄悄改边界。
2. `bool` 一律回退。`int(True) == 1`，一个误传的开关会变成合法数值 1。
3. `try_int` 不截断小数：`2.7 → None`。`sequence` / `split_index` 这类索引字段
   静默截断会造成错位，宁可让调用方回退。

`tests/test_coerce.py` 30 条断言逐条钉住上述三点，**负控 7/7 全部被抓**
（M1 放行 NaN、M2/M4 放行 bool、M3 截断小数、M5/M6 忽略 default、M7 不再接受字符串）。
另外把 `tests/test_export_alignment_units.py` 里针对旧副本的 3 行断言，
换成路由级用例 `test_route_coerces_string_timestamps_instead_of_raising`：
字符串 start/end 要能正常入队，解析不出来的要回退成 0 后被业务校验拒绝 —— 而不是抛 `ValueError`。

### D. 补标注顺带暴露的 4 个真实类型不符

`lsc/` 侧那 12 个未标注函数补完签名后，mypy 立刻报出 4 处**之前根本看不见**的错误
（这就是"豁免一挂，真错也看不见"的具体样子）：

- `base.py` / `url_policy.py` 的两个 `HTTPRedirectHandler.redirect_request` 覆写：
  第二参数 typeshed 是 `IO[bytes]`（不是响应对象），`headers` 是 `HTTPMessage`（不是 `Message`）；
- `open_http_stream()` 原先返回 `Any` —— 所有调用方对返回对象的属性访问**全部不校验**；
  现在声明 `-> addinfourl`；
- `SharedRoomIngest._lease_manager` 因为初始化写成 `self._lease_manager = None`
  而被推断为 `None` 类型，一旦标注 `bind_lease(manager: object)` 就报"不能把 object 赋给 None"
  —— 这是推断错误，不是运行错误，修法是显式 `_lease_manager: object | None`。

`registry.py` 的 5 个惰性转发壳（`resolve_stream_v2` / `probe_candidates` /
`select_stream_lease` / `select_ingest_lease` / `resolve_playable_lease`）之前是
`(*args, **kwargs)` 无返回类型 ⇒ 对调用方表现为 `Any`，orchestrator 里对
`result.platform`、`probes.items()` 的访问全都不检查。现在返回类型钉回真实类型，
参数仍是 `Any`（壳的诚实形状就是 `Callable[..., X]`）。

### E. 剩下 245 条为什么不做

全是 python-backend 的 WS 处理器签名。刷成 `def handle_x(data: Any) -> dict[str, Any]`
只是把 `Any` 从参数挪到返回值，收益为 0；真正需要的是**一份 payload/响应的 TypedDict 契约**
（§11.6 立项描述）。那属于协议层改动，影响面覆盖前端 `WSPayloadMap`，不在"还债"批次里顺手做。

### F. 一处刻意没碰的东西

`room_handler._parse_fps` 与 `room_utils.parse_fps` 是同一形状的另一对副本，
但它含业务判据（`'原画' → 0.0`），不属于"取标量"口径，且删任一侧都会改变调用方可观察行为
—— 留给 §11.6 的 handler 契约立项一起处理。

## 11.23 第十五批：`AnalysisProgress` 的状态推导链搬进可测层（§18 迁移线的第三个簇）

### A. 为什么先搬这个簇

`derivePrimaryStatus` 是一条 **18 个分支、有优先级** 的判定链：把 `phase` /
`analysis_stage` / `scan_reason` / `round_phase` / `degraded_mode` / `provider`
多套后端状态收敛成"动词 + 一句人话 + 语气 + 引导动作"。这正是文子守卫**结构性无能为力**
的形状 —— 守卫只能说"『收尾待恢复』这句文案还在源码里"，说不了
"同时 `completed` 与 `finalization_state='checkpoint_saved'` 时用户看到哪一个"。
而后者恰恰是会误导用户的类别：把"没扫完"显示成"已完成"。

于是按 §11.13 的迁移顺序做：先把纯逻辑搬进 `src/utils/analysisProgress.ts`
（`derivePrimaryStatus` + `formatDuration`/`formatThroughput`/`formatFps` +
`hasFixedScanRange` + 新拆出的 `confirmedAnalysisPercent`），再写行为用例，最后才回头改守卫。

组件从 701 行降到 561 行；`ExportSummary` 留在组件里，utils 只吃
`AnalysisSummarySlice`（`{pendingConfirm, listed}`）这个结构子集。

### B. 结果

- `src/utils/analysisProgress.test.ts` 由 5 条 → **33 条**：优先级（stalled > 收尾可恢复 >
  检查点待恢复 > 通用 error > completed > …）、每个终态的语气与引导动作、
  运行态细节的拼接顺序与插值参数、三个格式化函数的非有限值边界。
  `t` 用恒等桩（`key|{params}`），所以"参数有没有真的传进去"也是断言的一部分。
- **负控 11/11 全部被抓**（M1/M2/M5 是三条分支顺序互换，M3 `!running` 判据、
  M4 `min(98)` 上限、M6 NaN 挡、M7 引导动作、M8 语气、M9 `stalled` 布尔位、
  M10 收尾秒数、M11 `current.error` 兜底）。
- 三条守卫按判据改写：
  `test_compact_analysis_progress_...` 去掉两条已变成行为用例的文本断言（改名
  `..._hides_empty_export_summary`）；
  `test_continuous_status_preserves_task_snapshot_...` 去掉两条"等待录制"文案断言；
  `test_analysis_progress_labels_listed_not_raw_highlights` 瘦成一条 `可导`。
- 门禁实测：ruff 0 / mypy Success(103 文件) / pytest 2225 / 两个 tsc 工程 0 错误 /
  vitest 29 文件 **335** 用例 / 文子守卫 136 条全绿。

### C. 一处"守卫被注释糊弄"的实证

原守卫里有 `assert "待调" in progress`。经核，组件源码中**已经没有**"待调"这个用户可见文案
（早先改成了「N 个待 OCR 复核」），该断言之所以一直绿，是被
`// 待调：保持向后兼容 guard 断言` 这**一行注释**满足的。
这是 §5 "文子守卫的覆盖率是假的"最干净的一个样本：断言的目标早已消失，守卫仍报告通过。
删掉它，并在守卫 docstring 里写明原因。

### D. 负控脚本本身的一个坑（元教训）

第一版负控脚本用 `\n\n` 作为"分支边界"来挪动分支 —— 但这条判定链里**不少分支之间没有空行**，
切片吃掉了后面几个分支，变异文件语法直接炸；`vitest` 退出码非 0，脚本就把它记成"抓到"。
第二版改成花括号配平切分支后，M1/M2/M5 又"漏过" —— 因为那是**空操作**：
`move_after(通用error, 收尾可恢复)` 把分支挪到了它本来就所在的位置后面。
第三版改用 `move_before` 并加 `assert mutated != ORIG`，11/11 才是真的。

固化成规矩：**负控脚本必须自证变异改变了文本**（`mutated != ORIG`），
并且必须把"语法炸/收集失败"与"断言失败"分开报 —— 前者是无效变异，不是测试有牙。

### E. 顺带记下的一条现状（未改）

`mode === 'valorant_round'` 且后端**没给** `round_phase` 时，状态行不拼相位段（空串）；
显式 `round_phase='unknown'` 才显示"寻找回合"。二者语义相同（都不知道相位），
`ROUND_PHASE_LABEL` 的 `|| ''` 兜底把"缺席"和"未知"分成了两种表现。
这属于用户可见文案变化，且 `derivePrimaryStatus` 与 JSX 第 438 行各有一份同样表达式
（§2 "多份手抄"的形状），改要一起改 —— 记为待产品确认，不夹带在本批里偷偷动。

## 11.24 第十六批：`Workbench` 顶部三个纯函数搬进 `clipExportPolicy`（迁移线第四簇）

### A. 为什么是这三个

`Workbench/index.tsx` 顶部有一批**本来就不依赖 React 闭包**的模块级纯函数，
它们被"困"在一个 5,259 行的文件里 —— 想测就得 import 整个组件（连带 antd 与 20 个 mock），
于是历史上只能用文子守卫凑数。本批搬出三个：

| 函数 | 判据 | 原守卫 |
| :--- | :--- | :--- |
| `isApproximateClip` | 谁算"定位不准"：快照/exact 优先假，AI 高光未被标 approximate 时不算近似，其余看墙钟字段齐不齐 | `test_is_approximate_clip_excludes_ai_highlights`（切函数体文本找 `is_ai_highlight`） |
| `resolveManualRecordingRange` | 墙钟 mark → 录制轴秒数：媒体起点优先、回退 `recording_start_mono`、扣 `content_offset`、夹负、出点不晚于入点即拒 | `test_add_clip_snapshots_wallclock_fields` 里的一条文本断言 |
| `clipSnapshotJobId` | `clip-` + 快照 id 前 8 位 | 无 |

落点是既有的 `src/utils/clipExportPolicy.ts`（同属"切片决策口径"，已有 17 条行为用例与 `clip()` 夹具）。
`Workbench` 5,259 → 5,222 行；`reconcileContinuousListedClips` **没动** —— 它已经在
`Workbench/index.test.tsx` 里有真行为用例，搬它只是为搬而搬。

### B. 结果

- `clipExportPolicy.test.ts` 17 → **29 条**（+4 `isApproximateClip`、+4 `resolveManualRecordingRange`、
  +1 `clipSnapshotJobId` 与其边界）；**负控 8/9 被抓**。
- 守卫 136 → 135：`test_is_approximate_clip_excludes_ai_highlights` 整体下线（原地留指针注释），
  `test_scrub_mark_surfaces_approximate_precision` 去掉一条已被下面分段覆盖的重复断言。
- 门禁：ruff 0 / mypy Success(103) / pytest **2224**（2225 减掉下线的 1 条守卫）/
  两个 tsc 工程 0 错误 / vitest 29 文件 **344** 用例 / 守卫 135 条全绿。

### C. 那条"漏过"的变异是**等价变异**，不是测试没牙

N2 把最后一段的 `c.mark_precision !== 'exact' &&` 换成 `true &&`，用例全绿。
按代码顺序证明它对**任何输入**都不可观察：函数第一行就是
`if (c.clip_snapshot_id || c.mark_precision === 'exact') return false`，
所以永远走不到"`precision === 'exact'` 且需要该子判据"的组合 —— 那半句是死条件。

对照：N8（mark 校验只挡 `undefined/null`）第一次也报"漏过"。这次不是等价：
NaN 会一路算成 `NaN` 区间、被出口 `end > start` 顺手挡掉，所以**看起来**没差异；
但字符串 `'1010'` 在 JS 里会参与减法强转成数字，就真的返回区间了。
补一条字符串断言后 N8 被抓。**教训：报"漏过"时必须先判"等价变异 / 断言不区分 / 变异无效"三种可能，
不能直接归因成"测试有牙"或"测试没牙"。**

### D. 顺带发现（未改）

`isApproximateClip` 里那半句死条件说明：这个判据在历史上被改过至少两次
（早退与末段各写了一遍 exact 判据）。留着无害，但它属于 §2 "多份手抄"的微观样本 ——
下次动这个函数时应连带清掉，而不是再叠一层。

## 11.25 第十七批：导出摘要聚合从 `Workbench` 的 useMemo 搬进 `clipExportPolicy`

### A. 目标

`exportSummary`（状态条上的"待确认 / 已入队 / 导出中 / 完成 / 失败 / 入列"）原本内联在
`Workbench/index.tsx` 第 4,132 行的 `useMemo` 里。守它的那条守卫检查"pending 不计入 queued"的
方式是 **断言一段文本不出现**：

```python
assert "status === 'pending') summary.queued" not in workbench.replace(" ", "")
```

也就是"防某种写法出现"，而不是"证明某种行为成立"。写错一个变量名它就失效，
把聚合改写成任何等价形式它又立刻假红。本批把聚合搬进 `utils/clipExportPolicy.ts`
（`summarizeClipExport`），判据改成正向输入/输出断言。

顺带把 `ExportSummary` 接口从 `AnalysisProgress.tsx` 也搬进同一处，成为唯一定义
（组件改成 `import type { ClipExportSummary as ExportSummary }`，调用点与 prop 名不变）。
这是 §2 "同一形状多份手抄"的又一个微观样本：接口在组件、聚合在页面、消费在状态条。

### B. 结果

- `clipExportPolicy.test.ts` 29 → **35 条**；`summarizeClipExport` **负控 7/7 全部被抓**
  （S1 待确认映射成入队 / S2 丢老字段 `exported` / S3 `exported` 盖住真实 `export_status` /
  S4 漏 `refining` / S5 `listed` 改成"未导出条数" / S6 漏 `export_status==='pending'` /
  S7 漏 `failed` 计数）。
- `Workbench` 5,222 → 5,201 行（`wc -l` 口径；本批删 22 行内联聚合、加 1 行调用）；守卫仍 135 条，其中
  `test_analysis_progress_receives_real_export_summary_counts` 改成只守"接线"
  （聚合来自 `summarizeClipExport`、结果真的作为 prop 传下去、收尾完成保留 `...previous`），
  删掉两条字段名文本断言与那条"防写法"否定断言。
- 门禁：ruff 0 / mypy Success(103) / 两个 tsc 工程 0 错误 / vitest 29 文件 **350** 用例 /
  守卫 135 条全绿 / pytest 2224（本批未动 Python，与上一批同）。

### C. 两次"漏过"都出在变异或夹具上，不是测试没牙

第一轮 S1、S5 报漏过：

- **S1 是无效变异** —— 把 `'pending'` 加进 `if (status === 'queued' || ...)` 之后，
  分支体是 `summary[status] += 1`，而 `summary` 没有 `pending` 键，
  于是只是往对象上多挂了一个垃圾键，`queued` 纹丝不动。
  真实缺陷形状是"待确认被映射成入队"，改成改写 `status` 表达式的变异后才被抓。
- **S5 是夹具不区分** —— `listed` 的变异用 `!c.exported` 过滤，而我给的样本只设了
  `export_status: 'completed'`、没设老字段 `exported`，过滤自然不生效。
  补一条"老字段也算已导出"的样本后被抓（这条样本本身也是有价值的：`exported` 是兼容字段）。

### D. 负控脚本在 Windows 上必须带写重试

跑 S1 时 `io.open(TARGET, "w")` 抛 `OSError(22)`，而且是在 `finally` 还原那一步抛的
—— 结果被测文件被留在**变异态**。修法是：写入带重试（前一个 `vitest` 子进程可能还压着句柄），
且还原后**读回比对** `content == ORIG` 才打印"已还原"。
本轮已手工还原并复测；脚本改法并入 §11.23-D 的负控规矩第 3 条。

## 11.26 第十八批：手动切片构造从 `handleAddClip` 闭包里搬出来（并修掉一处跨房跳号）

### A. 这一段为什么必须先搬

`handleAddClip` 里那个 `ClipSegment` 字面量决定"导出的时候按哪条时间轴取内容"：
是否冻结 `mark_*_wallclock`、是否带 `recording_*_mono`、`mark_precision` 记 `exact`
还是 `approximate`（后者在批量导出时被 `isApproximateClip` 拦下）。
它过去被三条守卫以"切 `useCallback` 函数体找子串"的方式守着，其中
`test_manual_clip_uses_per_room_counter` 的第一条断言写的是
`"room_id === roomId" in add_body or "filter" in add_body` —— **经核是被上面的房间查找顺带满足的**，
跟它想守的计数器毫无关系（继 §11.23-C 的"被注释满足"之后第二个同类样本）。

搬进 `src/utils/manualClip.ts`：`countManualClips` / `buildManualClip` / `distributeManualIndexes`。

### B. 顺带修掉的一个真实缺陷

公共轴批量添加那条路径原本这样编号：

```ts
res.clips.forEach((c, i) => {
  const roomManualCount = store.clips.filter(cc => cc.room_id === c.room_id && cc.source === 'manual').length
  label: formatManualClipLabel(..., roomManualCount + i + 1)
```

`roomManualCount` 是**按房间**取的，`i` 却是**跨房间的全局下标** ——
两间房各加一片时，第二间拿到 `base+2`（跳号）；三间房时更靠后。
现在用 `distributeManualIndexes(items, baseCountOf)`：每个房间各自从自己的基数往上累加。
`[A,B,A]` 且 `base(A)=1, base(B)=0` ⇒ `[2,1,3]`（旧实现给 `[2,2,4]`）。

另两处口径收紧（都属于原文本守卫想守但守不住的）：
- mark 为 `undefined` 时旧代码会照样建出一条 `start: undefined` 的切片
  （守卫条件写的是 `!== null`）；现在要求有限数值，否则返回 `null` 并提示设置入出点。
- 批量与单房两条路径此前各写一份字段清单，属 §2 "多份手抄"；现在同一构造。

### C. 结果

- 新增 `src/utils/manualClip.test.ts` **11 条**；**负控 9/9 全部被抓**
  （M1/M2 计数少筛一个维度、M3 复现被修掉的跳号、M4 同房重号、M5 `&&`→`||` 精度放松、
  M6 非有限 mark 漏网、M7 序号进 label 少 1、M8 不冻结墙钟快照、M9 录制轴秒数写死）。
- 守卫仍 135 条：`test_add_clip_snapshots_wallclock_fields` 改成只守"真的调用了构造器"，
  `test_manual_clip_uses_per_room_counter` 改成直接认那个计数器 + 批量路径接上 `distributeManualIndexes`。
- `Workbench` 5,201 → 5,188 行（`wc -l`）。门禁：ruff 0 / mypy Success(103) / 两个 tsc 工程 0 错误 /
  vitest 30 文件 **361** 用例 / 守卫 135 条 / pytest **2224** 全绿。
- 一条新的迁移税证据：文本断言**不只住在 `test_frontend_stability_guards.py`**。
  本批搬完构造器后，先跑守卫文件是全绿，跑全量 `pytest` 才掉出
  `tests/test_ux_important_followups.py::test_add_clip_and_export_snapshot_content_offset`
  —— 它在另一个文件里断言 `content_offset:` 出现在 `handleAddClip` 体内。
  已改为"添加路径确实调用构造器 + 构造器本体冻结 `content_offset`"。
  结论：**每次搬迁必须跑全量 pytest，不能只跑守卫文件。**
- 一条**未改**的口径不对称（记档）：`mark_precision='exact'` 只看"三件套字段在不在"，
  而 `recording_start_sec/end_sec` 还要求换算出的区间非空。于是存在"`exact` 但录制轴秒数为 null"
  的组合（墙钟与录制锚点明显不同源时）。改动它会影响导出取哪条轴，属协议级判断，
  留给 #21/#16 一类需要用户确认的事项一起处理；测试里已把现状钉住并注明。

## 11.27 第十九批：批量导出的"发请求 + 乐观更新"搬出 `Workbench`，并修掉一处**列表项串写**

### A. 搬迁中掉出来的真缺陷

`handleExportMany` 的成功分支与失败分支各写了一遍"这条切片对应列表里哪一条"：

```ts
c.clip_id === clip.clip_id
  || (c.start === clip.start && c.end === clip.end && c.room_id === clip.room_id)
```

关键在于 **手动切片没有 `clip_id`**：`ClipSegment.clip_id?: string` 是可选字段，
而 `appStore.addClip` 不补 id（`buildManualClip` 也不产出）。于是对两条手动切片来说
`c.clip_id === clip.clip_id` 是 `undefined === undefined` ⇒ **恒真**。
后果不是"看起来不对"而是可观察的错误状态：

- 批量导出第 1 条时，列表里**所有**无 id 的切片都被写成 `export_status:'queued'` 且共用同一个 `job_id`；
- 后端回来的 `clip_queued`/`clip_completed`/`clip_failed` 按 `job_id` 认领，因此进度与失败
  会落到**错的条目**上；被 skip（无录制文件）的那条也会显示"排队中"。

同一形状在 `handleConfirmClip` 里其实是**防住了**的（那里写的是
`(c.clip_id && c.clip_id === clip.clip_id) || …`）—— 一处防住、一处没防住，
正是 §2 "同一判据多份手抄、各自漂移"的教科书样本。

修法：抽 `isSameClipEntry(candidate, target)`，`clip_id` 两边都有才认 id，否则退回
"同房间 + 同入出点"三元组。负控 F1 就是"把判定还原成搬迁前的写法"，被 4 处断言抓到。

### B. 一起搬出去的东西（`src/utils/clipExportFlow.ts`，119 行）

| 导出 | 判据 |
| :--- | :--- |
| `isSameClipEntry` | 列表项身份（上表） |
| `buildExportRequest` | 有 `clip_snapshot_id` ⇒ `export_clip_by_id` 且**不传区间**；否则 `export_clip` 并冻结墙钟/录制锚点/`content_offset` + `use_room_marks:false`；快照切片的认领 id 用 `clipSnapshotJobId` |
| `normalizeExportedRange` | 后端回的实际区间：必须是真 `number`、有限、出点晚于入点 |
| `markClipsQueued` / `markClipsFailed` | 乐观更新（返回新数组；成功时清掉上一条失败文案） |

### C. 结果

- 新增 `src/utils/clipExportFlow.test.ts` **13 条**；**负控 11/11 全部被抓**
  （F1 串写缺陷、F2 漏 `use_room_marks:false`、F3 不冻结墙钟、F4 `source` 反号、
  F5 快照分支多传区间、F6 相等端点算有效、F7 不挡字符串、F8 不清旧错误、
  F9 不写 job_id、F10 失败不写 exported:false、F11 认领 id 来源错）。
- 三条守卫按判据改写（`test_batch_export_confirms_each_clip_with_own_bounds`、
  `test_add_clip_snapshots_wallclock_fields`、
  `test_ux_important_followups.py::test_add_clip_and_export_snapshot_content_offset`）：
  字段冻结的断言指向 utils，Workbench 侧只留"循环里逐条调用构造器"这类接线。
  **上一批的教训再次生效**：这三条里有两条不在守卫文件里，只跑守卫文件会漏。
- `Workbench` 5,188 → 5,156 行。门禁实测：ruff 0 / mypy Success(103) / pytest **2224** /
  两个 tsc 工程 0 错误 / vitest 31 文件 **374** 用例。

## 11.28 第二十批：预览"该不该重建 / 该说什么话"搬进 `utils/previewLifecycle.ts`

### A. 这一簇的守卫形状最说明问题

`VideoPreview` 里两条判据的守它是**否定式文本断言**：

```python
phase_block = source.split("const phaseHint", 1)[1].split("return (", 1)[0]
assert "previewPhase === 'refreshing_url'" in phase_block
assert "bilibili" not in phase_block.lower()
assert "platform"     not in phase_block.lower()
```

前一条只能证明"那个字符串比较还在那一段里"，后两条只能证明"没写出平台名"。
二者都**不能**证明分支正确：把 `'probing'` 与 `'refreshing_url'` 的文案对调，三条断言全绿；
`shouldRebuildLivePlayer` 的判据也一样 —— 搬迁前它没有任何行为级防线，
而判据错一个条件的表现是"每次 store 抖动就拆一次播放器"或"回到直播要重新拉流"。

搬进 `src/utils/previewLifecycle.ts`：
- `shouldRebuildLivePlayer(prev, next)`：**只有** `next.epoch` 非空且与上一帧不同才重建；
  `mode` 在 `live_mse ↔ recording_review` 之间切换**不**拆 `LivePlayer`（回看另有 ReviewPlayer）。
- `previewPhaseCopy(phase, t)`：`refreshing_url` 带"首次 10–30 秒"提示，`probing` 不带，
  其余（含 phase 缺失）落默认档；文案对所有平台同构。

### B. 结果

- 新增 `src/utils/previewLifecycle.test.ts` **9 条**；**负控 8/8 全部被抓**
  （L1 挂载即重建、L2 丢掉 epoch 非空判据、L3 改成按 mode 判、L4 同 epoch 也重建、
  L5 丢等待提示、L6 探测档落默认、L7 默认档改文案、L8 文案写进 `bilibili`）。
  "平台无关"从"某段源码里没有平台名"升级成"**逐阶段扫一遍输出**里有没有平台名"。
- `VideoPreview` 1,031 → 1,024 行（`wc -l`；组件侧只留委托调用）。
- 守卫仍 135 条：`test_video_preview_phase_hint_is_platform_agnostic` 改成只守委托关系。
- 门禁：ruff 0 / mypy Success(103) / pytest **2224** / 两个 tsc 工程 0 错误 /
  vitest 32 文件 **383** 用例。

### C. 剩余 VideoPreview 守卫的定性结论

其余 ~10 条 VideoPreview 守卫守的是 **React ref 生命周期与接线**
（"重建播放器时不得 `disconnect` MediaElementSource"、"注册表里要保住 audio/gain 节点"、
"`volumechange` 里要跳过自己写入触发的回环"）。它们的形状是"`X` 出现在这段、不出现在那段"，
判据本身没有可纯化的输入/输出 —— 要把它们升级成行为用例，需要给该组件配 jsdom 渲染测试
（现在 `Workbench/index.test.tsx` 那套是 20 个 mock + 子组件占位，不足以承载）。
**这属于"该保留文本守卫"的一类**，与 §5 的结论不冲突：文本守卫对"漏接线/重命名"有效，
对"返回错值"无效；本批把后一类挑出来搬走，前一类如实留下并说明理由，不做"为了数字好看"的假迁移。

## 11.29 第二十一批：MSE 缓存收进 `MseCacheStore`，外加一次"把抖动测试改成有牙的确定性测试"

### A. 搬到哪、修了什么

`useWebSocket.ts` 里 4 个模块级字典 + 一个手写全局计数器（`_mseInitCache` / `_mseInitCacheTime`
/ `_mseSegmentCache` / `_mseSegmentCacheTime` / `_mseSegmentCacheBytes`）连同 TTL、单房段数上限、
跨房总字节上限三段判据，收进 `src/utils/mseCacheStore.ts` 的 `MseCacheStore`（时间从参数注入）。

搬迁过程中处理掉三件事：

1. **补回淘汰候选过滤**：原实现在跨房总量超限的 `while` 里带一个
   `.filter(key => cache[key]?.length > 0)`。我第一版重写把它丢了 —— 一旦某个房间在
   `segmentAt` 里有时间戳但没有段，`clearRoom` 释放不了任何字节，**那个 while 不会终止**。
   现在显式"候选必须真的还有段，没有就 break"。（这也是负控 T5 的教训，见 §C 之外的一条。）
2. **修一条小泄漏**：`_lastRuntimeEventByRoom`（防乱序 `runtime_event` 把房间健康态往回滚的水位表）
   此前**从不清理** —— 房间移除后水位条目永久留在 Map。现在 `clearMseRoomCache` 一并删。
3. `clearRoom` 与"只清 init"两条路径的 `onRoomCleared` 回调语义分开（负控 T9 守这条）。

### B. 结果

- 新增 `src/utils/mseCacheStore.test.ts` **10 条**；**负控 8/10 被抓**：
  T1 段过期不回填字节、T2 TTL 边界 `>`→`>=`、T3 淘汰挑最新房间、T4 单房超量漏扣字节、
  T7 `clearRoom` 不回调、T8 init 房数上限失效、T9 `dropInit` 多回调、T10 总上限判断反向。
- 两条"漏过"是**等价变异**（不是测试没牙）：
  - T5（`drainSegments` 不删时间戳）：公开 API 下两张 Map 总是同增同删，造不出悬挂时间戳；
  - T6（去掉 `clampNonNegative`）：正确记账下计数不会为负，clamp 只是保险。
  两条都**保留**了 clamp 与"候选非空"过滤作为廉价保险，并在此记录它们当前不可观测。
- `useWebSocket.ts` 由 ~870 行降到 835 行；守卫仍 135 条，
  `test_mse_caches_have_ttl_and_room_cleanup` 改成只守 **store 之外**的那半截清理
  （init 重试定时器、watchdog 两张表、播放头、runtime 水位）。

### C. 一次真闪断，和它暴露出来的两件事

全量 `vitest`（与全量 `pytest` 并发）把 `localFileMseSource.test.ts` 里
"定位期间有持续读盘进展时不得判死"这条**从前绿过的用例**跑红了。处理链值得记下：

1. **第一反应是放宽预算**：`firstFrameTimeoutMs: 300 → 1500`。改完立刻跑变异
   （删掉"有进展就顺延"那条分支）——**测试变绿了**：放宽预算等于把这条判据的防线搬空。
   这个改法被自己否掉。**规矩：改测试的时间/阈值参数之后必须重跑变异**，
   否则会在不知不觉间把守门用例改成纸糊的（正是 §5 批评的形状，只是这次是我自己制造）。
2. **真正的根因是用例前提已经过期**：它靠"每次读盘 sleep 12ms × 顺序扫 ~4.7MB"制造慢扫描，
   但 §11.10 把远定位改成二分探测之后，实际只有个位数次读盘 ——
   300ms 窗口到不到点，取决于机器快慢，而不是代码性质。所以它必然抖。
3. **最终改法**：`vi.useFakeTimers()` + `advanceTimersByTimeAsync`，并且**读盘延迟也走虚拟时间**
   （`setTimeout(resolve, 60)`），于是"扫描很慢"发生在被测代码的时间轴上：
   60ms × 探测数稳定跨过 300ms 看门狗窗口 ⇒ 顺延分支必被跑到。
   实测：基线连绿 3 次（其中两次与全量 `pytest` 并发），变异被抓，还原后复测绿。

**通用结论**：本仓凡是"用 `sleep` 制造慢/停滞"的用例都属于同一风险类（§11.9 已把 4 条墙钟
预算用例改成条件等待，这次是第 5 条，且它需要的是**假时钟**而不是条件等待）。
判据：慢/停滞必须由注入的时间源产生，不能由真实调度产生。

### D. 门禁

ruff 0 / mypy Success(103) / pytest **2224** / 两个 tsc 工程 0 错误 /
vitest 33 文件 **393** 用例（本批 +10 存储用例，另 1 条既有用例改为确定性时钟）。

## 11.30 第二十二批（#23 时序用例审计）：抓出一条**永远不会失败**的断言

### A. 审计方法与结论

把全仓所有"用真实 sleep 驱动"的测试点列出来（vitest 15 处、pytest 37 处），按**sleep 在断言里扮演什么角色**分三类：

| 类别 | 含义 | 判定 |
| :--- | :--- | :--- |
| ① 让事件循环转一圈的 yield（`sleep(0)` / `0.01–0.05`） | 只给协程调度的机会 | 保留；红/绿不取决于其长度 |
| ② **sleep 本身就是被测阈值的输入**（"合并窗口还没到"/"已过 TTL"） | 断言的真假直接挂在挂钟上 | **必须改**，否则红绿取决于机器 |
| ③ 慢是**夹具主动提供的刺激**（假 CDN 故意慢 0.15s、后台线程 0.3s 后才写文件） | 误差方向只会让刺激更强 | 保留并记录 |

第 ② 类在 pytest 侧只找到一处，就是本批的靶子；vitest 侧的那条（回看首帧看门狗）已在 §11.29-C 改完。

### B. 靶子：`test_persistence_coalesce.py`

原用例（唯一一条）：

```python
schedule_save_rooms(..., delay_sec=0.2); schedule_save_rooms(..., delay_sec=0.2)
time.sleep(0.05)
assert not path.exists() or load_rooms(path) == [{"room_url": "https://a"}] or True
```

`or True` 让这条断言**恒真** —— 它守的"合并窗口内不应落盘"从未被验证过；
`time.sleep(0.05)` 也不影响任何结果。这是 §5 "不能失败的测试不是测试"的第 3 个实证
（前两个：被注释满足的守卫、被房间查找满足的计数断言）。

重写为 5 条不依赖挂钟的用例，机制直接可断言：
注入假定时器（替掉 `persistence.threading`）⇒ "取消上一个定时器"、"只留一个 timer"、
"`delay_sec` 透传"、"待写快照是最后一次且是**拷贝**"、"到点回调真的落盘且不重复落"、
"fsync 每 N 次才做一次"（这条必须**包一层**真 `save_rooms`：写计数在其内部自增，整个替换会让判据失去输入）。

### C. 结果与验证

- **负控 8/8 全部被抓**（P1 不取消旧定时器、P2 不拷贝快照、P3 快照只留第一条、
  P4 delay 写死、P5 回调不消费 pending、P6 fsync 每次都做、P7 永不做、P8 手动 flush 不取消）。
  同 8 条变异在**改前**的用例下全部为绿 —— 差别的量级就是那条断言的有效性。
- 用例数 1 → 5；套件时长没增加（去掉真 sleep 后反而更快）。
- 顺手按 §2.5 把误放进函数内的 `import types` 提到文件顶部。
- 门禁：`ruff check .` All checks passed / `mypy` Success(103) / `pytest` **2228** 全绿；
  本批未动前端，vitest/tsc 仍为上一批实测的 33 文件 393 用例、两工程 0 错误。

### D. #23 的收尾判断

第 ② 类已清空。要让第 ③ 类里"慢"的**量级**也可控，需要生产代码接受注入时钟
（`ProbeService` / 持续分析 poller 目前直接读 `time.monotonic`）—— 那是跨模块设计改动，
不是测试债，且当前误差方向安全，**不在本债项内做**；已在任务里写明，避免下次又当新发现。

## 11.31 第二十三批：人工确认切片的三段判据搬进 `utils/clipConfirmPolicy.ts`

### A. 搬了什么

`handleConfirmClip` 里三件纯判据（此前只被"切函数体找子串"的守卫守着，而且这些守卫
**分散在两个 pytest 文件里**）：

| 导出 | 判据 |
| :--- | :--- |
| `resolveConfirmBounds` | 边界优先级：拖动游标 > 公共轴 mark > 房间 mark > 切片自身；非精修一律只用自身边界；`end > start` 才有效 |
| `selectSyncTargetRooms` | 同步范围：本次分析冻结目标集 > 同对齐组 > **空**；剔掉断连/不存在/本房间；禁止退回"全量已连接房" |
| `applyConfirmedClipUpdate` | 乐观更新的作用域与条目身份：先按 `room_id` 拦，再按"key 命中 → 本条 id 命中 → 几何相同" |

换算（`commonToRecording` / `toRecording` 及它们的 `try/catch` 回退）留在组件里 ——
那部分依赖 timeline ctx 与 store，硬搬只会把闭包依赖搬进 util。

### B. 一处我自己读错了原判据（值得记）

写用例时我先按直觉断言了"冻结目标集里有跨组房间 ⇒ 返回空"与
"目标集跨组 ⇒ 不回落到分组"。跑出来都不对：原实现是
`isUsable` 带 `(!groupId || r.align_group_id === groupId)`，
**跨组的冻结目标会被整个剔掉，随后照旧回落到同组**。
即"回落"发生在过滤之后，而不是"目标集非空就不回落"。

核对下来这是原实现的既有语义（同组共享同一条时间轴，组内同步本就是有意的），
移植必须忠实 —— 于是**改的是用例不是代码**，并把它显式命名成
`目标集全不可用时回落到同对齐组（而不是回落到空、也不是回落到全量已连接房）`，
免得后来人把它当 bug 顺手"修掉"。

### C. 结果

- 新增 `src/utils/clipConfirmPolicy.test.ts` **16 条**；**负控 13/13 全部被抓**
  （C1 非精修也读 mark、C2 mark 优先级颠倒、C3 丢拖动游标、C4/C5 有效性判据、
  C6–C9 同步范围四种破法、C10 丢房间作用域、C11 `clip_id` 缺失时靠 `undefined===undefined`
  全命中（§11.27 同型缺陷的第三条）、C12 key 命中失效、C13 AI 高光不纠正录制轴）。
- 三条守卫按判据改写：`test_clip_list_batch_export_includes_pending_confirmable_clips`、
  `test_confirm_clip_optimistic_update_scoped_to_room`（frontend_stability_guards）、
  `test_confirm_clip_filters_disconnected_targets`（**cross_feature_guards**）。
  又一次印证 §11.26 的规矩：第三条不在守卫文件里，只跑守卫文件会漏。
- `Workbench` 5,156 → 5,140 行。门禁实测：ruff 0 / mypy Success(103) / pytest **2228** /
  两个 tsc 工程 0 错误 / vitest 34 文件 **409** 用例 / 守卫 135 条。

## 11.32 末批：§7.3 全量复核 + 两处收口（rooms.json 形状闸门、编排器满员背压）

### A. 复核 §7.3 —— 它才是本轮任务的范围定义，所以逐行回代码核

方法：不再凭批次记忆，每行用 grep / 读实现复现一遍；能复现的标批次号，不能复现的改状态。
14 行结果：**9 行**已修（各自附批次号）、**1 行**经核不是缺陷（嵌套 `wait_for(shield)` 是有意设计）、
**1 行**本批收口（`save_rooms`）、**3 行**仍开且都需要外部条件（#13 剪映根因需真机标定、
#12 settings 搬家经实测不安全、i18n 剩文案本身而非门禁缺口）。

这张表的教训与 §11.16-C 同一条：**报告里每条"已收口"必须能被一条 grep 或一个测试复现**，
否则表格会长期比代码旧 —— 本次 14 行里有 9 行就是这种情况。

### B. `save_rooms` 形状闸门（原 r30）

- **事实**：WS 入口 `handle_save_rooms` 有严格校验（#14 抽出 `_room_url_shape` 与 `add_room` 共用），
  但内部自动保存路径（controller 状态变化 ⇒ `schedule_save_rooms` ⇒ `save_rooms`）**不做任何形状检查**。
  controller 一旦出 bug 吐出非 dict 条目或缺地址的条目，就原样写进 `rooms.json` ——
  而它是重启后恢复房间的唯一来源，后果是"下次启动凭空少房间"。
- **处置**：`room_payload_violation()`（`persistence.py:107`）在 `save_rooms` 顶部把关，
  违规记 `error` 日志并 `return False`。刻意**只拒绝、不静默过滤**：丢掉条目本身就是数据丢失，
  宁可整次写入失败并留痕。
- **判据不得严于真实契约**（本批踩到的坑）：第一版只认 `room_id`/`room_url`，
  全量 pytest 当场打红两个用例 —— 持久层有**两种历史写法**，orchestrator 落 `url`、
  handler 快照落 `room_url`，而 `room_handler._restore_rooms_from_disk`（:4366）读的是
  `room_url or url`。改成三键之一非空后绿；并补 `test_save_rooms_accepts_both_legacy_identity_shapes`
  把这个契约钉住，免得下次"收紧校验"又把老格式拦死。
- **新用例 4 条**（`tests/test_save_rooms_validation.py`，共 10 条）：整次拒绝且原文件完好 /
  从不静默丢条目 / 调度落盘路径同样被拦 / 两种历史身份形状都收。
- **负控 5/5 全部被抓**：V1 不查顶层类型、V2 不查条目类型、V3 完全不查身份、V4 空白身份放行、
  V5 闸门不影响写入。V1 的判别输入是 `{}` 空 dict 载荷 —— 只给 `"not-a-list"` 拦不住，
  因为去掉顶层检查后字符串仍会被条目循环逐个判死（首个 survivor 就是这么来的）。

### C. 收口过程中撞出的真缺陷：编排器满员即时拒绝

- **事实**：`test_concurrent_add_and_list_rooms` **在 HEAD 就是红的**（`git worktree` 检出 `dbeb707d`
  复现 3/3，与本批改动无关），报 `orchestrator too busy (8 pending, max 8)`。
- **根因**：`call()` 在 `_pending_count >= _MAX_PENDING_REQUESTS(=8)` 时**立即抛错**，
  而房间入口由线程池并发投递（`room_handler` 大量 `run_in_executor(... orch.call ...)`），
  产品默认 `max_rooms = 12 > 8`。⇒ 用户"一次粘贴 8~12 条链接批量添加"这种正常操作，
  必然瞬时打满 8 个同步槽位，把**正常的背压变成用户可见的添加失败**。
  这条用例历史上偶尔绿，是因为它顺带依赖外网解析 `example.com` 的快慢（派发快就不排队）——
  依赖外网本身是遗留问题，但**没有靠放宽断言绕过去**。
- **处置**：满员改为**等到本次调用的 deadline**：新增 `_acquire_pending_slot(deadline)` /
  `_release_pending_slot()`，用与计数器**同一把锁**的条件变量（不做"信号量 + 计数器"两套真相，
  否则漂移就是新的静默失效）；槽位等待与结果等待共用一个 deadline ⇒ 总耗时仍 ≤ `timeout`；
  `too busy` 文案与异常类型不变。
- **不需要改既有断言**：`test_call_rejects_when_pending_full` 给的是 `timeout=1.0` 且 8 个槽位被
  5s 的阻塞调用占死 ⇒ 等待同样在 1s 后抛 `too busy`，语义仍成立。
- **证据强度：用真·回退当负控。**新用例 `test_call_waits_for_a_slot_instead_of_failing`
  在 HEAD 的即时拒绝实现下**失败**、在本实现下通过（同一文件同一用例，只换实现），
  比合成变异更贴近真实破法；三处释放点（取消 / 正常派发 / 队列满）与原计数器递减点一一对应，
  用例末尾另断言 `pending_count` 归零，防泄漏。
- §7.4 里"`call()` 超时后结果未知、无重试语义"**仍开** —— 本批只改满员行为，没碰结果未知。

### D. 门禁复测 + 一条命令口径纠正

- ruff 0 / mypy Success(**103 files**) / pytest **2233** / tsc 两个工程 0 错误 /
  vitest 34 文件 **409** 用例 / 守卫 135 条 / 覆盖率 64%（下限 55）。
- **口径纠正（实测）**：从根目录跑 `mypy .` 会把 gitignore 掉的 `artifacts/model_bench/*.py`
  一起检查并报 6 条 —— 那**不是**门禁命令。CI 与 `pyproject` 的口径是
  `mypy --show-error-codes` + `files = ["lsc", "python-backend"]` ⇒ 本地复测必须用后者，
  否则会把别人留在工作区的实验脚本当成门禁变红。
- **Workbench 未继续搬家**：#12 的 settings 簇已有 §11.x 实测不安全的结论，本批未触碰。

### E. 本轮结束时的交接清单（每条都写清"解锁它需要什么"）

到 §11.32 为止，审计里**所有"我方能自行判定并验证"的条目已全部处理完**。剩下的 6 条不是工作量问题，
而是**缺输入**——硬做只会把不确定性转移给用户，所以逐条列出解锁条件：

| 开项 | 解锁它需要什么 | 为什么不能自行收 |
| :--- | :--- | :--- |
| #13 剪映候选去重 §7.1 根因（强终态快路径无"回退更长游程"） | 2~3 段**带人工标注回合起点**的真实录像（含闪变帧那种） | 改的是检测召回判据，没有真值集就无法判断"修好了"还是"换了一批新错" |
| #19 预览时钟冻结根因 | 现场复现时的 `debug.log`（`lsc.mseDebug='1'`）或 §7.4 建议的周期性快照先上线 | 放大器（自救链）已修；根因需要"致死过程"曲线，而当前快照只在死亡瞬间构造 |
| #15 仓库历史瘦身 | 用户授权改写历史（force push） | 影响所有协作分支与已发布 tag，不可逆 |
| #16 发布编号 v1.0.18 vs 已存在的 v3.0.23 | 产品决定：回到 1.x 线还是继续 3.x | Release 完整性门禁会拦下 v1.0.18；两个方向都合法，只有用户能定 |
| #21 持续分析入口是否禁用「通用」档 | 产品确认（该档永不产出，但可能有对外承诺） | 属对外行为变更，不是缺陷修复 |
| #12 settings 簇搬家 / i18n 棘轮 | 已被实测否证（搬家会让守卫集体变红且触及用户设置持久化格式；棘轮首跑数百告警） | 结论是"不做"，但需用户认可这个取舍 |

**另记一条本轮主动没做的（并已按实测改正结论）**：
`tests/test_stability_guards.py::test_concurrent_add_and_list_rooms` 用 `https://example.com/live-N.m3u8`
造房间。原本记的"根治=换成保留 TLD 走快速 NXDOMAIN"**经实测不成立**：本机解析器对**任意**域名都返回地址
（`lsc-nonexistent-host.test → 198.18.0.28`，12ms），换主机名并不会让它在离线/无代理环境下变快，
只是把"解析"变成"连不上"。同时回看 `add_room`（`orchestrator.py:864-888`）：它**同步**建 `RoomSession`，
只有"房间数达上限/URL 重复"才返回 None，真正联网的是后台 worker ⇒ 这条用例的**断言本身**
（不抛异常 + `room_count()==8`）已不再依赖网络结果，只依赖"编排线程忙多久"这一时延，
而时延引起的失败已被 C 节的有界等待消除。
⇒ 剩余的不是缺陷而是一次**测试隔离设计改动**（给 `add_room`/connect 提供可注入的离线 resolver，
让测试完全不产生出站连接），与 §11.30-D 记的"慢是夹具提供的刺激就不再制造可控性"同类，
本轮预算内不开这个头。要开的话先定接口：`RoomOrchestrator(connect_factory=...)` 还是
`resolver_factory=...`，别在测试里 monkeypatch 平台层。

CI 运行史仍待 `gh auth login`（§8 发布链与门禁 job 的实际红绿只有 GH 侧能看到）。

## 11.33 用户三项决策的落地（#21 通用档 / #16 发布编号 / #15 仓库瘦身授权）

### A. #21「通用」档在持续分析入口禁用（已做）

- 判据放进可测层：`src/utils/analysisProgress.ts:isContinuousAnalysisModeAllowed(mode, isContinuous)`
  —— 只有 `!isContinuous || mode === 'valorant_round'` 放行。**为什么现在才做**：
  后端 `analysis_handlers.handle_start_continuous_analysis` 早就有这条能力闸（§11.18-D），
  前端却一直把「通用直播」摆成可选项，用户要点了"开始"才吃到后端报错，
  甚至以为该功能存在 —— 典型的"纸面功能"留在入口上。
- 接线两处共用同一份判据：单选按钮 `disabled`（并给一行改选提示）＋提交校验
  （放在"必须在录制"那条**之前**，否则用户会先看到不相干的报错）。
- 用例：vitest 3 条（放行组合 / 一次性分析不受限 / 未知 mode 保守拒绝）；
  接线守卫 `test_continuous_analysis_entry_blocks_generic_mode`；守卫 135 → **136 条**。
- **顺带撞到一条自己的老守卫**：`test_workbench_continuous_analysis_uses_explicit_game_modes`
  逐字钉 `<Radio.Button value="generic">{t('通用直播')}</Radio.Button>` 整行 JSX，
  加一个 `disabled` prop 就把它撞红。改成不钉排版、只守"档位显式存在 + 文案未被改"，
  判据本身交给 vitest —— 这正是 §11.21 立的规矩在现实里第二次兑现。

### B. #16 发布编号：定线 1.x（已做，并掉出两个新事实）

- 决策落地为三条 CI 判据：tag 必须 `v1.x.y`；**倒退只在同线（`v1.*`）判定**；
  跨线更低改为响亮 `::warning::`（旧口径拿全标签比，会被已废弃的 v3.0.23 永久拦死 1.x）。
- **新事实 1（改正本报告 §8）**：1.x 线上其实有 `v1.5.0`（2026-07-02），原报告手写的"标签全集"
  漏了它和 `v1.0.0` ⇒ `v1.0.18` 即便不考虑 v2/v3 也是同线倒退。下一次发版号定为 **v1.6.0**，
  CHANGELOG 顶部那节已随之改名。
- **新事实 2**：版本号写在三处且两处是旧的（`package.json=1.0.17`、`pyproject=1.0.16`、
  `package-lock=1.0.11`）。旧守卫只比 `package.json` ⇒ "发版没同步"根本不可见。
  现在三处一起比，并把 `pyproject`/lock 同步到 `1.0.17`。
- 四条路径全部**实跑**过（把 YAML 里的脚本原样抽出来执行，不是读代码判断）：
  三处同步 → 越过同步检查、停在同线倒退；人为把 `pyproject` 改成 `9.9.9` → 被新检查拦下；
  `v4.0.0` → 被"必须是 1.x"拦下；`tag≠package.json` → 被原有检查拦下。
- **已知并接受的后果**：装过 v2/v3 的用户仍收不到 1.x 更新（`compareVersions` 比数值）。
  要救他们得改更新判据（按"最新发布"而不是按数值），那是独立一次改动，本轮未做。

### C. #15 仓库瘦身：授权收到，但**本轮不能执行**，前置条件按顺序如下

改写历史不可逆、且影响所有协作者与已发布 tag，所以即便已获授权，也要按顺序满足前置条件：

1. **本分支 24 批未提交的改动必须先落 commit**（用户从未要求 commit，我不会自行提交）——
   在脏工作树上跑 `git filter-repo` 等于拿用户的工作当赌注。
2. 装 `git-filter-repo`（本机未确认存在），并先做一次**完整镜像备份**：
   `git clone --mirror` 到 D 盘 + 校验 `git fsck`，回滚点没验过就等于没有。
3. `gh auth login` 之后才能核对远端：哪些 tag/Release 真正存在、有没有别人的分支
   基于将被改写的历史。
4. 与数据集解耦的更省事方案（`git submodule` / 单独的数据 release 资产 / LFS 迁走）
   应先比一遍再决定动不动历史 —— clone 2.08 GiB 里 98.7% 是数据集，
   把数据挪出去可能比改写历史代价小得多。

⇒ 本轮只把"授权"记进任务与 CHANGELOG，**未执行任何不可逆操作**。

### D. 门禁复测（本节改动后，全部现场重跑）

`ruff check .` 0 / `mypy --show-error-codes` Success(103 files) / 全量 pytest **2234 passed**
（2233 + 1 条新接线守卫）/ 后端文子守卫 **136 条** / vitest 34 文件 **412 用例** /
`tsc --noEmit` 两个工程各 0 错误。

## 11.34 现场抓到的"假失败"：停录超时被答成停止失败（§7.4 编排底座，方案 A）

### A. 证据是先跑出来的，不是想出来的

用户实跑（录制 32 分钟后停录并关窗）留下的时间戳：

```
16:17:22 停止录制: wait_for_finalize=False
16:17:32 [ERROR] 停止录制异常: error=orchestrator call timed out   ← 调用方 10s 放弃
16:17:34 recording hot failover merged segments=2 …                ← 编排其实把录制停掉了
16:17:34 sidecar 已随录像定稿改名 1 项 … .analysis.json
16:17:34 持续分析已停止: 累计 2 段高光
```

`handle_stop_recording` 的 except 分支原先无条件 `return {'success': False, ...}` ⇒
**录像完好定稿，用户却看到"停止录制失败"**。这就是 §7.4 记的"`call()` 超时后结果未知"
在真实路径上的一次落点（上一批我只改了满员背压，没碰结果未知）。

### B. 修法与两处顺带

- **回查再回答**：异常分支改成 `wait_recording_stopped(...)`，在 8s 窗口内轮询录制状态；
  确实还在录才答失败，已停就答成功并记一条 `warning`。窗口 8s 的依据就是上面那条
  "超时后 2s 定稿"（4 倍余量），不是拍的。
- **必须用不经 `call()` 的读法**：`manager.get_room()` 内部走 `call()`，编排线程正忙时
  回查会跟着一起超时 —— "读结果"变成第二个故障点。所以走
  `get_recording_status`（它的 docstring 明确写了"故意不经 call()"）。
  **同一条判据启动侧早就修过、停止侧漏了**，这正是本报告反复出现的
  "多份手工同步"母题；本次把读取逻辑提成模块级 `read_room_recording_status`，
  启动侧闭包改为委托它，不留第三份。
- **顺带掉出的一处真隐患**：原 `room = manager.get_room(room_id)` 直接跑在
  **asyncio 事件循环线程**上，经 `call()` 等编排线程 ⇒ 最坏把整个后端卡住 10 秒。
  现改为进线程池读，且读不到只回空路径，不影响已判定的 `success`。

### C. 证据强度

- 新增 `tests/test_stop_recording_false_failure.py` **10 条**（含 3 条参数化的"读不到快照不得乐观"）；
  轮询预算与间隔都是**注入参数**，不做"sleep 当生产阈值"那类用例（§11.30 分类 ②）。
- **负控 7/7 全部被抓**：N1 不回查直接判失败、N2 一律乐观、N3 不认房间已移除、
  N4 读不到快照当已停、N5 状态读取绕过快照、N6 退回快照丢口径、N7 handler 退回旧写法。
- 门禁：ruff 0 / mypy Success(103) / 全量 pytest **2244** / 覆盖率 64% / 守卫 136 / vitest 412（前端未动）。

### D. 仍然没做完的部分（不要当成已闭环）

1. §7.4 的"**结果未知无重试语义**"只收了一半：现在保证不假失败，但超时后 in-flight 的
   **返回值**（例如 orchestrator 给出的真实 `success`）仍然拿不回来，回查读的是状态而不是结果。
   要做完得给跨线程调用带"代次 + 结果可回读"的语义，属设计级改动。
2. 持续分析 worker 侧同样会撞这个超时（`room_handler.py:10400` 记 `持续分析异常` + `analysis_stage='异常退出'`），
   但那一刻其实正在停录 —— **停录期间的超时是预期噪声**，该按"正在停止"降级为 info/warning，
   不该留 ERROR 与"异常退出"字样。本轮未改（要一并核对状态面板派生链，避免把真异常一起静音）。
3. 同一秒出现**两条字节相同的 `kick worker`**（16:17:17 / 16:17:19 / 16:17:21），
   像是有两个 tick 都在踢同一个 worker；有 job 槽位去重所以不致错，但白烧一次投递，待单独查。

## 11.35 把持续分析调回 main 的水平：全局派生闸误接到分析热路径

用户指令：持续分析水平不如 main，要求调回，且**不动 main**（main == HEAD == `dbeb707d`，
26 批改动全在工作区未提交，所以"调回"= 撤销工作区里让分析退化的那部分，保留其余收口）。

### A. 定位方式与排除项

两个只读子代理并行对比 `git diff main` 的处理链与支撑链，回来我自己逐条复核关键断言：

- **analyzer 侧不是原因**：`_merge_round_windows`、`_drop_open_tail_rounds`、`_is_listable_ocr_round`、
  `_continuous_valorant_worker`、扫描窗口规划、OCR 阈值与采样率与 main **逐字节相同**；
  coverage 账本 list→tuple 是类型归一（`merge_ranges` 本来返回 tuple，JSON 出口显式转 list）；
  `sound_detector` / `valorant_observer_scoreboard` 在 main 上已是死桩/零调用。
- **`lsc/config.py` 与 `resource_monitor.py` 相对 main 零改动** ⇒ 不是默认值漂移。
- 元凶在支撑链：**`run_hidden` 被接进了全局派生闸**（第 8/17 批做的），main 的 `run_hidden`
  是裸 `subprocess.run`。

### B. 根因（一句话：闸违背了自己的设计边界）

`lsc/utils/process_launcher.py` 顶部写着"**名额只覆盖启动瞬间**，长生命周期进程在 `Popen`
返回后立即记账并释放 —— 否则一个录制进程会永久占着闸"。但 `run_hidden` 里
`with spawn_slot(...)` 包的是 `subprocess.run`，**名额被持有到整条命令结束**。
`run_hidden` 恰好是 main 文档里写明的那类入口："持续分析会发大量短 FFmpeg/ffprobe 调用"。
于是一次几百秒的抽帧就把 `MAX_CONCURRENT_SPAWNS=2` 占满，后面每个短探测各等满
`_TRANSIENT_GATE_SEC=5.0` 才 fail-open —— 只加时延、不改判定，正是"吞吐腰斩但没有报错"的形状。

**现场形状实测**（`.runtime` 量测脚本，名额被 2 条长命令占满、3 个短探测）：

| 短命令路径 | 3 个探测总耗时 |
| :--- | :--- |
| 接闸（旧 `run_hidden` 语义，各等满 5s 后 fail-open） | **15.03s** |
| 不接闸（现 `run_hidden`，真起 3 个进程） | **0.25s（≈59×）** |

对应现场：`scan_throughput` 0.93 → 0.42、backlog 单调涨到 ~580s、单场 3 次 270s 扫描超时。

### C. 改法（范围严格限定在"分析热路径不再被闸拖慢"）

| 位置 | 改动 | 理由 |
| :--- | :--- | :--- |
| `process_launcher.run_hidden` | **不再占名额**，改为只计数不阻塞（`transient_spawn_total` / `transient_by_category` 进 `budget_snapshot`） | 与 main 行为一致；闸继续管它该管的长生命周期站点 |
| `shared_ingest._launch_process` | 进样启动/换线 `spawn_slot(..., timeout_sec=6.0)` 加 **`fail_open=True`** | 这是"启动失败就丢一段录制"的路径；现场硬拒后落成 `upstream ffmpeg start failed` + `failure=TIMESTAMP_DISCONTINUITY`。削峰保留（仍等 6s），否决去掉 |
| 派生闸本身 | **保留**（预览/录制/导出/进样的启动瞬间仍封顶） | §7.4 的三次事故同形是"太多 FFmpeg 同时启动"，那才是它该管的事 |

没动的三处，写清楚而不是悄悄留着：

1. `mse_streamer.py:473` 预览启动 10s **硬拒**、`capture.py:491` 录制启动 8s fail-open、
   `MAX_LIVE_CHILDREN=48` 软上限 —— 都不在分析热路径上；预览启动被拒是可重试且会显式报错，
   改成什么语义是独立决定，本轮不夹带。
2. `valorant_broadcast.py:2343 / 2812` 新增 `available_end is not None`：核过是防
   `float(None)` 抛错的守卫（main 上这条路会崩→窗口未覆盖→重扫）。**保留**，但记一句：
   若以后发现"尾部回合比 main 少"，第一个查这里，因为它把"等待更多媒体"换成了终态判定。
3. `analysis_handlers` 的 resume-finalization 启动路径不经过新能力闸 —— 与吞吐无关，
   属"入口一致性"问题，未动。

### D. 证据

`tests/test_process_spawn_budget.py` 8 → **11 条**（新增：短命令**从未 acquire 过闸**、
闸满时 3 个探测总耗时 < 1s 且仍计数、`spawn_category` 不漏进 subprocess kwargs、
进样重启必须 `fail_open=True`）。**负控 4/4 全部被抓**（把短命令重新接回闸 / 不计数 /
漏出参数 / 进样改回硬否决）。删掉了因此失效的死常量 `_TRANSIENT_GATE_SEC`。
门禁：ruff 0 / mypy Success(103) / 全量 pytest **2247** / 覆盖率 64% / 守卫 136 / vitest 412。

### E. 还需要一场真机验证

以上是"去掉人为时延"，**不等于召回已回到 main 的水平** —— 那要用户再跑一场同样长度的直播，
看 `scan_throughput` 是否回到 ≥1、backlog 是否不再单调涨、单位时间产出段数是否恢复。
本轮结束后我这边没有可比的历史数据（main 从未在同一台机器上跑过同样这场直播）。
