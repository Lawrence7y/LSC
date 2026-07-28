# LSC 直播切片系统 — 更新说明

## v3.0.0 (2026-07-28)

### 新增功能

- **持续分析（Valorant 回合切割）**
  - 音频能量 + 回合结束钟声分割战斗段
  - OCR（RapidOCR）识别购买阶段 / 胜负结算，校正权威边界
  - 相位调度器（buy / combat / post_combat / intermission）控制 OCR 预算
  - 录制结束后全文件 OCR 收尾精修
- **主房分析 → 副房映射**
  - 副房通过 `recording_start_mono` + `content_offset` 差值映射后 `clip_queued`
  - 映射失败时广播 `mapping_fallback`，前端 toast 提示
- **AI 回合待确认机制**
  - AI 高光默认 `confirm_status=pending`，不自动 FFmpeg 导出
  - 精修：拖时间线调入出点 → 确认 / 确认并导出
- **剪映草稿集成**
  - 分析完成后自动生成剪映草稿（`pyJianYingDraft`）
  - 草稿目录白名单安全校验
- **工作台 UI 统一**
  - 浅色 + 品牌色 `#31B3AE` 主题
  - Modal / 设置抽屉溢出修复
  - 分析进度与导出摘要
- **DVR 时间线**
  - 录制回看紫色标记左边界对齐
  - 离线文件 MSE 预览

### 优化

- **功耗优化**
  - OCR 采样间隔与相位预算，避免全时段满负荷扫帧
  - 预览路数压力感知降分辨率 / 帧率
  - 共享进样减少重复 CDN 拉流
- **性能优化**
  - 房间卡片布局简化（徽章归入头部与元数据行）
  - 录制队列位置提示
  - 预览画质降级横幅提示

### 安全修复

- WebSocket Origin + Token 双校验
- 全局状态并发锁保护
- IPC 监听器泄漏修复
- ffprobe 执行器隔离
- 输入大小限制

### Bug 修复

- 修复长时间录制稳定性问题
- 修复热路径消息断线队列策略
- 修复 `mse_init` 竞态（`request_mse_init` + `replay_init`）
- 修复录制源切换时播放器重建问题
- 修复切片导出墙钟快照精度

---

## v2.0.0 (较早版本)

### 新增功能

- 一键对齐直播间（多房间音频互相关对齐）
- 长时间录制稳定性修复

---

## v1.0.1 (2026-06-28)

### Bug 修复

- 修复安装后空白窗口问题（`app.isPackaged` 判断 + 5 秒超时检测）
- 图标改为白色背景（适配 Windows 各主题）

---

## v1.0.0 (初始版本)

- 多路同步录制（最多 12 路）
- MSE 实时预览（最多 4 路）
- 墙钟精确切片导出
- 平台适配（抖音 / B站 / 虎牙 / 快手 / 斗鱼 / 小红书 / 微博）
- 全局快捷键与设置页

---

## 安装与文档

- **下载**：[GitHub Releases](https://github.com/Lawrence7y/LSC/releases)
- **日志位置**：`%APPDATA%\lsc-electron\logs\`
- **问题反馈**：GitHub Issue
