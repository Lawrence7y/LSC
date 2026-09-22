---
feature: hardening-and-diagnostics
status: designed
updated: 2026-09-22
branch: feat/jev-highlight-judge
commits: (base)..(head)
---

# Hardening and Diagnostics

## Report

## [S1] Problem

LSC 已上架微软商店，但源码审查发现供应链、凭据、路径白名单、测试防线与商店支持闭环上的缺口：
未钉死哈希的第三方 FFmpeg 下载源、Cookie 明文/进命令行、输出目录可指向盘符根、前端假守卫不验证行为、
商店用户缺一键诊断包、Store 构建仍引导 GitHub 下载 Setup。

## [S2] Design

固化「批次 1–2 止血 + 测试真行为化 + 商店支持」，不改变产品主路径语义。

- 供应链：FFmpeg 默认官方源；镜像 opt-in；强制 `LSC_FFMPEG_SHA256`（或显式放行）；pip 默认 PyPI；ps1 去第三方代理。
- 凭据：Cookie 默认 DPAPI；env/FFmpeg `-headers` 不携带 Cookie。
- 路径/IPC：输出目录拒绝盘符/UNC 根；`local-media:allow-root` 仅受信根；WS Token 仅主窗口。
- 持久化：settings 覆盖前 `.bak` + 损坏回退；start/server 关停 flush rooms。
- 参数：码率单位归一化；MSE 码率安全解析；导出预设软编默认 + `resolveExportCodec`。
- 并发/协议：Lease/计数器加锁；WS 限流回 `rate_limited`；capture `note_child`；剪映草稿备份恢复。
- 测试：假守卫迁 vitest 行为测试；供应链/批次1 真行为测试。
- 商店：`export-diagnostic-pack` 脱敏 zip；设置页按钮；Store 构建禁 GitHub 下载并指向 Store 更新。

## [S3] Out of Scope

拆分 room_handler/Workbench/orchestrator；遥测上报；Cookie 同意门禁重做；平台联网回归；MSIX 提审材料。

## Tasks

- [ ] T1: 写入 spec 并登记任务树 — acceptance: 文档存在且覆盖 S2 (covers: S2)
- [ ] T2: 仅提交本特性相关文件到当前分支 — acceptance: 可追溯提交且无关脏文件未混入 (covers: S2)
- [ ] T3: 验证套件全绿 — acceptance: pytest 相关用例 + vitest + 后端 start-stop 冒烟 (covers: S2)
- [ ] T4: 独立 reviewer 审查 diff — acceptance: 合规/正确/一致，critical 清零 (covers: S2; depends: T2, T3)
- [ ] T5: Finalize 文档 — acceptance: Report + delivered + commits 区间 (covers: S2; depends: T4)
