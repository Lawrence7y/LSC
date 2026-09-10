# broadcast_mode 影子模式取数小结（2026-09-10）

对应交接文档 `docs/plans/valorant-broadcast-inpoint-workstream-20260910.md` **§3 第 1 步**的验收交付物。
目的：为「是否正式接线 `broadcast_mode=True`（任务 A1）」提供决策数据。

---

## 1. 场次与方法

| 项 | 值 |
| :--- | :--- |
| 平台 / 房间 | `huya` / `29701502` |
| 标题 | 骑士之路 \| EDG冠军赛夺冠回顾 |
| 档位 | **显式 `broadcast`**（`profile_reason='explicit'`）；`auto` 不会命中，标题不含赛事关键词 |
| 影子开关 | `LSC_VALORANT_BROADCAST_MODE_SHADOW=1`，**后端日志实证生效** |
| 录制时长 | **1432s（≈23.9 分钟）**，已分析 1366s |
| 模型 | `valorant_phase_v1` / `DmlExecutionProvider` |
| 影子扫描次数 | **46** |

后端启动即自证开关到位：

```
[INFO] lsc.backend: broadcast_mode 影子开关 LSC_VALORANT_BROADCAST_MODE_SHADOW='1'
```

后端同时给出了预期内的提示（标题无赛事特征但被显式指定）：

```
profile_mismatch_warning: True
当前直播间无明显赛事特征，但已显式启用赛事策略 (broadcast)，可能导致分析滞后
```

---

## 2. 累计统计

| 指标 | 生效路径 | 影子路径（`broadcast_mode=True`） |
| :--- | ---: | ---: |
| 回合数 | **10** | **10** |
| `next_combat` 闭合数 | **0** | **0** |
| 影子独有（shadow_only） | — | **0** |
| 生效独有（primary_only） | — | **0** |
| 时长变化（resized） | — | **0** |

**→ 零差异。** 46 次扫描、10 个回合，两条路径**逐条完全一致**，`broadcast_mode` 对结果没有任何影响。

### 产出的切片（7 条）

| 回合 | 区间 | 时长 | `end_by` | 起点/终点质量 | 审计 | conf |
| :--- | :--- | ---: | :--- | :--- | :--- | :--- |
| R01 | 11.0–71.2s | 60s | `broadcast_exclusion` | coarse/precise | passed | 0.7/0.92 |
| R02 | 221.0–324.5s | 104s | `broadcast_exclusion` | coarse/precise | passed | 0.7/0.92 |
| R03 | 358.5–468.7s | 110s | `broadcast_exclusion` | coarse/precise | passed | 0.7/0.92 |
| R04 | 565.5–671.2s | 106s | `broadcast_exclusion` | coarse/precise | passed | 0.7/0.92 |
| R05 | — | 56s | `broadcast_exclusion` | coarse/precise | passed | 0.7/0.92 |
| R06 | — | 67s | `broadcast_exclusion` | coarse/precise | passed | 0.7/0.92 |
| R07 | — | 92s | `next_prep` | — | — | — |

审计计数：`accepted=6 / rejected=5 / manual_review=0`。

---

## 3. 判读与建议

按交接文档 §3 第 1 步的判读表，本次落在：

> **「无显著差异 → 该场景碎片少 → 维持现状，先做入点侧（A2/A3）」**

### 建议：**不要接线 A1**（`broadcast_mode=True`）

依据不只是"没差异"，而是一个更强的因果链：

1. **碎片确实与 `next_combat` 强相关**——本次 `next_combat` 闭合数为 **0/0**，一条都没有。
2. **7 条切片里 6 条由 `broadcast_exclusion` 闭合、1 条由 `next_prep` 闭合**，时长 56–110s 均为正常回合长度 → **本段不存在那类碎片**。
3. 既无 `next_combat` 闭合的碎片，`broadcast_mode`（只抑制 COMBAT 态 fresh-clock 假切分）**就无事可做**。

**一条支持"别接 A1"的活证据**：中途 R03 一度是 **16s 碎片**（`end_by=next_prep`、`broadcast_audit=pending_lookahead`），被**现有审计自行修正为 110s 完整回合**（改判 `broadcast_exclusion`）。也就是说**该类碎片已被现有机制消化**，不需要 `broadcast_mode` 兜底。

### 局限（必须随结论一起读）

1. ⚠️ **本次没有重现**交接文档 §1.3 的碎片形态。那次碎片证据来自**另一段录像**（`12-00-36`，回放密集区在 615–629s）。因此"零差异"**不能**外推为"`broadcast_mode` 永远无用"，只能说**在这类内容上无用**。
2. 采样规模：46 次扫描 / 10 回合 / 单场，未覆盖长时回放密集区。
3. 决策影响：A1 维持"不接线"。若日后在高碎片内容上取到 `primary_next_combat ≫ shadow_next_combat`，再重开此项。

---

## 4. 取数过程中发现的副缺陷

### 4.1 ✅（已修）Electron 环境变量白名单缺 `LSC_VALORANT_BROADCAST_MODE_SHADOW`

`lsc-electron/electron/main.ts` 的 `safeEnv` 白名单只透传 `LSC_VALORANT_MODEL_DIR` / `LSC_VALORANT_VISION_SHADOW`。
**按交接文档直接开 `LSC_VALORANT_BROADCAST_MODE_SHADOW=1` 不会生效**——变量被静默丢弃，会白录一整场且无任何报错。

已修：加入白名单 + `tests/test_electron_backend_env.py` 守卫 + 后端启动打一行开关自证日志（`python-backend/main.py`）。

### 4.2 ⚠️ `start_confidence` 是二值代理，不是实测置信度

实测 R01 得到 `start_confidence=0.7`、`start_quality=coarse`。真值来自 `lsc/analyzer/valorant_broadcast.py:519`：

```python
item["start_confidence"] = 0.95 if item["start_delta"] is not None else 0.70
```

**这推翻了交接文档中"`start_confidence` 恒为 0.95、`continuous_finalization.py:202` 的 0.8 门是死门"的描述。** 准确表述：

- 它是 **二值代理**（有 delta→0.95，无 delta→0.70），**不是**实测视觉置信度；
- `:202` 的 `confidence < 0.8 → coarse` 门**是活的**（实测已触发）；
- 但该门**冗余**：`start_confidence ≥ 0.8` ⟺ `start_delta is not None`，而广播档 `boundary_refined` 本就要求 `start_delta is not None` → **永远提供不了 `boundary_refined` 之外的证据**。

→ **R3 结论（`precise` 无独立精度证据）依然成立，但机制描述需更正**；
→ **A4 的正确落点是 `valorant_broadcast.py:519`**（把二值代理换成实测值），而非原写的 `valorant_ocr_rounds.py:873`。

### 4.3 ✅（已修 `8ba9a67`）收尾改名不幂等 → 每次退出多一份完整录像副本

录制目录中出现**逐字节相同**（md5 一致、NTFS File ID 各异、硬链接数 1）的重复录像：

| 会话 | 副本数 | 触发场景 |
| :--- | ---: | :--- |
| `00-50-16` / `01-19-14` / `02-06-12` / `09-06-29` / `09-49-28` / `12-00-36` / `14-16-36` / `15-18-04` | 各 1 份 ✅ | 正常停录 |
| `17-40-04` | **4 份**（121MB×4，且 `_录制中.mp4` 未清理） | 17:43 退出（日志 `success=false, errors=1`） |
| `18-05-24` | **3 份**（1045MB×3） | 18:29 优雅退出（`success=true`） |

- **只在"应用退出 / 收尾"路径上发生**，正常停录的 8 个会话都干净 → 指向 `finalize_room_recording` / `shutil.move` 的非幂等调用，而非录制本身。
- **实测浪费**：本次单场 3.06GB（`EDG夺冠回顾/` 目录此刻占用 7.5GB）。
- **连带风险**：sidecar 命名分裂——录像已改名为 `..._至_..._18-29-22.mp4`，但分析 sidecar 仍是 `2026-09-10_18-05-24_录制中.analysis.json`，而 `.finalization.json` 只有最后一个副本有对应项。这会威胁 `docs/spec-jianying-draft-export.md` 要求的 `recording_id + round_key + sidecar` 三重校验。
- 另有 `2026-09-10_00-41-10_录制中.mp4` **始终未改名**（未收尾的孤儿录像）。

**已修复（`8ba9a67`）**——根因与修法：

- **根因**：`orchestrator._finalize_and_commit_recording` 固定重试 3 次、每轮取
  `datetime.now()` 作结束时刻（故 3 个不同文件名）；`recording_layout.finalize_recording_file`
  原先直接用 `shutil.move`——源被占用时 `os.rename` 抛错 → 回退 `copy2`（成功）+
  `unlink`（失败）→ 抛异常，**而已复制出来的目标留在磁盘**。每重试一次多一份，正好 3 份。
  占用者不是录制 FFmpeg（已被 `proc.wait` 等退），而是退出时仍在读录像的**并发分析/ffprobe**
  ——这也解释了它只在退出时复现。
- **验证**：对照实验在"源被占用 + 重试 3 次"下，旧实现留 **3 份**（文件名与磁盘实测的
  `18-29-20/21/22` **完全一致**），新实现留 **0 份**。
- **修法**：① `finalize_recording_file` 改用 `os.replace`（原子改名），仅在确属跨盘
  （EXDEV）时才复制+删源且删源失败须回滚目标，其余错误直接上抛——**源被占用时绝不复制**；
  ② 调用方重试改为幂等（源消失即复用，不再改名）+ 有界 5s 等待窗口 + 超时保留原「录制中」
  名并告警（宁可没改名也不留副本）。
- **连带问题亦已修（`ff0f542`）**：sidecar 命名分裂（分析 sidecar 停在 `_录制中`）——
  实测 11 个 `.analysis.json` 里只有 1 个与录像同名，根因是定稿改名只搬 mp4、不搬 sidecar。
  影响不表面：剪映导出按**最终录像名**取 sidecar，落空后会丢掉"分析 sidecar 中的拒绝标记"，
  **被拒切片可能重新混入草稿**；而代码里现有的惰性重绑定只覆盖 `finalization.json`，
  分析侧永久失联。修法：在定稿改名咽喉处同步搬运 sidecar（含 `.bak`），
  存量 14 项已一次性归位（10 改名 + 4 归档）。

### 4.4 ⚠️ broadcast 档分析滞后

`net_coverage_throughput` 最低 **0.753**（覆盖速度仅为录制速度的 3/4），`backlog_mode: priority-catchup`，滞后由 27s 增长至峰值 **178s**，单轮扫描周期由 7s 升至 88s；`consecutive_scan_timeouts` 始终为 0。

这是 `broadcast` 档带视觉模型深度审计的固有代价（后端已预警）。**对取数正确性无影响**（只是延迟），但意味着**停止录制后仍需等 finalization 追平尾部**。本次退出已保存 `finalization_state=checkpoint_saved`，可续跑。

---

## 5. 复现方法与踩坑

```bash
# 1) 带影子开关启动（dev 模式；Electron 会把白名单内的 LSC_* 透传给后端）
cd lsc-electron
export LSC_VALORANT_BROADCAST_MODE_SHADOW=1
npm run dev

# 2) UI：直播类型→无畏契约；视角策略→官方赛事/二路解说（★ 必选，auto 不会命中）

# 3) 读数据（注意日志会轮转，必须把 backend.log.1 一起算）
grep "broadcast_mode 影子对比" backend.log backend.log.1
```

| 坑 | 说明 |
| :--- | :--- |
| **日志轮转** | 单文件 2MB 上限。只读 `backend.log` 会漏掉大部分扫描（本次 46 次里 45 次在 `backend.log.1`）。 |
| **`auto` 档不命中** | 现有房间标题均无赛事关键词，必须显式选 `broadcast`。 |
| **快照类接口不可用于核算** | `get_continuous_analysis_status` 的 `listed_clips` 是**当前**状态；回合边界会被审计后续修正（如 R03 由 16s→110s），不能用早期快照下结论。 |
| **重复录像会污染目录** | 见 §4.3，核对"哪个文件才是本次录制"时必须按 mtime + File ID 查，不能只看文件名。 |
