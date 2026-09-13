# 夹具：2026-09-11 20:45:21 赛事草稿「已定稿切片被丢弃」现场

冻结一次真实导出事故的输入，用于回归 `tests/test_broadcast_export_authority_lifetime.py`
与 `tests/test_timeline_review_guards.py` 之外的导出权威链路。**只读，不参与生产路径。**

## 现场一句话

20:45:21 用户导出剪映草稿：请求 8 条，实际只写入 3 条。其中 `round-000105` 在 20:43:26 已由
赛事审计定稿（`audit=passed` / `end_by=broadcast_exclusion` / `end_quality=precise`），
却因为**任务态在同一秒被 pop、权威回落到 20:37 的旧分析 sidecar**，被改回
`pending_lookahead` 后按「未确认」跳过。另有 `round-000135` 因为**不在任何权威集合**里，
被报成「旧分析会话遗留切片」（真实原因是它 20:45:17 才出结论、出点未定稿）。

## 文件与来源

| 文件 | 来源 | 说明 |
|---|---|---|
| `2026-09-11_20-10-29_至_2026-09-11_20-38-42.mp4` | 0 字节占位 | 只为 `os.path.isfile()` 成立，让 sidecar 定位生效；真实录像 1.27GB 不入库 |
| `…analysis.json` | 真实文件（mtime 20:37:41，8 段） | 导出当时后端读到的**旧**分析快照，`round-000105/135` 都还是 `pending_lookahead` |
| `…finalization.json` | 真实文件（mtime 20:45，accepted 3 / rejected 1 / pending 0） | 收尾快照，只落盘了 45/55/70 三条 accepted |
| `clips.json` | `backend.log` 的 WS 报文原文 | 20:45:21 前端实际发出的 8 条（含 105 的 passed/precise） |
| `authority_snapshot.json` | 派生（见下） | 20:45:20 任务 pop 时的后端 `listed_clips` 权威快照 |
| `expected.json` | 人工口径 | 修复后应得的 included / 逐条 reason_code |

`authority_snapshot.json` 以前端请求副本为底，按 `backend.log` 的实测审计结论修正两条
前端尚未合并的终态（这是权威与前端快照的真实差异，不是编造）：

```
20:44:09 赛事回合审计完成: 1232.0-1346.0 -> 1232.0-1346.0, audit=pending_no_exclusion, status=pending, end_by=next_prep
20:45:17 赛事回合审计完成: 1352.0-1445.0 -> 1352.0-1445.0, audit=passed, status=vision_confirmed, end_by=next_prep
20:43:26 赛事回合审计完成: 1050.2-1173.0 -> 1050.2-1113.3, audit=passed, status=vision_confirmed, end_by=broadcast_exclusion
```

其余日志证据（同一现场的其它不变量）：

```
20:45:21 generate_jianying_draft_response: requested_clip_count=8, included_clip_count=3,
         skipped_clip_count=5, warnings=[…R03 未确认…, …R05 未确认…, …R05 未确认…,
         …R08 未确认…, …R08 非当前录制权威切片（旧分析会话遗留切片）…]
20:43:26 精修候选终态…round_key=round-000105…（只进 listed，未进 durable 投影）
20:39:27 改名后仍写 2026-09-11_20-10-29_录制中.analysis.json（「至_」文件被冻在 20:37:41）
```

## 期望口径（见 `expected.json`）

- included = {45, 55, 70, **105**}
- skipped 逐条 reason_code：71 `END_NOT_FINAL`、76 `NEVER_AUDITED`、123 `NO_EXCLUSION_EVIDENCE`、135 `END_NOT_FINAL`
- **红线**：不放宽 `_broadcast_gate_passed`。105 能入列是因为它的权威终态本该可达；
  71/123/135 的出点确实未定稿 / 无排除证据，无论怎么修都不得进草稿。
