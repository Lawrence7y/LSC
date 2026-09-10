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
| **已产出文档** | 4 份（根因分析、对照实验、本文件、**取数小结**） |
| **取数已完成** | ✅ 2026-09-10 实机跑 1 场（`huya/29701502`，1432s，46 次影子扫描）→ **零差异**（生效 10 回合 / 影子 10 回合 / `next_combat` 0:0） |
| **A1 决策** | ❌ **不接线** `broadcast_mode`——本段无 `next_combat` 闭合即无该类碎片，开关无事可做。详见小结 §3（附局限：未重现另一段录像的碎片形态） |
| **待动手（会改变切片结果）** | 入点侧引入模型回放否决（A2）；**A4 落点已更正为 `valorant_broadcast.py:519`**，与 A3 合并提前到 A2 之前 |
| **缺陷收口** | ✅ 已修：P1「收尾改名不幂等致重复副本」（`8ba9a67`）、sidecar 命名分裂（`ff0f542`）、C1/C2/C3 与影子开关白名单（`a56996f`）——均含对照实验/守卫测试 |
| **第 2 步进度** | ✅ **全部收口**：2.1（A5+A6，`268747e`）、2.2（A4+A3，`f455339`）、2.3（A2 收窄版，`08666b7`）已完成；2.4 A7 **取证后改判不做**（§2.2） |
| **回归基线（当前）** | 全量 pytest **1822 passed / 0 failed**（2026-09-10，须配合 §6 的 ASCII `TMP/TEMP`） |
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
| **R2** | 入点证据链 **100% 由 OCR 交战钟驱动**，模型回放标签对入点**零影响** | 🔴 设计缺口 | `start_confidence` **不是实测值**：`valorant_broadcast.py:519` 写二值代理 `0.95 if start_delta is not None else 0.70`（`valorant_ocr_rounds.py:873` 的 0.95 只是无人设置时的兜底）；`valorant_broadcast.py` 5 处改写 `start`（`:746/:760/:981/:1014/:1228`）无一处来自回放证据 |
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

> 状态（2026-09-10）：A1 ✅已决策不接线 / A2 ✅已完成（范围已收窄，见 §2.1） / A3+A4 ✅已完成（`f455339`） / A5+A6 ✅已完成（`268747e`） / **A7 ❌已评估·改判不做（依据见 §2.2）** → **第 2 步全部收口**

| # | 待办 | 位置 | 风险 |
| :--- | :--- | :--- | :--- |
| A1 | 正式接线 `broadcast_mode=True` | `valorant_ocr_rounds.py:1455` | ✅ **已决策不接线**（取数零差异，见小结 §3） |
| A2 | 入点**回放否决**（显式化 + 可审计） | `valorant_broadcast.py` 起始门禁 | ✅ 已完成（`08666b7`）：**纯附加**标注，不改入点；范围收窄依据见 §2.1 |
| A3 | `precise` 需**交叉证据**（起点视觉 combat 占比），不能只靠 `start_delta` 自洽 | `continuous_finalization.py:187-210`；`:202` 的 `confidence < 0.8 → coarse` 门**是活的但冗余**（见下方陷阱说明） | ✅ 已完成（`f455339`） |
| A4 | **用实测值替换二值代理**（`valorant_broadcast.py:519`）——**不是删掉兜底 0.95**（见下方陷阱说明） | `valorant_broadcast.py:519`（主）+ 起始门禁通过后覆写 | ✅ 已完成（`f455339`），窗口语义按实测校正为**前视** |
| A5 | **消费** `replay_segments`（作"终点不得伸进赛后回放块"的排除区间） | `valorant_ocr_rounds.py:805` + `room_handler._set_boundary_quality` | ✅ 已完成（`268747e`），实测裁掉 26.7s |
| A6 | 关键词表剔除 `"clutch"/"ace"/"triple"`，并新增"回放/replay/重播"**否决式**正面识别 | `valorant_ocr_rounds.py:94-111` | ✅ 已完成（`268747e`） |
| A7 | 非切块候选允许"前导回放"时**前移起点**（而非整条拒绝） | `valorant_broadcast.py` 起始门禁 | ❌ **已评估·改判不做**：机制不成立（窗口内无处可移），实测收益为 0 —— 依据见 §2.2 |

#### 2.1 A2 的范围收窄依据（2026-09-10 实施前核实）

任务书原文是「入点密扫引入**模型**回放否决：回放帧上的交战钟不得作为入点锚点」，落点
`_refine_boundary_ts`（OCR 层）。实施前核实发现**两处前提不成立**：

1. **OCR 层手里没有视觉模型**——"模型否决"在该层无法实现；
2. 移到审计层用视觉模型做，**又抓不到主要 case**：根因文档 §1.3 自己的结论是
   模型**分不清"回放中的实战镜头"与实时交战**（该类 `p_replay` 仅 0.001–0.041）。
   可达的只有"起点落在回放转场/水印/非游戏画面"这一部分，而**这部分现有入点门禁
   已经在做**（门禁要求起点处有稳定视觉 combat 游程，无则 `no_stable_combat` 拒绝）。
   "回放中的实战镜头"只能靠 OCR 的"计时器不可读"证据，但 `replay_segments` 是
   **赛后**的（受限于 `[result_ts, end]`），**无法回头指导入点**——要覆盖它必须新增
   **前置**回放检测，属 A7 范畴。

故 A2 按"显式化 + 可审计"落地（纯附加、零消费方风险）：

- 新增 `_start_window_replay_evidence()`：检测起点前视窗口内是否出现 `replay`/`non_game`；
- 既有拒绝路径上增加 `broadcast_start_gate_detail="replay_at_start"` + INFO 日志，
  **拒绝链路与既有 reason 串不变**，只增加成因可统计性。

**与 A4 的协同**：起点含回放的候选实测视觉占比为 **0.0**，按 A4 写入的
`start_confidence` 在 `confidence < 0.8 → coarse` 门下**结构性不可能**评为 `precise`
——即使门禁未跑也不会误放行。

**真实录像抽检**（跨 10 场录像取样 **15 条**起点，超出验收要求的 ≥10 条）：
14/15 起点干净（占比 1.0、无回放）；1/15 起点落在回放里（`01-19-14` @225.2s，样本
`replay,replay,replay`，占比 0.0）；**违规「含回放却评为 precise」= 0 条**。


> **⚠️ A4 的落点是 `valorant_broadcast.py:519`，它仍是 A3 的开关（2026-09-10 实机取数修正）**
>
> 实机取数（真实赛事直播，见 `docs/reports/valorant-broadcast-shadow-datacollection-20260910.md` §4.2）
> 取到 `start_confidence=0.7`，**推翻本文档早先"恒为 0.95 / 全链路无人写入"的判断**。真实机制：
>
> ```python
> # lsc/analyzer/valorant_broadcast.py:519
> item["start_confidence"] = 0.95 if item["start_delta"] is not None else 0.70
> ```
>
> `valorant_ocr_rounds.py:873` 的 `r.get("start_confidence", 0.95)` **只是无人设置时的兜底**，
> 并非主写入点。因此：
>
> - `start_confidence` 是**二值代理**（有没有 delta），**不是实测视觉置信度**；
> - `continuous_finalization.py:202` 的 `confidence < 0.8 → coarse` 门**是活的**——实测已触发
>   （0.70 < 0.8 → `start_quality=coarse`）；
> - 但该门**冗余**：`conf ≥ 0.8` ⟺ `start_delta is not None`，而广播档 `boundary_refined`
>   本就要求 `start_delta is not None` → **永远提供不了 `boundary_refined` 之外的证据**。
>   **R3 结论（`precise` 无独立精度证据）依然成立。**
>
> **陷阱不变**：不要删 `valorant_ocr_rounds.py:873` 的兜底——同文件 `:906-911` 广播分支要求
> `start_confidence is not None` 才算 `boundary_refined`，删掉会让**所有广播回合降级 `coarse`**。
>
> → 正确做法：把 `:519` 的二值代理换成**实测值**（如起点 ±2s 内视觉 combat 占比）。一旦有真值，
> `:202` 那道门才真正具备判别力——这正是 A3 想要的效果。**故把 A4 并入 A3，并提前到 A2 之前做**
> （便宜，且正好为 A2 提供它需要的回归守卫）。

#### 2.2 A7 的评估结论：机制不成立，改判不做（2026-09-10 取证）

A7 原文主张「非切块候选允许"前导回放"时**前移起点**，而非整条拒绝」，理由是根因
文档的推断——"只能在**接受错入点**与**丢整回合**间二选一"。实施时与另一条工作流
**新加的、尚未提交的契约**冲突（3 个测试明确断言"前导回放的普通候选必须拒绝"，
理由写在 docstring：*回放开头属于上一回合尾段，后续 OCR 候选会覆盖该回合*）。
两边对"回合到底丢没丢"判断相反，故先取证再定。

**取证方法**：跨 3 场录像，从 `*.finalization.json` 的
`accepted/rejected/pending_candidates` 桶取出**全部 11 条被起点门禁拒绝**的候选
（去重），核对两件事：①是否有别的候选覆盖其跨度；②其跨度内容是否含真实交战。

**结果**：

| 类别 | 条数 | 跨度内 combat 占比 | 判定 |
| :--- | ---: | :--- | :--- |
| 跨度是**非游戏/回放**内容（`non_game`/`replay` 为主） | **9** | **全部 0%** | ✅ 拒绝正当 |
| 跨度里**确有交战** | **2** | **65% / 80%** | ❌ 误拒，回合真丢 |
| 被后续已接受候选覆盖 | **1/11** | — | 仅 1 条 |

→ 根因文档的"会丢整回合"**成立**；另一条线的"多数拒绝正当"**也成立**——两边各看到一部分。

**但 A7 的实现方式救不了那 2 条**（决定性证据）：对两条真误拒候选做起点后 20s 逐秒标签：

```
#1 [80.6, 199.3]（跨度 65% 交战）
   non_game,replay,non_game×3,unknown,replay×9,non_game×5   ← 前 20s 无 combat
   稳定 combat 游程起点: None          （门禁窗口内无处可前移）
#2 [685.6, 817.0]（跨度 80% 交战）
   replay,replay,non_game×3,unknown,non_game,replay×3,non_game×4,unknown×2,buy,
   combat,combat,combat                ← 交战在第 17s 才开始
   稳定 combat 游程起点: 702.6        （需前移 17.0s，已超出 15s 窗口）
```

两条的**前导非交战段都长于 `START_GATE_SCAN_LIMIT_SEC = 15.0`**，即窗口内根本
没有 combat 可移 → **A7 按原文实现，观测收益为 0**。

**真正的成因是门禁窗口太窄**，而代码对此有明确取舍注释：
*"普通候选不做 15s→35s 扩展：起点不是真实 combat 就当场拒绝，省掉额外抽帧/推理
（缓解解说流滞后）"*。该取舍有实测支撑——broadcast 档分析滞后一度达 **178s**
（见小结 §4.4），扩窗会加重。

**判定：不做 A7。** 理由与代价：

- A7 原机制收益为 0，做了等于白改；
- 实质替代方案是"**条件扩窗**"（仅当候选跨度含交战时把 15s 扩到 35s），
  确实能救回那 2 类回合，但"跨度是否含交战"本身也要抽帧（约 20 帧/候选），
  除非复用审计已抽的尾部 lookahead 帧——在滞后仍是主要痛点的当下不划算；
- 残留代价显式记录：**11 条拒绝中 2 条误拒（≈18%）**，按"控制分析滞后"这一
  更高优先级目标接受。
- **副产品**：本结论与另一条工作流的契约方向一致 → **无需改动他们那 3 个测试**，
  冲突自然化解。

**什么情况下应重开此判**：若滞后问题被解决（例如帧缓存线落地后抽帧成本大降），
"条件扩窗"的性价比将反转，届时按上表信号（跨度 combat 占比作判别器）实现即可。

### B. 模型 / 数据层

> 状态（2026-09-10）：**B3 已完成（test/replay 32 帧，全部人工确认）**；**B1 样本已就绪**（72 帧水印确证）（客观真值，非猜测）；
> **B2 被实测推翻**（见附注 ③，不可单独执行）；**B5 已修**（融合稀释回放信号，`ae3ba71`）；
> B4 与 B1 可合并考虑。

| # | 待办 | 说明 |
| :--- | :--- | :--- |
| B1 | **补"回放中的实战镜头"训练样本**（已改为"纠正 455 帧标签后重训"，runbook 见 §4.2） | 已在 72 帧水印确证集上证实：模型 `replay` 召回 **0.0%**，八成判成 `non_game` → 重训是唯一真解（见附注 ②） |
| B2 | 下调 `replay` 阈值 0.77 → **0.70** | ❌ **实测推翻**：在真实回放（72 帧确证集）上 0.70 仅召回 8.3%（§1.5 的 val 集不能代表真实转播）。**须在 B1 重训后于确证集上重新推导**（见附注 ③） |
| B3 | **补 `replay` 测试集** | ✅ **已完成**：`test/replay` **32 帧**（全部人工确认，按录像切分）+ 回归指标 **34.4%**（`manifest_replay_verified_20260910.jsonl`，见附注①） |
| B4 | 考虑把 **OCR 回放证据**作为模型的补充输入 | OCR 抓得到模型漏掉的实战镜头回放，两者互补 |
| **B5** | ✅ **已修** `replay` 类的融合稀释（`ae3ba71`） | `top_HUD 0.3` 看不到底部回放水印，把整帧 0.96–0.99 的置信度拉到阈值下。改为对 `replay` 用整帧判定（或取 `max`／换底部水印带）→ 同一批 72 帧 ≥0.77 的检出从 **0/72 升到 12/72**。**增益项，不能替代 B1**（见附注 ④） |

#### 🔴 B 附注重大发现：模型的"回放盲"主要是**标签训练出来的**（2026-09-10 全量扫描）

按操作者确认的两条约定——① **回放都会有 REPLAY 字样**；② 此前把"非游戏画面的 replay"
归到了 `non_game`——对数据集做了全量水印扫描（2512 帧，整帧 OCR）：

| 结果 | 数量 |
| :--- | ---: |
| 含字面 `REPLAY` 水印但**当前标签不是 replay** 的帧 | **455**（`non_game` 454 + `combat` 1；train 452 / val 3） |
| 命中置信度 | **全部 ≥0.99**（中位 0.992） |
| 其中文件名自带 `replay_boost` / `rarex` 的 | **360 / 375** |

即 **`train/non_game` 的 1061 帧里有 451 帧（约 43%）其实带 REPLAY 水印**。

**已改标签**（可一键回滚，回滚单 `relabel_rollback.jsonl`）：455 帧搬入 `replay`；
并同步 manifest 的 `frame_path` 与 `label` 字段（复核：文件存在但 label 与目录不符 = **0**）。

| dir | 改前 | 改后 |
| :--- | ---: | ---: |
| `train/non_game` | 1061 | **610** |
| `train/replay` | 1135 | **1587** |
| `val/non_game` / `val/replay` | 170 / 27 | **170 / 30** |

**这重写了 B1 的因果**：此前（根因文档 §1.3 与 B1 条目）的结论是"模型分不清回放，
因为**训练集缺**'回放中的实战镜头'样本"。但实测显示——**训练集里本来就有 454 帧带 REPLAY
水印的样本，只是被标成了 `non_game`**。模型把水印确证回放判成 `non_game`（B3 测试集上
16/32、早期 49/72），恰恰是**它学对了标签**。

→ 因此：
- **B1 的方向得到强化，且可能比预期便宜**——不是"从零补一类样本"，而是"**纠正已有 454 帧的
  标签后重训**"；重训后应能立刻看出 `replay` 召回的变化；
- **B2（调阈值）在此之前毫无意义**（阈值救不了被错误监督的目标）；
- 早期"`replay` 召回 0%"的表述应改述为"在**被错误标注**的训练集下，模型学不到该类"；
- ⚠️ **须留档的疑问**：360/455 是当初**刻意挖出**的 `replay_boost`/`rarex` 样本。若当初的
  意图是"回放内容应归入非游戏以便排除"，则本次改标签是**语义变更**而非纠错。操作者已确认
  按"依赖文字"的约定处理，此处仅作记录。

#### ⚠️ B 附注补记：入集已**回滚**——OCR 水印依据被操作者质疑（2026-09-10）

本轮曾把 72 帧"水印确证"数据入集（`train/replay` +21、`test/replay` +29），
**随后按操作者判断整套回滚**，数据集已精确还原为原始规模
（`train/replay` 1116 / `test/replay` 0）；manifest 改名
`manifest_replay_verified_20260910.jsonl.INVALID_rollback` 保留证据、不被任何流程读取。

**质疑点**：操作者指出「若需依靠截图中的文字 **REFLASH** 来确认，则数据集需重新标注」。
我的依据字符串经核对**确实是 `REPLAY`**（72 帧全部；单帧证据：右下角固定位置
`(0.895, 0.936)`、OCR 置信度 **0.985**，同帧另有 `EDGnobody/TEheybay/TEKai/EDG CHICHOO`
等多名选手叠加与 `SPIKE CARRIER KILLED` 击杀提示，符合高光回放版面）。
但**本轮无法看图**（`mimo_vision` 报 unknown tool、`read_image` 报当前模型
`deepseek-flash` 不声明图像输入），**能否把屏幕上真实渲染的字形判成 `REPLAY` 还是
`REFLASH`，我这一侧只有 OCR 一个通道、无法裁决**。在依据未获确认前，不出未确证的
测试集/训练样本。

**✅ 结论（操作者看图确认）：屏幕上确实就是 `REPLAY`** → 挖掘依据成立，已**重新入集**：

- 修掉 `cv2.imwrite` 中文路径静默失败（改 `cv2.imencode(...).tofile(...)`），
  并在脚本内**断言 manifest 每条都有对应文件**（上次正是缺这一步，产生 34 条幽灵条目）；
- 内容 sha 去重后 71 条人工确认 → **51 帧唯一**；切分改为**按录像**（避免同段相邻帧
  跨集泄漏）：**test = `12-00-36`(21) + `02-06-12`(10) = 32 帧**（全部人工确认，
  满足 B3 的 ≥30）、**train = `09-06-29`(19)**；
- manifest：`scripts/valorant_vision/manifest_replay_verified_20260910.jsonl`（51 条，
  零缺失），每帧带 `watermark_verified/human_confirmed/model_pred` 溯源。
- **B3 回归指标（B5 已生效下测得，32 帧全为水印确证回放）**：
  **replay 召回 34.4%**（11/32 @0.77）；模型判定 `non_game` 16 / `replay` 16；
  `p_replay` 均值 0.433、中位 0.347、最大 0.9998；阈值 0.70→37.5%、0.60→40.6%、0.50→46.9%。
  即**即使修好融合，仍有约 2/3 的明确回放难例漏检** → B1 重训的必要性不变。

补充核对（全 72 帧 OCR 扫描）：含 `REPLAY` 的行 **72 处**（取值全部就是字符串 `REPLAY`），含 `FLASH`/`REFLASH` 的行 **0 处**。即**我这一侧的通道里不存在 `REFLASH`**——但 OCR 不能代替看图，屏幕上真实渲染的字形仍需操作者裁决。

**连带影响（须一并重新确认）**：附注 ①～⑤ 中**基于该 72 帧集**的结论都要打问号——
包括"replay 召回 0.0%"、B2 的"0.70 → 8.3%"、以及 B5 在 72 帧上的"0/72 → 12/72"。
**不受影响**的是 B5 的 val 集验证（replay 85.2→100%、其余四类零回归），该验证不依赖
这批帧；故 B5 本身的正当性仍成立，只是它在"真实回放"上的收益幅度待重新测量。

**本轮新踩的坑（值得记）**：`cv2.imwrite` 在**中文路径**下**静默失败**（与 §6 记的
`cv2.imread` 同一问题），导致 manifest 出现 34 条"有记录、无文件"的幽灵条目。
入集一律改用 `cv2.imencode(...).tofile(...)` 或先 `np.fromfile` 式读写。

#### B 附注：B1/B3 已产出**水印确证**数据，并推翻 §1.5 的 B2 结论（2026-09-10 实测）

**① 用仓库自带 OCR 建立客观真值**（绕开"视觉模型不可用"）：回放水印是**文字**，故对
22 段 OCR 回放段按秒/半秒密抽帧（扫描 301 → 扩样约 600 帧），用 `ocr_detector` 逐帧搜
`REPLAY/回放/重播/慢动作`，**只保留 OCR 真读到字面水印的帧** → **72 帧水印确证回放帧**（3 场录像：`09-06-29` 29 帧 / `12-00-36` 33 帧 / `02-06-12` 10 帧）
（满足 B3 对 `test/replay` ≥30 帧的要求；每帧都有水印文字背书，零猜测）。
清单：`C:/lsc_tmp/verify/verified/verified_manifest.json`（含会话/时间戳/所属段/原录像路径/
OCR 原文/模型预测）。

**② 模型在这些"铁证回放"上的表现——`replay` 召回 0.0%**：

| 阈值 | 判为 `replay` 的帧 |
| ---: | ---: |
| **0.77（现状）** | **0 / 72（0.0%）** |
| 0.70（B2 原推荐） | 6 / 72（**8.3%**） |
| 0.60 | 10 / 72（13.9%） |

模型分布：`non_game` 49 / `unknown` 23 / **`replay` 0**。即**面对明确写着 REPLAY 的画面，
模型一次都没认出来**，八成判成"非游戏"。→ **B1（补样本重训）是唯一真解**。

**③ ⚠️ 推翻 §1.5 的 B2 结论**：§1.5 在 **val 集**（27 帧，以"回放转场/水印"为主）上测得
"0.70 → 召回 100%"，据此把阈值下调列为 B2。但**在真实赛事回放上 0.70 只有 8.3%**——
**val 集不能代表真实转播回放**。→ **B2 不可单独执行**，必须在 B1 重训后于**本附注的
72 帧确证集**上重新推导（该集正可作为 §7.3 要求的"另一段视频复核"载体）。

**④ 🔴 新发现（可操作）：`top_HUD` 融合在稀释回放信号**。
广播档融合为 `full 0.7 + top_HUD 0.3`，而**回放水印在画面底部**，top 34% 裁剪看不到它，
0.3 权重把整帧的高置信度硬拉到阈值以下：

| 同一批 72 帧 | p_replay 均值 | 最大 | ≥0.77 |
| :--- | ---: | ---: | ---: |
| 融合（现行） | 0.167 | 0.750 | **0 / 72** |
| 仅整帧 | 0.228 | **0.9998** | **12 / 72** |

例：`02-06-12_473750` 融合 0.720 → 仅整帧 **0.998**；`02-06-12_472250` 0.679 → **0.962**。
→ **已修（B5，`ae3ba71`）**：新增 `_FUSION_BYPASS_CLASSES=("replay",)`——豁免类不参与顶部
融合、直接用整帧概率，并做**条件归一化**（保持豁免类取值、缩其余类）。不可退回
"整行除以总和"（会把刚抬起的 replay 压回阈值下，0.77 下检出 12/72→6/72，已加源码守卫）。
**验证（同权重、仅改融合）**：行和精确=1；72 帧确证集 @0.77 **0/72 → 12/72**；
val 逐类召回**零回归且全线小涨**（replay 85.2→**100.0%**，non_game/buy/combat/result
各 +0.5～3.0pp）。仍不能替代 B1（多数水印确证帧被判 `non_game`、`p_replay<0.02`）。
⚠️ **生效前提**：B5 改的是广播档融合路径（`predict_broadcast_batch` + 该模型的
`broadcast_input_fusion: 0.7/0.3` 元数据），而**广播档接线（`profile="broadcast"`、
`_DEFAULT_BROADCAST_MODEL_DIR`）本身属另一条工作流的未提交改动**——HEAD 的
`audit_broadcast_rounds` 仍构造 `ValorantFrameClassifier()`（POV 基线模型）。
故 B5 的收益要等广播档接线落盘后才在生产链路体现（提交点自洽性不受影响）。

**⑤ 顺带证实 OCR 回放启发式的误报率很高**：22 段 OCR 回放段里**只有 3 段**出现过字面
`REPLAY` 水印；其余扫描帧的 OCR 文本多是 `HALFTIME`+`00:00:47`+`6-6`（中场）、
`CURRENT:X | NEXT:Y | ROUND N`（地图 veto/记分板）、`BUY PHASE` 等**非回放内容**
——因为它们同样"计时器不可读"。
→ 这解释了 §1.3 那个"目视确证 REPLAY 水印"的 @620s 帧为何在本次 OCR 下**读不到 REPLAY**；
→ 也意味着 **A5 实际消费的是"赛后非游戏内容（回放/中场/记分板）"而非严格意义的回放**
——修剪方向仍然正确（这些内容本就不该进片尾），但命名应更准确。

**⑥ 入集方式**（待人工抽检确认后执行）：帧写入
`datasets/valorant_phase_broadcast/test/replay/`（B3）与 `train/replay/`（B1），并补
`manifest_*.jsonl` 条目（字段契约见 `scripts/valorant_vision/manifest_broadcast.jsonl`）。
`datasets/valorant_phase_broadcast*/` 已在 `.gitignore:201`，属本地数据、不进仓库。

#### B1 重训**已执行**：首要判据达标，但整体是**负收益** → B1 暂挂，转"改输入契约"（2026-09-10）

完整报告：`docs/reports/valorant-broadcast-b1-retrain-result-20260910.md`。

跑通四次 10-epoch 微调（baseline / 不换标签对照 / `hard_weight=1` / `hard_weight=4`），
官方 `broadcast_runtime` 口径终表：

| 模型 | split | Macro F1 | replay 召回 | replay 精确 | combat 召回 |
| :--- | :--- | ---: | ---: | ---: | ---: |
| baseline | val | **0.8935** | 0.9000 | **1.0000** | **0.9490** |
| 对照（**不换标签**，同配方） | val | 0.8838 | 0.9000 | 0.9643 | 0.9612 |
| `hard_weight=1` | val | 0.7821 | 0.9000 | 0.5000 | 0.7937 |
| `hard_weight=4` | val | 0.7771 | 0.9000 | 0.4426 | 0.7864 |
| baseline | test | 0.5336 | 0.3438 | 1.0000 | 0.3125 |
| 对照（不换标签） | test | 0.5491 | 0.4062 | 1.0000 | 0.3125 |
| `hard_weight=1` | test | 0.5340 | **0.6250** | 0.8333 | 0.1875 |
| `hard_weight=4` | test | 0.5158 | **0.6250** | 0.8333 | 0.1875 |

**① B1 的首要判据（"`test` replay 召回显著上升"）确实达标**：0.3438 → **0.6250（+28.1pp）**。
其中**纯微调**贡献 0.3438→0.4062，**标签纠正**净增 0.4062→0.6250。→ 说明"标签确实写错了"
这一判断是对的，纠正方向本身有效。

**② 但代价不可接受，且五项帧级门禁无一通过**：`val` Macro F1 **−0.1114**、
`combat` 召回 **−0.1553**（391/412 → 324/412）、`replay` 精确率 **−0.5000**；
`val` 的 `replay` 召回三行**都是 0.9000**、`non_game` 召回**都是 0.8882**——
即纠正**完全没有**提升模型在 `val` 上的回放识别，只是把 34 个非回放帧（31 个 `combat` +
2 个 `buy` + 1 个 `non_game`）判成了 `replay`。

**③ 单变量对照锁死归因——不是配方，是"标签纠正 + 输入契约"**：
- 对照（455 帧**放回** `non_game`，其余完全同配方/同权重/同关蒸馏）**≈ baseline**
  （0.8838 vs 0.8935）→ **训练链路与超参没问题**（onnx2torch 往返亦已验证：argmax 32/32 一致、
  概率行和恰为 1）；
- `hard_weight` 1 与 4 **几乎无差** → 权重倍数不是杠杆（此前"**每源帧** 6.27 倍 / **42.84%**
  train CE 权重压在 32 张唯一图"的直觉**被实测否掉**）

**④ 根因：标记在模型输入尺度下几乎不可见，模型只能改抓画面内容**。
OCR 几何实测（1920×1080）——**存在两种不同风格的标记**：

| 帧组 | REPLAY 位置 | 原字高 | 距底 | 224×224 输入下 |
| :--- | :--- | ---: | ---: | :--- |
| 被纠正的 455 帧 | **右上角** x∈[1688,1859] y∈[22,61] | 38.6px | 1019px | **≈20×8 px** |
| `val/replay`（原有，同风格） | 右上角，同一位置 | 38.6px | 1019px | ≈20×8 px |
| `test/replay`（水印确证，另一风格） | **右下角** y∈[977,1044] | 67.5px | 35.7px | ≈21×14 px |

标记在右上角 ROI 里**可测**（边缘能量 70.43 vs 无标记帧 23.03），但在**整帧**里只占
**8%**（18.86 vs 17.37）——被画面稀释。叠加"只有 **32 个唯一带标源帧**"（train），
模型改抓**内容/会话近路**：把"长得像那 35 个源帧"的**交战画面**判成 `replay`。

⚠️ **这同时修正 B5 的前提陈述**：B5 的"回放水印位于画面**底部**、顶部 34% 裁剪看不到"
**只对 `test/replay` 那套广播风格成立**；训练/验证集的标记在右上角（y∈[22,61]），
**落在顶部 34% 裁剪之内**。B5 的豁免逻辑本身仍正确（对底部风格是必要的），但它的
事实依据需要按"两种风格"重述。

**⑤ B2（阈值重推导）救不回来——已实测**：把候选的 `replay` 门从 0.77 扫到 0.98，
`val` Macro F1 只从 0.7821 回到 **0.8274**（仍远低于 0.8935），
**`combat` 召回一直是 0.79 不动**（0.7937→0.7985）——因为门只把低置信 `replay` 降级成
`unknown`，而 `unknown` 在召回里**同样算错**；同时 `test` 真实回放召回反而掉到 0.5312。
→ **B2 的隐含前提"模型已经会了、只是阈值不对"在本例不成立**，挂起到输入契约改造之后。

**⑥ 数据集真实结构（此前未记录，是权重口径的关键）**：`rarex<k>_`/`replay_boost<k>_`/`hardx<k>_`
是**增强/过采样副本**标记（生成方 `build_broadcast_hard_dataset.py`；判定方
`rebuild_source_separated_datasets.py:38`），**不是内容证据**——`train/non_game` 里也有
216 帧带 `replay_boost`。剥掉后：`train/replay` **1587 帧 = 158 唯一源（10.04×）**、
`train/result` 89/25（3.56×）、`train/non_game` 610/314（1.94×）；
**`val`/`test` 全部 1.00×**（无重复污染，评估口径干净）；被纠正的 455 帧 =
**35 个唯一源**（train 32 + val 3，其中 32 个基名组是**整组**被移动的）。
→ 权重口径必须按**唯一源帧**算；`build_retrain_manifests.py` 已同时报告
`ce_weight_share_of_relabeled` / `relabel_unique_sources` / `train_unique_sources`。

**⑦ 建议**（详见报告 §6）：
- **首选**：把"右上角 + 右下角标记 ROI"按**原生分辨率**加成**独立输入支路**参与融合
  （现有 `predict_broadcast_batch` 已有双路融合骨架 + B5 豁免，是天然挂点）——即 **B4 的具体形态**：
  让标记从"整帧 8% 的边缘能量"变成"整幅输入的 100%"；
- **备选**：生产上**不依赖模型**判回放——OCR 对两种风格都已实测可读（72/72、≥0.99），
  "标记存在 + 落在两个已知位置区间"本身就是**确定性判据**，可直接供 A2 的回放否决与 A5 消费；
- ❌ **不要**把这三个候选挂到 `_DEFAULT_BROADCAST_MODEL_DIR`（门禁全不过，`combat` 退化会伤入点）。

**⑧ 两个会让重训静默走偏的坑**（已修，`train_onnx_finetune.py`）：导出元数据会**丢掉**
教师的 `broadcast_input_fusion` / `class_stable_prob`（→ 候选静默退回"无融合+默认阈值"，
与基线**不可比**）；`--new-manifest` 会给被纠正帧 `distill_weight=0.5`，而教师在这些帧上
**454/455 判 `non_game`、平均 p(non_game)=0.983**（→ KL 项把纠正**反向拉回**）。
已加 `_inherit_runtime_meta()` + `--hard-manifest`（`hard_distill_weight=0.0`），
并把教师预测固化进清单作为可审计证据。

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
| 2.1 | ✅ A5 + A6（低风险先做，不改入点） | `replay_segments` 有明确消费方（终点排除，实测裁掉 26.7s）；关键词表更新且不引入新回归 — **已完成 `268747e`** |
| 2.2 | ✅ **A4 + A3**（把 `valorant_broadcast.py:519` 的二值代理换成实测信心，同时激活 `:202` 的判断力） | 现有 `tests/test_continuous_finalization.py`、`test_valorant_broadcast.py` 全绿；新增"错入点不得评为 precise"的守卫测试；广播回合**不得**因 `start_confidence` 缺失而批量降级 `coarse` — **已完成 `f455339`** |
| 2.3 | ✅ A2（入点回放否决），**范围收窄**为"显式化 + 可审计 + 抽检" | 真机抽检 15 条起点，「含回放却评为 precise」= 0 条 — **已完成 `08666b7`**，依据见 §2.1 |
| 2.4 | ❌ **A7 改判不做**（前导回放前移） | 取证结论：A7 机制在观测数据上**收益为 0**（两条真误拒的前导段都长于 15s 门禁窗口，窗口内无处可前移）；残留 2/11 误拒按"控制分析滞后"接受。依据与重开条件见 §2.2 |

**A4 窗口语义的实测校正（重要）**：任务书写的是"起点 **±2s** 内视觉 combat 占比"，
但真实录像 7 个回合实测显示**对称窗口是错的**——健康入点的样本本就形如
`buy,unknown,combat,combat`（起点前理应是购买阶段），对称窗口会把 6/7 个正常回合压到
<0.8 而批量降级。改为**前视 `[start, start+2s]`** 后 5/7 达标，低分的两个恰是真正可疑的：
`01-19-14` 类起点落在回放里的、以及起点处于 `combat,result,unknown` 过渡态的。
缺样本时**不写值**（保留兜底），以满足"不得批量降级"的验收。

**顺序理由**（2026-09-10 实机取数后调整）：

- A5/A6 不碰阈值不碰入点，先做可积累回归信心；
- **A4 提前到 A2 之前**：它只把 `:519` 的二值代理换成实测值，是**便宜的一步**，而一旦有真值，
  `:202` 那道已存在的门才真正有判别力——**等于用最小代价先拿到 A3 的护栏**，正好为随后改动入点
  的 A2 提供回归守卫（原顺序把 A3/A4 排在 A2 之后，反而让最冒险的一步裸奔）；
- A2 依赖 A5 的排除区间；A3 与 A4 合并做（二者是同一件事的两端）。

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

### 4.2 本次工作产出（**核心，先读这四份**）

| 文档 | 内容 |
| :--- | :--- |
| `docs/reports/valorant-broadcast-inpoint-rootcause-20260910.md` | **根因分析**：两分支差异对照、入点证据链（7 步）、5 条根因（R1–R5，含文件:行号）、改进建议、验收标准。**开头有追加修正** |
| `docs/reports/replay-vs-nextcombat-experiment-20260910.md` | **对照实验**：`broadcast_mode` 决策数据、模型 vs OCR 回放识别能力对照、影子模式实施说明（§6）、取数判读规则 |
| `docs/reports/valorant-broadcast-shadow-datacollection-20260910.md` | **取数小结**（§3 第 1 步的交付物）：实机 1 场 / 46 次扫描的累计统计、**A1 不接线**的判据与局限；含 3 项副发现（白名单缺陷、`start_confidence` 二值代理、**收尾改名不幂等致副本**） |
| `docs/reports/valorant-broadcast-b1-retrain-runbook-20260910.md` | **B1 重训 runbook**：可执行命令（微调/复核/晋级）、基线取值（val 4/5 门禁不合格、test replay 召回 0.3438）、判读标准与三项已知阻塞（来源会话不足 3、缺回合级报告、帧级门禁未达标） |
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
| 入点 confidence | **二值代理 `0.95` / `0.70`**（**不是实测值**：有 delta→0.95，无 delta→0.70） | 主写入 `valorant_broadcast.py:519`；`valorant_ocr_rounds.py:873` 为兜底默认 |
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

> **根因已定位、影子模式已落地、代码侧任务已收口**。交付按 §3 第 0 步的可切分边界落盘
> （不要试图按文件一刀切，本工作流与另外 4 条在工作区行级交织）。
> **A1 取数已完成并决策不接线**（零差异，见小结）；**第 2 步入点侧已全部收口**：
> A5+A6、A4+A3、A2（收窄版）已完成，**A7 取证后改判不做**（机制收益为 0，见 §2.2）。
> **下一步是第 3 步（模型/数据）**：B1 补"回放中的实战镜头"样本（根本成因）→ B3 补 test 集
> → B2 阈值按重训后重新推导；B4（把 OCR 回放证据作为模型补充输入）可与 B1 合并考虑。
> `broadcast_mode` 与入点的关系不变：**入点精度不能靠它**，只能靠"回放否决 + `precise` 交叉证据"。
