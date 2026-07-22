# LSC 全量审查修复总纲

## 目标

将 2026-07-22 代码审查报告中的 Critical / High / 专项债项，按可合并批次落地为可验收修复，恢复工具链可运行性，并堵住已确认的运行时崩溃与本地 CSWSH 面。

不借机拆分 `room_handler.py` / `Workbench/index.tsx`，不重做 Valorant 模型训练，不清理历史 `release/win-unpacked` 入库问题（另开 chore）。

## 背景

审查覆盖 `lsc`、`python-backend`、`lsc-electron`、`scripts/valorant_vision`、`tests`、CI/依赖。客观数据：pytest 1009 收集、1005 通过 / 4 失败（均在 `test_frontend_stability_guards.py`）；ruff 85 问题；mypy 配置损坏无法运行；危险模式扫描（`shell=True` / `pickle` / `eval` 等）干净。

已抽查确认 Critical 五条属实：

1. `room_handler.py` `_do_analysis_and_export` 使用未定义 `analysis_time`
2. `server.py` Origin `startswith` 可被 `localhost.attacker.com` 绕过，且无握手鉴权
3. Electron 无单实例锁；Windows `detached` 子进程清理不可靠
4. `clip.py` 导出 watchdog 依赖从未赋值的 `self.width/height/hardware_encoder`
5. `ocr_accel.save_probe_cache` 写失败可导致 OCR 路径致命崩溃

## 文档包（方案 B）

| 文件 | 职责 |
|---|---|
| 本文件 | 范围、原则、批次依赖、总验收、非目标 |
| `2026-07-22-audit-remediation-batch1-critical-design.md` | Critical ×5 止血 |
| `2026-07-22-audit-remediation-batch2-high-design.md` | High：并发 / 正确性 / 前后端一致性 / 取消 |
| `2026-07-22-audit-remediation-batch3-debt-design.md` | CI/mypy/依赖、安全加固、日志、性能 |

每批随后用独立 implementation plan（`docs/superpowers/plans/`）执行；本总纲不替代计划中的逐步任务清单。

## 原则

1. **批次可独立合并**：Batch2/3 不得依赖未合入 Batch1 的行为变更（WS token 契约除外，须 Electron 同步发版）。
2. **只修审查项**：不做无关重构；文件已过大处仅做局部必要改动。
3. **最小回归**：每个改动带单测或 guard；前端 guard 与实现同步修正。
4. **安全渐进**：Origin 精确匹配可先于 token；单实例锁可先于 Job Object。
5. **可回滚**：每批独立 PR；生产默认强制 token，开发可用环境变量临时旁路（默认关闭旁路）。

## 批次依赖

```text
Batch1 Critical ──必须先合──▶ Batch2 High
                                    │
                                    └──▶ Batch3 Debt（可与 Batch2 尾部并行）
```

- Batch1 解决崩溃与攻击面，必须优先。
- Batch2 解决正确性与用户可见卡死，依赖 Batch1 的导出/分析路径稳定。
- Batch3 工具链与可观测性可与 Batch2 后期并行，但不阻塞 Batch1 合并。

## 总验收矩阵

| 批次 | 必须通过 | 主要风险 / 缓解 |
|---|---|---|
| Batch1 | C1–C5 相关测试绿；分析并导出不抛 `NameError`；非法 Origin / 无 token 拒连；单实例锁生效；watchdog 超时随分辨率变化；只读目录下 OCR 不崩 | Token 需前后端同发 → feature flag `LSC_WS_TOKEN_REQUIRED`（生产默认强制，开发可关） |
| Batch2 | 配置/分析任务/持久化锁生效；终态广播不丢；ClipList 稳定 id；原 4 个 frontend guard 失败清零；CPU 回退导出与 OCR 可取消 | 终态不丢抬高队列水位 → 只保护白名单事件，优先丢高频可合并消息 |
| Batch3 | mypy 可跑通约定包；CI ruff/mypy/tsc 有真实红线；`output_dir` 负例；INFO 无 Cookie | coverage 门槛过高卡 CI → 首版低门槛或仅核心包 |

**全量成功标准**：Critical 未修项 = 0；High 清单均有对应提交；Debt 的 D1–D3 完成，D4 至少完成监控 sleep、hybrid deepcopy、Electron console 写盘三项；未扩到非目标。

## 非目标

- 拆分 `python-backend/handlers/room_handler.py` 或 `lsc-electron` Workbench 巨型文件
- 为多数 platforms 适配器 / `lsc/gui` / `editor` 补齐全面测试
- 删除或重写已入库的 `lsc-electron/release/` 历史构建产物
- Valorant 相位模型重训、回合边界算法大改（已有独立 valorant specs）
- 将广播循环改为纯事件推模型、音频全量流式读取大重构（仅文档化或低风险收敛）

## 回滚策略

- 按 PR 回滚单批即可恢复该批行为。
- Batch1 WS token：若旧 Electron 连新后端失败，短期用 `LSC_WS_TOKEN_REQUIRED=0`（仅非生产），同时催促前端发版；不得在生产默认关闭。
- Batch2 广播策略：若出现延迟异常，可退回「丢最旧」但保留终态白名单保护为最小集。

## 后续

1. 本总纲与三份 batch design 经用户审阅通过后，再写对应 `plans/` 实施计划。
2. 实施顺序固定为 Batch1 → Batch2 → Batch3（Debt 可并行尾部）。
3. 每批合并前跑相关 pytest；Batch3 合并前确认 CI 文件变更在 PR 上真实执行。
