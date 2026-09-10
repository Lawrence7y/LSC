# Valorant 官方解说分支（broadcast）入点问题 · 工作流状态与交接

文档日期：2026-09-10
范围：`pov` / `broadcast` 双分支持续分析的**回放干扰**与**切片入点准确性**工作流
文档性质：**状态汇总 + 后续任务清单 + 阅读索引**（已做的 / 未做的 / 接下来要做的 / 需要读的）

---

## 0. 一页速览

| 维度 | 状态 |
| :--- | :--- |
| **用户原始诉求** | 官方解说分支切片质量低于第一人称分支，主要表现为**入点判断不清**；怀疑是赛事回放干扰 |
| **诉求是否成立** | ✅ 成立，且已定位到**可验证的代码级根因**（不是"模型训得不够好"这么笼统） |
| **最反直觉的结论** | 新训练的**回放标注对入点零影响**——它只被用在**出点截断**；而入点证据链 100% 依赖 OCR 交战钟 |
| **已落地代码** | `broadcast_mode` **影子模式**（只记录、不生效）+ 24 条守卫测试 |
| **已产出文档** | 3 份（根因分析、对照实验、本文件） |
| **待决策** | 是否正式开启 `broadcast_mode`（需先取数） |
| **待动手（会改变切片结果）** | 入点侧引入模型回放否决；`start_delta` 改为交叉证据 |
| **交付状态** | 本工作流可切分部分**已提交**（`3821ffb` 死代码清理 / `05ba92d` 三份文档 / `0f86d48` 影子模式）；其余 **4 条**工作流的改动仍在工作区，勿打包 → 见 §3 第 0 步执行记录 |
| **回归基线** | 全量 pytest **1791 passed / 0 failed**（2026-09-10 复核复现；须配合 §6 的 ASCII `TMP/TEMP`，否则 `test_recording_asset_timeline` 可能被 safe-delete 拦截） |

---

## 1. 已完成

### 1.1 前置：PySide6 遗留死代码清理（已完成，可回滚）

| 项 | 内容 |
| :--- | :--- |
| 删除 | `lsc/gui/`（8 文件 / **1487 行**）+ `lsc/cli.py`（**283 行**）= **1770 行** |
| 判定铁证 | 生产链路（`python-backend` + `lsc/core`）对 `lsc.gui` **零 import**（仅注释提及）；引用方全在 `tests/`；`lsc/gui/__init__.py` 自述已弃用 |
| 测试连带 | **11 个测试文件**：迁移 8 个（7 个重连测试、17 个房间管理测试、4 个并发测试、benchmark 脚本、2 处导入源）、删除 3 个纯死代码用例 |
| 测试净变化 | **−3**（全部是"测被删代码自身"的用例），活逻辑覆盖零损失 |
| 配置/文档同步 | `pyproject.toml`（移除 `lsc/gui/` 排除 + `PySide6.*` override）、`requirements.txt`、`dependency_manager.py`、`CLAUDE.md`、`README.md`、`CHANGELOG.md` |
| 回滚素材 | `C:/lsc_tmp/backup/{gui,cli.py}`；git 可 `git checkout HEAD -- lsc/gui lsc/cli.py` |
| 验证 | 后端 16 模块导入正常 + WS 服务完整启动（端口回退正常）；全量 pytest 由 1769 → 1766 passed（差值 = −3，已对账） |

### 1.2 根因分析（已完成）

**产出**：`docs/reports/valorant-broadcast-inpoint-rootcause-20260910.md`

**五条根因（按贡献度）**：

| # | 根因 | 性质 | 关键证据（文件:行号） |
| :--- | :--- | :--- | :--- |
| **R1** | OCR FSM 的「赛事回放保护」是**未接线的死代码** | 🔴 缺陷 | `valorant_ocr_rounds.py:416` 参数默认 `False`；生产调用 `:1455` 不传；`broadcast_mode=True` 仅存在于 `tests/test_valorant_ocr_rounds.py:159,183` |
| **R2** | 入点证据链 **100% 由 OCR 交战钟驱动**，模型回放标签对入点**零影响** | 🔴 设计缺口 | `valorant_ocr_rounds.py:873` 硬编码 `start_confidence=0.95`；`valorant_broadcast.py` 5 处改写 `start`（`:746/:760/:981/:1014/:1228`）无一处来自回放证据 |
| **R3** | `start_delta` 是**自洽性**指标（粗扫与密扫之差），不是**准确性**指标 | 🔴 缺陷 | `continuous_finalization.py:187-210` 用 `start_delta ≤ 3.0` 判 `precise`；两者同源同盲 → 错入点可被盖章 `precise` 静默通过 |
| **R4** | `replay` 类阈值 **0.77 高于模型自身概率均值 0.760** | 🟠 标定错误 | 模型元数据 `class_stable_prob={"replay":0.77}`（详见 §1.5 闭环复核） |
| **R5** | 回放标注只作用于**出点侧**；OCR 自产的回放标注 `replay_segments` **无逻辑消费者** | 🟠 死数据 | `valorant_ocr_rounds.py:805` 写入（会落盘到 sidecar），全仓无代码读取 |

**附带发现**：
- `_END_BANNER_KEYWORDS` 含 `"clutch"/"ace"/"triple"`（`valorant_ocr_rounds.py:100` 起）——解说高光回放的叠加字样，可让回放被判为"回合结束"；两张关键词表（`:94` `_PREP_BANNER_KEYWORDS` / `:100` `_END_BANNER_KEYWORDS`）**均无"回放/replay/重播"**
- 非切块候选入点**无前移机制**：`new_start > start` 即整条拒绝（`valorant_broadcast.py:702`）→ 只能在"接受错入点"与"丢整回合"间二选一
- `MAX_BROADCAST_ROUND_SEC=150`（`valorant_broadcast.py:22`）→ 多回合合并切块 → 入点结构性 `coarse`

### 1.3 真实录像只读对照实验（已完成）

**产出**：`docs/reports/replay-vs-nextcombat-experiment-20260910.md`

**素材**：`D:\desktop\新建文件夹 (2)\新建文件夹\EDG夺冠回顾\` 下真实 EDG 录像 + sidecar（617s / 1169s）
**方法**：产品自带 `FrameProvider`(1fps) + `ValorantFrameClassifier(profile="broadcast")` + ffmpeg 定点抽帧**目视建立真值**。全程只读。

**三条结论**：

1. **`next_combat` 是碎片回合的唯一来源** —— 录像 `12-00-36` 9 回合中，3 个 19/23/26s 碎片 **100%** 由 `next_combat` 闭合；46–232s 正常回合全部由 `next_prep`/`broadcast_exclusion` 闭合
2. **碎片与回放确有因果**（3 个中 2 个已目视确证）—— `668.1–687.1`（19s）整段在回放块内（t=672、t=682 均见 "REPLAY" 水印）
3. **🔴 关键新发现：模型对"实战镜头回放"失效**

| 时段 | 目视真值 | 模型 | OCR `replay_segments` |
| :--- | :--- | :--- | :--- |
| 68–88s | 实时交战 | combat ✓ | 回放 ✗ **假阳性** |
| 311–317s | 回放（REPLAY 水印） | **replay ✓** | 漏检 ✗ |
| 344–354s | 回放（击杀配对叠加） | **combat ✗（`p_replay` 仅 0.001–0.041）** | 回放 ✓ |

→ 模型的 `replay` 类**只认"REPLAY 水印/转场 UI"，不认"回放中的实战镜头"**（像素与实时交战同构）；
→ 这比"阈值偏高"严重得多，**调阈值救不了**；主因是**训练集缺"回放中的实战镜头"样本**；
→ 反向：OCR 的 `replay_segments`（靠"计时器不可读"的间接证据）**抓到了模型漏掉的回放** → **两者互补，但都不可单独依赖**。

### 1.4 `broadcast_mode` 影子模式（已完成并验证）

| 项 | 内容 |
| :--- | :--- |
| 开关 | 环境变量 `LSC_VALORANT_BROADCAST_MODE_SHADOW=1`（`1/true/yes/on`），**默认关闭** |
| 生效条件 | 开关打开 **且** `source_profile == "broadcast"` |
| 行为 | 用**同一批 OCR 标签**并行喂一份 `broadcast_mode=True` 的 FSM，产出两份回合列表差异 |
| 记录 | `state["broadcast_mode_shadow"]`（本次摘要）、`state["broadcast_mode_shadow_totals"]`（累计）、`state["ocr_fsm_broadcast_shadow"]`（影子 FSM，供增量续扫）+ INFO 日志 |
| 摘要字段 | `primary_rounds` / `shadow_rounds` / `shadow_only` / `primary_only` / `resized` / `primary_next_combat` / `shadow_next_combat` |
| **不改变** | 生效回合列表、`round_key`、边界密扫、回放标注 |
| 源码 | `lsc/analyzer/valorant_ocr_rounds.py`（新增开关/比对函数/并行喂帧，`__all__` 增补 3 名） |
| 测试 | `tests/test_broadcast_mode_shadow.py`（**24 条**，含 AST 级源码守卫） |

**三重验证**：
- **行为中性（真实录像端到端）**：`09-49-28` 280–390s、`12-00-36` 660–700s 两次跑影子关/开 → 生效列表**完全一致**
- **机制有效（FSM 级）**：同一序列 → 生效 `[1.0,35.0] next_combat pending`（26s 碎片）vs 影子 `[1.0,50.0] next_prep vision_confirmed`（49s 完整回合）
- **源码守卫（AST）**：`broadcast_mode=True` 全模块**仅一处代码调用**；生效 `feed()` 不传该参数

**⚠️ 同时修正了此前的判断**：`broadcast_mode=True` **只抑制 COMBAT 态**的 fresh-clock 假切分（`:470-477`）；
**SETTLE 态**的合法 `next_combat` 闭合**仍保留**（`:550`/`:582`）。因此"关闭 next_combat 会加重回合合并"的副作用**被高估**。

### 1.5 `replay` 阈值复核（本轮新补，闭环 R4）

既有计划 `valorant-continuous-analysis-chain-optimization-20260908.md` §7.3 明确：**「Replay 暂定稳定阈值：0.77」+「阈值必须在独立校准集上选择，并在另一段视频上复核」**。此前的实测只覆盖**召回**，本轮补齐**精度**：

| 阈值 | replay 召回（val n=27） | **combat 误判为 replay**（val n=412） | combat 变 unknown |
| ---: | ---: | ---: | ---: |
| **0.77（现状）** | 85.2% | **0.0%** | 9.2% |
| **0.70（推荐）** | **100.0%** | **0.0%** | 9.2% |
| 0.65 | 100.0% | 0.0% | 9.2% |
| 0.60 | 100.0% | 0.2% | 9.0% |

→ **0.70 严格优于 0.77**（召回 85.2% → 100%，精度不变）；≤0.60 开始出现误判。
→ ⚠️ `val/replay` 仅 **27 帧**，样本偏小；`test/replay` 为 **0**，无法独立复核。
→ 🔴 **安全窗口比上表看起来窄得多（2026-09-10 独立复核补充）**：实测全量分布后，replay 侧
压在 0.77 以下的只有 4 帧 `{0.725, 0.732, 0.738, 0.752}`，combat 侧 `argmax=replay` 的
最大值是 `0.617`。即整个可行区间只有 **(0.617, 0.725]，宽约 0.11，由 5 帧钉住**。0.70 落在
区间内没错，但它是**当前模型权重**的产物——**B2 必须在 B1 重训之后重新推导，不可一次调完就固化**。

---

## 2. 未完成 / 已知缺陷

### A. 代码层（可直接动手）

| # | 待办 | 位置 | 风险 |
| :--- | :--- | :--- | :--- |
| A1 | 正式接线 `broadcast_mode=True` | `valorant_ocr_rounds.py:1455` | 中（会改变切片结果，需先取数） |
| A2 | 入点密扫引入**模型回放否决**：回放帧上的交战钟不得作为入点锚点 | `_refine_boundary_ts:659` | 中高（改变入点，需强回归守卫） |
| A3 | `precise` 需**交叉证据**（起点 ±2s 内视觉 combat 占比），不能只靠 `start_delta` 自洽 | `continuous_finalization.py:187-210`；**已存在的 `confidence < 0.8 → coarse` 门在 `:202`，目前是死门** | 中（影响导出门禁与人工复核量） |
| A4 | **用实测值填充** `start_confidence`（**不是删掉** 0.95——见下方陷阱说明） | `valorant_ocr_rounds.py:873` | 低（但它是 A3 的开关，须与 A3 合并做） |
| A5 | **消费** `replay_segments`（作"起点/终点不得落入"的排除区间）或删除该死数据 | `valorant_ocr_rounds.py:805` + 门禁/密扫 | 低 |
| A6 | 关键词表剔除 `"clutch"/"ace"/"triple"`，并新增"回放/replay/重播"正面识别 | `valorant_ocr_rounds.py:94-111` | 低 |
| A7 | 非切块候选允许"前导回放"时**前移起点**（而非整条拒绝） | `valorant_broadcast.py:702` | 中（可能引入重复回合） |

> **⚠️ A4 是陷阱，且它实际是 A3 的开关（2026-09-10 复核补充）**
>
> `continuous_finalization.py:202` **已经存在**一道 `confidence < 0.8 → coarse` 的精度门。
> 它是**死的**——因为 `start_confidence` 恒为 0.95：`valorant_ocr_rounds.py:873` 写的是
> `r["start_confidence"] = float(r.get("start_confidence", 0.95))`，即**兜底默认值**，全链路
> 无任何代码写入实测置信度。推论：**当前 `precise` ⟺ `boundary_refined` 且 `start_delta ≤ 3.0`，
> 不含任何独立精度证据**——这是 R3 的直接实锤。
>
> **但不能直接删掉 0.95**：同文件 `:906-911` 广播分支要求
> `start_confidence is not None` 才置 `boundary_refined=True`。删掉默认值 → 该字段变 `None`
> → **所有广播回合 `boundary_refined=False` → 全部降级 `coarse`**（大面积回归）。
>
> → 正确做法是**用实测值填充**；而一旦填了真值，`:202` 那道 0.8 门**自动复活**，
> 这正是 A3 想要的大部分效果。**故建议把 A4 并入 A3，并提前到 A2 之前做**（便宜，
> 且正好为 A2 提供它需要的回归守卫）。

### B. 模型 / 数据层

| # | 待办 | 说明 |
| :--- | :--- | :--- |
| B1 | **补"回放中的实战镜头"训练样本** | 当前 `replay` 类样本以"回放转场/水印"为主，缺实战镜头回放 → 这是模型失效的**根本成因** |
| B2 | 下调 `replay` 阈值 0.77 → **0.70** | 证据见 §1.5 |
| B3 | **补 `replay` 测试集** | `datasets/valorant_phase_broadcast`：train 1116 / val 27 / **test 0** → 训练流程无法回归 |
| B4 | 考虑把 **OCR 回放证据**作为模型的补充输入 | OCR 抓得到模型漏掉的实战镜头回放，两者互补 |

### C. 前置工作遗留的待决策缺陷（非本次引入）

| # | 缺陷 | 现状 | 影响 |
| :--- | :--- | :--- | :--- |
| C1 | `RoomOrchestrator.shutdown()` **不幂等** | 第二次调用抛 `TimeoutError`（`_thread` 停后未置 None）；幂等性原由**已删除的 Qt 门面**用 `_shut_down` 提供 | 生产调用点各一次（`main.py:332`、`server.py:565`、`start.py:76`），当前无实害 |
| C2 | `orchestrator._config_file_path()` **硬编码** `~/.lsc/LiveStreamClipper/rooms.json`，**不读 `LSC_DATA_DIR`** | 与 `persistence.py` 的 `data/rooms.json` 分裂成两套账本 | 任何开发/测试进程都会覆盖用户真实房间配置 |
| C3 | 仓库根目录脏文件 `NUL` / `NVIDIA Corporation/umdlogs` / **`%SystemDrive%/`**（内含 `ProgramData/Microsoft/Windows/Caches` 一棵树） | **未删除**（非代码，待确认；三者都出现在 `git status` 的 untracked 里） | 低（但会污染每次 `git status`） |

### D. 环境 / 偶发（非本次引入，已知）

| 项 | 说明 |
| :--- | :--- |
| `test_recording_asset_timeline.py::test_recording_asset_materializes_multiple_segments_and_cleans_temp` | **环境性失败，非"恒定失败"（2026-09-10 复核更正）**：单跑 `1 passed in 0.20s`，全量跑亦通过。仅当 `TMP/TEMP` 指向会触发 WorkBuddy safe-delete 的位置时才失败——按 §6 改用 ASCII `TMP/TEMP` 即消失，与代码逻辑无关 |
| `test_stability_guards.py::TestOrchestratorRoomsConcurrency::test_concurrent_add_and_list_rooms` | **子集运行必失败、全量运行通过**（编排器 `_MAX_PENDING_REQUESTS=8` 背压 vs 12 线程；已用 HEAD 版本对照确认为既有偶发） |
| 既有 ruff 问题 20 个 | 均在**他人未提交改动**的文件里（`room_handler.py` 8 个等），非本次引入 |

---

## 3. 接下来需要做的（按优先级）

### 第 0 步：提交落盘（**2026-09-10 复核新增，需在一切之前**）

**现状**：本次工作流的产物**一件都没提交**。`git status` = **11 untracked / 11 deleted / 60 modified**：

- **未跟踪**：三份文档（本文件 + `rootcause` + `replay-vs-nextcombat-experiment`）、`tests/test_broadcast_mode_shadow.py`、`docs/FEATURE-INVENTORY.md`
- **已删除**：`lsc/gui/`（8 个 `.py` / 1487 行）+ `lsc/cli.py`（283 行）= **1770 行**
- **已修改**：60 个文件（其中相当比例**不属于本工作流**）

**风险**：一次 `git checkout .` / `git reset --hard` / 工作区清理，§1「已完成」整节当场蒸发——**连本文件自己都保不住**。

**⚠️ 关键约束：本工作流无法按"文件"干净切分。** 同一工作区里缠着 **5 条**未提交工作流，且存在**行级交织**：

| # | 工作流 | 代表文件 |
| :--- | :--- | :--- |
| ① | **本次**（入点） | `lsc/gui`+`cli.py` 删除、影子模式、三份文档 |
| ② | 广播审计 / 帧缓存 | `lsc/analyzer/frame_provider.py`（新）、`valorant_broadcast.py`（重写 ~104 行）、HUD 宽 ROI 哨兵 |
| ③ | 剪映草稿终态门禁 | `lsc/exporter/jianying_draft.py`、`python-backend/continuous_finalization.py` |
| ④ | 前端工作台 | `lsc-electron/**`（18 文件） |
| ⑤ | 低延迟预览 | `docs/plans/low-latency-preview-*.md`、`scheme-c-*.md`（其自述与本工作无关） |

**行级混合证据（已实读 diff）**：

- `CHANGELOG.md` 的 v1.0.12 一节**同时**包含 ①②③④ 的条目；`CLAUDE.md` 同理——含「审计微步骤硬边界」「官方 HUD 宽 ROI 哨兵」「赛事假 next_prep 出点否决」「结算横幅保留」「剪映草稿终态门禁」等**均非本次**的条目
- `lsc/analyzer/valorant_ocr_rounds.py` 的 **19 个 hunk 里只有 6 个属于影子模式**，其余属于帧缓存（`frame_provider` 参数贯穿 `_refine_boundary_ts` / `refine_valorant_round_boundaries`）与 HUD ROI 哨兵

**建议切分**：

| 处理 | 文件 | 依据 |
| :--- | :--- | :--- |
| ✅ **可按文件干净提交** | `lsc/gui/*` + `lsc/cli.py`（删除）、`pyproject.toml`、`requirements.txt`、`python-backend/dependency_manager.py`、`README.md` | 已逐份读 diff 确认**纯属死代码清理**，无其他工作流内容 |
| ✅ **可按文件干净提交** | 三份新文档 + `docs/FEATURE-INVENTORY.md` | 全部为新增文件 |
| ⚠️ **需 `git add -p` 做 hunk 级切分** | `CHANGELOG.md`、`CLAUDE.md`、`lsc/analyzer/valorant_ocr_rounds.py` | 行级混合；或等 ②③ 先落盘后整体提交 |
| ❌ **不属于本工作流，勿打包** | `lsc-electron/**`、`frame_provider.py`、`jianying_draft.py`、`continuous_finalization.py` 等 | 见上表 ②③④⑤ |

**硬约束**：影子模式的**实现**（`valorant_ocr_rounds.py` 的 6 个 hunk）必须与 `tests/test_broadcast_mode_shadow.py` **同批落盘**——否则该提交点的测试是红的（测试引用的影子函数只存在于工作区）。

**验收**：本次产物在 `git status` 中清零；且**在每个提交点上** `pytest tests/test_broadcast_mode_shadow.py` 全绿。

#### ✅ 执行记录（2026-09-10 已执行）

| 提交 | 内容 | 规模 |
| :--- | :--- | :--- |
| `3821ffb` | `refactor: 删除 PySide6 遗留死代码` | 27 文件 / +304 −2143 |
| `05ba92d` | `docs: 工作流状态、根因分析与对照实验` | 4 文件 / +1129 |
| `0f86d48` | `feat: broadcast_mode 影子模式（切换前取数）` | 3 文件 / +423 |

**切分手法（供后续参考）**：`CHANGELOG.md` / `CLAUDE.md` 是单 hunk 或跨工作流混合，无法用
`git add -p` 直接切；改为**构造"中间版本"**——备份工作区版本 → 只写入本工作流相关内容 →
`git add` → 提交 → 从备份还原工作区版本。`valorant_ocr_rounds.py` 同理：从 20 个 hunk 中筛出
7 个影子 hunk（并剔除 hunk 17 内混入的 `round_key` 身份块），用"锚点插入"重建中间版本。

**验证**：
- 影子守卫 **24 passed**；且在 **`git worktree` 检出的 `0f86d48` 隔离副本**中同样 24 passed
  → 证明该提交点**自包含**（不依赖工作区里的 `frame_provider.py` 等未提交文件）
- `tests/test_valorant_ocr_rounds.py` 在该提交点只有 2 个**属于边界质量工作流**的新测试失败
  （预期，其实现未包含在本提交内），其余 32 个全过 → 切分未伤及既有测试
- 工作区全量 pytest **1791 passed / 0 failed**

**仍未提交（保留在工作区，等各自工作流落盘）**：`CHANGELOG.md` / `CLAUDE.md` 中其余 4 条
工作流的条目、`valorant_ocr_rounds.py` 的帧缓存与 HUD ROI 改动、以及 ②③④⑤ 的全部文件。

### 第 1 步：取数（**前置，零风险**）

在真实比赛场景开启 `LSC_VALORANT_BROADCAST_MODE_SHADOW=1` 跑 **1–2 场**，读累计统计判读：

| 观察 | 含义 | 决策 |
| :--- | :--- | :--- |
| `primary_next_combat` ≫ `shadow_next_combat` | 碎片被大量消除 | **支持切换**（A1） |
| `shadow_only` 偏多 | 影子额外产出回合 | **人工确认**是"被碎片掩盖的真实回合"还是"过度合并的伪回合" |
| `resized` 中 `shadow_sec` 普遍 > `primary_sec` | 生效路径在把回合切短 | **支持切换**（A1） |
| 无显著差异 | 该场景碎片少 | 维持现状，先做入点侧（A2/A3） |

**验收**：产出 1 份取数小结（场次、累计统计、判读结论、是否切换的建议）。

### 第 2 步：入点侧修复（**会改变切片结果，风险最高**）

| 顺序 | 任务 | 验收标准 |
| :--- | :--- | :--- |
| 2.1 | A5 + A6（低风险先做，不改入点） | `replay_segments` 有明确消费方或被删除；关键词表更新且不引入新回归 |
| 2.2 | A2（入点密扫回放否决） | 用已知含前置回放的真实录像核对：`start_quality="precise"` 的切片起点帧**不含**回放转场/水印；人工抽检 ≥10 条 |
| 2.3 | A3（`precise` 交叉证据） | 现有 `tests/test_continuous_finalization.py`、`test_valorant_broadcast.py` 全绿；新增"错入点不得评为 precise"的守卫测试 |
| 2.4 | A4（去硬编码） | `start_confidence` 反映实测视觉一致性 |
| 2.5 | A7（前导回放前移） | 无重复回合；`round_key` 去重仍生效 |

**顺序理由**：A5/A6 不改阈值不改入点，先做可积累回归信心；A2 依赖 A5 的排除区间；A3 在 A2 之后才有交叉证据可用。

### 第 3 步：模型 / 数据（**周期最长，可与第 2 步并行**）

| 任务 | 验收标准 |
| :--- | :--- |
| B2（阈值 0.77→0.70） | val 集：replay 召回 ≥95% 且 combat 误判 ≤1%；**并在另一段独立视频上复核**（计划 §7.3 的原始要求）；**且须在 B1 重训后重新推导**（§1.5 的安全窗口仅约 0.11 宽，绑死当前权重） |
| B3（补 test 集） | `test/replay` ≥ 30 帧；训练流程能产出 replay 类的回归指标 |
| B1（补实战镜头回放样本） | 新增样本集在 val 上把"实战镜头回放"的 `p_replay` 从当前的 0.001–0.041 提到可判别水平（建议以"过阈值率 ≥80%"为门禁） |

### 第 4 步：待决策项的收口

| 项 | 建议 |
| :--- | :--- |
| C1（shutdown 不幂等） | 加 3 行守卫（`_shutdown_done` 标志），或明确记录"仅单次调用"契约 |
| C2（房间账本路径分裂） | 让 `_config_file_path()` 优先读 `LSC_DATA_DIR`，与 `persistence.py` 合并为一套账本 |
| C3（脏文件） | 确认后删除（`NUL` 为 Windows 重定向误写；`NVIDIA Corporation` 为驱动日志目录） |

---

## 4. 过程中需要读的文档

### 4.1 先读（规范类，每次会话开始前）

| 文档 | 为什么读 |
| :--- | :--- |
| **`CLAUDE.md`** | **唯一权威参考**：架构约束、通信协议、安全防御、错误处理规范、目录结构 |
| `AGENTS.md` | 已迁移至 `CLAUDE.md`（保留兼容壳）；但**末尾「AI 协作工作规范」仍需遵守**（善用可视化、区分事实与猜测、最小改动、必须测试真实结果、保护数据） |
| `docs/CODING_STANDARD.md` | 编码规范（异常处理、日志、import 规范） |
| `CHANGELOG.md` | 本次改动已登记在 v1.0.12（含「清理死代码」与「broadcast_mode 影子模式」两节） |

### 4.2 本次工作产出（**核心，先读这三份**）

| 文档 | 内容 |
| :--- | :--- |
| `docs/reports/valorant-broadcast-inpoint-rootcause-20260910.md` | **根因分析**：两分支差异对照、入点证据链（7 步）、5 条根因（R1–R5，含文件:行号）、改进建议、验收标准。**开头有追加修正** |
| `docs/reports/replay-vs-nextcombat-experiment-20260910.md` | **对照实验**：`broadcast_mode` 决策数据、模型 vs OCR 回放识别能力对照、影子模式实施说明（§6）、取数判读规则 |
| **本文件** | 工作流状态、任务清单、阅读索引 |

### 4.3 关键源码（定位入点链路，建议按顺序读）

| 文件 | 关键位置 |
| :--- | :--- |
| `lsc/analyzer/valorant_ocr_rounds.py` | `OcrRoundFSM.feed()`（`:408`，`broadcast_mode` 参数 `:416`）；`_open_combat` `:608`；`_close` `:619`；`_is_combat_timer` `:646`；**`_refine_boundary_ts` `:659`（入点密扫，核心）**；`_annotate_replay` `:764`；`refine_valorant_round_boundaries` `:808`；`_summarize_broadcast_mode_shadow` `:924`；`detect_valorant_rounds_ocr` `:987`（标签判定 `:1355-1417`、FSM 喂帧 `:1455`、**影子块 `:1440` 起**） |
| `lsc/analyzer/valorant_broadcast.py` | 常量区 `:22-60`（`MAX_BROADCAST_ROUND_SEC` `:22`、`_TIMER_OCR_LABELS` `:60`）；`_stable_visual_label` `:63`；`_first_stable_exclusion` `:192`；`_stamp_broadcast_decision` `:490`；`_stable_combat_run_start` `:627`；**`_start_gate_decision` `:669`（入点门禁）**；`_expand_oversize_candidates` `:706`；`audit_broadcast_rounds` `:775` |
| `lsc/analyzer/valorant_frame_classifier.py` | 五分类模型包装：`_CLASS_NAMES` `:28`、broadcast 模型目录 `:36-39`、`predict_broadcast_batch` `:278`（融合 full 0.7 + top_HUD 0.3，top = 上 34% `:295`） |
| `lsc/analyzer/valorant_profile.py` | `pov`/`broadcast`/`auto` 档位解析与 `_BROADCAST_HINTS` |
| `python-backend/continuous_finalization.py` | **`classify_boundary_quality` `:116`（边界质量裁决）**；`_set_boundary_quality` `:285`；容差常量 `:16`（`DEFAULT_...=1.0`）/`:20`（`BROADCAST_...=3.0`） |
| `python-backend/handlers/room_handler.py` | `_set_boundary_quality` `:1529`、`boundary_review_required` `:1544`；broadcast 审计编排 `:7461` 起（**audit 在前、密扫在后**，`:7537-7540` 有顺序契约注释） |
| `lsc/analyzer/frame_provider.py` | `FrameProvider.get_frames` `:131`（1fps 抽帧缓存） |

### 4.4 既有计划（**避免重复造任务**）

| 文档 | 与本工作的关系 |
| :--- | :--- |
| `docs/plans/valorant-continuous-analysis-chain-optimization-20260908.md` | **最重要**。§7.1 输入策略（融合权重）、**§7.3 类别阈值**（"Replay 暂定 0.77"+"须在另一段视频上复核" = 本工作 §1.5 正是该复核）、§7.4 结构证据与"延迟定稿"原则、§8 数据集与训练优化 |
| `docs/plans/valorant-broadcast-runtime-accuracy-fix-tasks-20260908.md` | 456 行，T1–T6 任务（审计结果丢失、退出收尾闭环、粗扫调度、离线/生产口径统一、边界质量对账、回归与可观测性）。**与本工作的 A/B 项部分重叠**，动手前先读，避免重复 |
| `docs/plans/valorant-broadcast-phase1-architecture-and-ux.md` | 阶段一（架构非阻塞重构与实时体验） |
| `docs/plans/valorant-broadcast-phase2-dataset-and-vision-enhancement.md` | 阶段二（数据与特征引擎）——B1/B3 应挂到这里 |
| `docs/plans/valorant-stream-live-analysis-online-start-gate-20260908.md` | 在线入点门禁方案（与 A2 相关） |
| `docs/reports/valorant-broadcast-runtime-investigation-20260908.md` | 2026-09-08 现场调查：**R27 切入位于 REPLAY 转场之后、R135 起点仍在买枪/比分板画面**（本工作的现场佐证） |
| `docs/FEATURE-INVENTORY.md` | 功能清单与"存疑项"（含本次死代码清理的执行记录） |

### 4.5 相关规范

| 文档 | 关系 |
| :--- | :--- |
| `docs/spec-jianying-draft-export.md` | 剪映草稿权威校验（`recording_id + round_key + sidecar` 三重校验）——入点质量影响草稿门禁时需读 |
| `docs/spec-websocket-protocol.md` | `clip_queued` 等事件契约（若要向前端暴露影子统计需读） |
| `docs/plans/low-latency-preview-architecture-20260910.md` | 预览延迟架构（**与本工作无关，勿混**；内含"共享进样默认关闭"决策） |

---

## 5. 关键事实索引（速查）

| 事实 | 值 | 证据 |
| :--- | :--- | :--- |
| `broadcast_mode` 是否接线 | **否**（仅测试传入） | `valorant_ocr_rounds.py:416,1455`；`tests/test_valorant_ocr_rounds.py:159,183` |
| 入点精修窗口 | **±3s @5fps**，需连续 2 帧 | `valorant_ocr_rounds.py:53,55,56` |
| 交战钟判据 | `45 < timer ≤ max` | `_is_combat_timer:646`，`BUY_TIMER_MAX_SEC=45`（`:39`） |
| 入点 confidence | **硬编码 0.95** | `valorant_ocr_rounds.py:873` |
| broadcast `precise` 容差 | `start_delta ≤ 3.0` | `continuous_finalization.py:20,187-190` |
| 超长切块阈值 | `MAX_BROADCAST_ROUND_SEC = 150.0` | `valorant_broadcast.py:22` |
| 终段稳定帧数 | `EXCLUSION_STABLE_FRAMES = 4` | `:26` |
| replay 阈值 | `0.77`（建议改 **0.70**） | ⚠️ **同名不同文件**：实际生效的是广播档模型 `lsc/analyzer/models/valorant_phase_broadcast_finetune_v4_fused_20260907/valorant_phase_v1.json`；根目录那份 `lsc/analyzer/models/valorant_phase_v1.json`（POV/baseline）**没有** `class_stable_prob` 字段，grep 到它会误判。§1.5 实测 |
| 融合权重 | full 0.7 + top_HUD 0.3（top = 上 34%） | `valorant_frame_classifier.py:286,295` |
| 训练数据分布 | replay: train 1116 / val 27 / **test 0** | `datasets/valorant_phase_broadcast/*/` |
| 回归基线 | **1791 passed / 0 failed**（配合 §6 ASCII `TMP/TEMP`） | 全量 pytest（约 75s） |

---

## 6. 环境与复现注意事项（踩过的坑）

| 坑 | 解法 |
| :--- | :--- |
| **Windows 中文路径下 `cv2.imread` 静默失败返回 `None`** | 必须 `cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)` |
| **ffmpeg 是 Windows 原生程序**，Git-Bash 的 `/c/...` 路径它不认 | 传 `C:/...` 形式路径 |
| pytest 启动触发 WorkBuddy safe-delete | `TMP/TEMP=<ASCII 路径> pytest --basetemp=<每次唯一 ASCII 路径> -p no:cacheprovider` |
| 真实录像与 sidecar 时间轴 | **已校准**（目视 t=620 为回放，与 sidecar `replay_segments=[[615.067,629.067]]` 吻合） |
| 真实录像存放位置 | `D:\desktop\新建文件夹 (2)\新建文件夹\EDG夺冠回顾\`（含 `_至_` 命名录像 + `.analysis.json` sidecar） |
| 本地模型可直接实测验证假设 | `ValorantFrameClassifier(profile="broadcast")` + `datasets/valorant_phase_broadcast/` |
| 判定"未接线功能"的通用手法 | `grep -rn "<参数名>" --include=*.py .` → 非默认取值**只出现在 tests/** 即为未接线 |

---

## 7. 一句话交接

> **根因已定位、决策数据已备、影子模式已落地——但一件都没提交**。第一件事是 §3 第 0 步：
> 按可切分边界落盘（不要试图按文件一刀切，本工作流与另外 4 条在工作区行级交织）。
> 然后先在真实比赛开启 `LSC_VALORANT_BROADCAST_MODE_SHADOW=1` 取数，据此决定是否正式接线
> `broadcast_mode`（A1）；同时并行推进**入点侧修复**（A5→**A4+A3**→A2，风险最高，需强回归守卫）
> 与 **模型数据补齐**（B1/B2/B3，周期最长）。**入点精度的提升不能靠 `broadcast_mode`**，
> 必须走"入点密扫引入回放否决 + `precise` 需交叉证据"这条路线。
