# L3 实测 runbook：一次真实会话的「列表↔草稿↔权威账本」验收

> 配套工具：`scripts/verify_live_session.py`（只读，7 项检查，退出码即门禁）。
> 相关前序：`finalize-round-scoping-draft-20260912.md`（Part 1/Part 2）、
> `candidate-dedup-merge-draft-20260912.md`（L1/L2 落地记录）。

## 1. 一句话

以前 L3 是"人眼读日志"；现在是一条命令，判 7 件事，**失败即非零退出**，可直接进门禁。

## 2. 前提

使用**已包含下列改动**的构建（当前工作树即满足）：

* C1 终态权威快照跨会话保留、C2 扫描通路终态补投影、C3 改名后落盘路径同步、
  C4 跳过原因码、C6 收尾完成判定收紧；
* L1 区间内边界自检（`INTERIOR_BOUNDARY`）、L2 定稿后组内择一（`DUPLICATE_ROUND`）。

## 3. 操作时序（关键：导出后**尽快**跑校验）

1. 起录 → 开持续分析（赛事档 broadcast）→ 跑够 ≥15 分钟（有多个回合）；
2. 停录 → 等界面出现收尾完成（`finalization_state=completed`）；
3. 导出草稿（记下回执里的 `draft_dir`）；
4. **立刻**跑第 4 节命令——剪映一旦打开该草稿会加密 `draft_content.json`，
   草稿目录也可能被清理（本次事故的 2045 草稿就只剩 `.recycle_bin`）。

## 4. 命令

```bash
python scripts/verify_live_session.py \
  --log "%APPDATA%/lsc-electron/logs/backend-stdout.log.2" \
  --log "%APPDATA%/lsc-electron/logs/backend-stdout.log.1" \
  --log "%APPDATA%/lsc-electron/logs/backend-stdout.log" \
  --room-id <room_id> \
  --finalization "<录像目录>/<录像名>.finalization.json" \
  --draft-dir "<draft_dir>" \
  --out docs/reports/verify-live-session-<date>.json
```

日志轮转文件按**时间顺序**多传几个（`.2` → `.1` → 当前）；`--room-id` 用于在多房间会话里
挑出该房间那次导出（响应载荷本身不含 room_id，工具按 `request_id` 与请求关联）。

## 5. 判据（7 项）

| # | 检查 | 期望（改后） | 失败通常意味着 |
|---|---|---|---|
| 1 | `draft_counts_consistent` | `requested == included + skipped` | 导出计数口径漂移 |
| 2 | `skipped_have_reason_codes` | 每条跳过带 `reason_code` | C4/v1.0.15 未生效，或新增了没有原因码的跳过路径 |
| 3 | `no_finalized_clip_dropped` | 权威集合里"出点已定稿"的条数 ≤ 实际写入数 | **105 类事故回归**（C1/C2） |
| 4 | `draft_segments_match_included` | 草稿切片轨段数 == `included_clip_count` | 导出器口径不符 / 草稿已加密或目录被清理（"无法验证"，不会假绿） |
| 5 | `finalization_completed` | 收尾 sidecar `phase=completed` | 收尾没跑完就导出 |
| 6 | `all_listed_have_terminal` | 每条已入列切片都有终态归属 | **C6 契约**（事故里 71/76/105/123/135 缺） |
| 7 | `authority_snapshot_preserved` | 存在「终态权威快照已保留」且无 `NOT_IN_AUTHORITY` 跳过 | **C1 未生效**（任务态 pop 后权威不可达） |

## 6. 改前基线（本次事故归档，2026-09-12 实测）

命令同第 4 节，`--draft-dir` 指向事故草稿 `LSC_EDG夺冠回顾_20260911_2045`，`--finalization`
指向夹具 sidecar。**结果：2 OK / 5 FAIL，exit 1**——即工具能抓住本次事故：

```
[OK]   draft_counts_consistent: requested=8, included=3, skipped=5
[FAIL] skipped_have_reason_codes: 跳过 5 条, 逐条明细 0 条（响应只有聚合告警，无法定位到具体切片）
[FAIL] no_finalized_clip_dropped: 权威集合定稿 4 条 [045,055,070,105], 实际写入 3 条; 少写 1 条
[FAIL] draft_segments_match_included: 草稿切片轨段数=None, included_clip_count=3（草稿目录不存在：可能已被清理）
[OK]   finalization_completed: 收尾 sidecar phase=completed, final_round_count=7
[FAIL] all_listed_have_terminal: 已入列 9 条, 有终态归属 4 条; 缺终态: [071,076,105,123,135]
[FAIL] authority_snapshot_preserved: 快照日志 0 条（C1 未生效：任务态 pop 后权威不可达）
```

其中 `finalization_completed` 通过、而 `all_listed_have_terminal` 失败，正是"sidecar 说完成了、
但仍有已入列切片没有归属"的现场——这条组合就是 C6 要修的东西。

> 注意：该基线依赖 `%APPDATA%\lsc-electron\logs\backend-stdout.log{,.1}` 这两个**会轮转**的文件；
> 日志一旦被覆盖就用第 7 节的 `--self-test` 验证工具本身，基线数字以本节记录为准。

## 7. 工具自检（证明它会变绿）

```bash
python scripts/verify_live_session.py --self-test
```

在临时目录合成一次"3 条候选 / 2 条入草稿 / 1 条带原因码跳过"的会话，**7/7 通过、exit 0**（已实测）。
这样"只会报红的门禁"与"永远绿的门禁"都被排除。

## 8. 已知边界（不会假绿，但要知道为什么红）

* **第 4 项**：草稿被剪映打开后 `draft_content.json` 会加密；此时工具读 `.backup/*.load.bak`，
  若两者都不可读 → 报 "无法验证" 并按 **FAIL** 处理（不会误判为通过）。故务必在打开剪映前跑。
* **第 5 项**用 sidecar 而非日志：终态广播不落 backend 日志，只有轮询响应，取轮询会因时序抖动误判。
* **第 3 项的"疑似被丢"定位**在旧版响应（无 `skipped` 明细）里给不出具体 round_key——
  升级后响应带明细才能点名；旧日志只能给出"少写 N 条"。
* 日志轮转：漏传早期文件会让"导出前最后状态""快照日志"判据失真，尽量按序传全。
