# Batch3 Debt：工具链 / 安全加固 / 可观测性 / 性能设计

父文档：`2026-07-22-audit-remediation-overview-design.md`  
可与 Batch2 尾部并行；不阻塞 Batch1。

## 目标

修复审查专项债：让 mypy/CI 真正可用，补齐安装依赖，收紧设置路径与构建泄露面，提升日志可诊断性，并做低风险性能收敛。

## D1. 工具链与 CI

### D1a. mypy 配置

**现状**：`warn_return_value = true` 非法（应为 `warn_return_any`）；`python-backend` 连字符目录导致包检查困难。

**设计**：

- 修正 `pyproject.toml` `[tool.mypy]` 选项名
- 约定检查范围：首版强制 `lsc/core`（及已干净子包）；`python-backend` 用显式文件列表或 `mypy_path` + 模块名映射，避免把目录名当合法包名硬扫
- CI 中真正执行 mypy，失败即红（范围与上一致，可逐步扩大）

### D1b. `pyproject.toml` 依赖

**现状**：`dependencies` 仅 PySide6+numpy；`websockets`/`psutil` 等在 `requirements.txt`，`pip install .` 缺运行时依赖。

**设计**：将启动后端所必需的运行时依赖写入 `project.dependencies`（至少 `websockets`、`psutil`，其余按「安装即可起 WS 后端」最小集与 `requirements.txt` 对齐）；可选重型依赖保持 optional extra。

### D1c. CI 红线

**现状**：ruff 仅强制 `lsc/core/`；全量 `--exit-zero` + `continue-on-error`；装 mypy 不跑；无覆盖率门槛；Electron 不跑 `tsc`。

**设计**：

| 检查 | 行为 |
|---|---|
| ruff | 强制 `lsc/` + `python-backend/`（合入前清零或 ignore 显式列出）；`scripts/valorant_vision` 可暂 informational |
| mypy | 强制约定包范围 |
| pytest | 保持矩阵；coverage 首版 `--cov-fail-under` 取低门槛（建议 40 或仅上报，避免一夜全红） |
| Electron | PR job：`npx tsc --noEmit`（不强制每次 electron-builder） |

### D1d. Frontend stability guards

修正 `test_frontend_stability_guards.py` 与当前 TS 对齐，或改为更稳的符号/结构断言；与 Batch2 H3c 一起保证 4 红清零（若 Batch2 已清，本项为回归守护）。

## D2. 安全加固

### D2a. `output_dir` 白名单

`save_settings`（及等价入口）解析 `output_dir` 后必须落在用户家目录、`~/LSC`、或既有应用数据白名单根下；拒绝任意盘符/穿越。错误返回友好中文提示。

### D2b. 生产构建符号

Electron 生产 `main`/`preload`：`sourcemap: false`；开启 minify（或至少关闭 sourcemap）。开发配置不变。

### D2c. FFmpeg 命令日志脱敏

`mse_streamer`（及同类）INFO 禁止打印含 Cookie/Authorization 的完整命令；DEBUG 可截断脱敏。

## D3. 日志可观测性

- 「操作异常（已忽略）」：资源清理路径保留 DEBUG；业务/可恢复失败至少 WARNING + 异常类型
- 不引入新日志框架；不改轮转大小（2MB×5）除非审查另有要求

## D4. 性能（低风险）

| 项 | 设计 |
|---|---|
| 进程监控 `sleep(0.05)` | 改为 `0.2`～`0.5` 或事件驱动；保活语义不变 |
| 广播 100ms | 保持；与 Batch2 终态不丢配合 |
| hybrid `deepcopy(fsm)` | 避免每 tick 全量深拷贝；拷必要字段或复用快照 |
| Electron `appendFileSync` console | 异步队列或限频，避免主进程同步写盘 |
| 整段音频 `readframes` | 本批仅在总纲/本文件记录风险；流式读取列为 follow-up |

## D5. 明确非目标

- 拆分巨型 handler/Workbench
- platforms/gui/editor 全面补测
- 清理 git 中历史 `release/win-unpacked`（另开 chore；要求新构建勿再提交 unpacked）
- Valorant 模型/算法大改
- 广播改为纯推模型、音频管线大重构

## 验收

- `mypy` 按约定范围可运行且通过
- CI 对 ruff/mypy/tsc 失败会红
- `output_dir` 负例测试
- INFO 日志抽样无 Cookie
- D4 三项（sleep、deepcopy、console）有代码变更说明

## 回滚

- CI 门槛若误伤：先收窄强制范围，不改回 `exit-zero` 伪装绿灯
- `output_dir` 校验过严：扩展白名单根，不关闭校验
