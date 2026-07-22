# Batch1 Critical：审查止血设计

父文档：`2026-07-22-audit-remediation-overview-design.md`

## 目标

消除审查确认的 5 个 Critical：运行时崩溃、本地 CSWSH、多开抢端口/僵尸后端、导出误杀、OCR 只读目录崩溃。本批只做止血，不扩 High/Debt。

## C1. `analysis_time` 未定义

**现状**：`handle_start_analysis_export` → `_do_analysis_and_export` 调用 `save_analysis_results(..., analysis_time_sec=analysis_time)`，该名未赋值；另两处调用未传耗时，默认 `0.0`。

**设计**：

- 进入分析逻辑前：`t0 = time.monotonic()`
- 调用 `save_analysis_results` 前：`analysis_time = time.monotonic() - t0`
- 统一三处调用均传入真实耗时（分析路径两处 + 持续分析落盘处若存在同类遗漏一并修）
- 取消/早退路径：若已开始计时仍可写入已消耗时间；未开始则不传或传 `0.0`

**验收**：针对该路径的单测或 mock，断言 `analysis_time_sec > 0`（或取消时 `>= 0`）；人工触发「分析并导出」不再 `NameError`。

## C2. WebSocket Origin 精确匹配 + 一次性 token

**现状**：`origin.startswith(('http://localhost', 'http://127.0.0.1'))` 可被 `http://localhost.attacker.com` 绕过；协议层无 token。

**设计**：

1. **Origin**
   - 允许：`null`（Electron `file://` 常见）、以及 URL 解析后 host ∈ `{localhost, 127.0.0.1, ::1}` 且 scheme ∈ `{http, https}`
   - 拒绝：前缀伪造、缺 Origin、其它 host
2. **Token**（浏览器 `WebSocket` 无法设自定义 Header，故不用 `X-LSC-Token`）
   - 后端启动：`secrets.token_urlsafe(32)`，读环境变量 `LSC_WS_TOKEN`（Electron 注入）；若未设置则自生成并仅本进程持有（此时仅知 token 的客户端能连）
   - Electron 主进程生成 token → 写入后端 `env.LSC_WS_TOKEN` → preload/`getBackendWsToken` 暴露给渲染进程
   - 客户端连接 URL：`ws://127.0.0.1:9876/?token=<token>`（query 名固定 `token`）
   - 校验：`hmac.compare_digest`；缺失或错误 → `1008` 关闭
3. **兼容**
   - `LSC_WS_TOKEN_REQUIRED`：默认 `1`（强制）；仅本地开发可 `0`（仍保留 Origin 精确匹配）
   - Vite 开发：localhost host 白名单；dev 可通过主进程或本地配置拿到同一 token

**验收**：负例（`localhost.attacker.com`、无 token、错 token）拒连；正例（合法 Electron / 本地 Vite + token）可连。

## C3. Electron 单实例锁 + 子进程生命周期

**现状**：无 `requestSingleInstanceLock()`；Windows `detached: true` 派生后端，主进程异常退出后易残留占端口。

**设计**：

- 启动早期：`requestSingleInstanceLock()`；失败则 focus 已有窗口并 `app.quit()`
- `second-instance`：恢复/聚焦主窗口
- 生命周期：
  - `before-quit`：`event.preventDefault()`（首次）→ 发停后端 → 等待退出（超时后强杀）→ 再允许退出
  - Windows：在保持现有权限需求前提下，用 Job Object（或项目已有等价手段）将 Python 子进程与主进程绑定；若 Job Object 引入成本过高，本批最低交付为「单实例锁 + before-quit 可靠等待」，Job Object 作为同批优选增强
- 不改变「Windows 下 detached 避 WinError 5」的既有约束，除非 Job Object 方案已验证可替代

**验收**：手动双开只留一实例；正常退出后 9876 释放；崩溃场景在支持 Job Object 时无僵尸（否则文档化残留风险为已知限制）。

## C4. 导出 watchdog 超时参数化

**现状**：`_watchdog` 读 `self.width/height/hardware_encoder`，从未赋值，注释中的分辨率/软编倍率无效，恒 300s。

**设计**：

- 在启动 FFmpeg 导出前解析或传入 `width`/`height`/`hardware_encoder`（来自探测、`ExportOptions` 或命令已知信息）
- `_watchdog` 闭包捕获这些局部变量，不再读未定义实例属性
- 倍率保持现语义：基线 300；像素 >4K → ×2.5；>1080p → ×1.5；软编 → ×2（可叠加）
- 无分辨率信息时保持 300s 基线

**验收**：单测给定 4K+软编断言 timeout 计算值；无分辨率时为 300。

## C5. OCR probe 缓存写失败不致命

**现状**：`save_probe_cache` 的 `write_text`/`replace` 无防护；`create_ocr` 路径上写缓存失败可变成致命错误。

**设计**：

- `save_probe_cache` 内部 `try/except OSError`（及 `PermissionError`）：失败打 WARNING，不抛
- 保证 `create_ocr` 在缓存不可写时仍返回可用 OCR 实例（含 CPU 回退）
- 不改变探测优选逻辑；仅让缓存变为 best-effort

**验收**：mock 只读/写失败时 `create_ocr` 成功返回；不出现未捕获 `OSError`。

## 非目标（本批）

- 消息队列终态保护、ClipList key、config 锁等 High 项
- mypy/CI/ruff 清零
- 拆 handler 文件

## 测试与发布

- 新增/更新：`tests/` 下 Origin/token、watchdog 超时计算、ocr 缓存写失败、analysis_time 传参相关用例
- Token 传递固定为：后端环境变量 `LSC_WS_TOKEN` + 客户端 URL query `?token=`（不用自定义 Header / 首包 auth）
- Electron 与 python-backend **同 PR 或同发布单元**合入，避免 token 契约撕裂
- 合并前：相关 pytest 绿；手工清单覆盖 C3

## 回滚

- 按文件回滚 C1/C4/C5 无协议影响
- C2/C3 需前后端一起回滚；可用 `LSC_WS_TOKEN_REQUIRED=0` 作紧急开发旁路，生产不默认关闭
