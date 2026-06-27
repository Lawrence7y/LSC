# LSC 项目核心决策记录

## 架构决策

### 2026-06-26: PySide6 已弃用，Electron 为唯一前端
- 决策人: 老大
- 原因: Electron 更适合分发和跨平台
- 影响: 所有 UI 功能仅在 lsc-electron/ 中开发，lsc/gui/ 不再维护

### 2026-06-26: 视频预览升级方案 — MSE + fMP4
- 当前 JPEG 帧流 5-10fps 无法满足需求
- 选择方案 A: Media Source Extensions + FFmpeg fMP4 转码
- 目标: 单路预览 25-30fps，有音频，可精准 seek
- 实施优先级: P0（阻塞所有后续功能）

## 需求覆盖目标

| 需求 | 当前 | 目标 |
|------|------|------|
| 1. 直播链接识别 | 95% | 98% |
| 2. 预览与控制 | 40% | 95% |
| 3. 多房间管理 | 65% | 95% |
| 4. 进度条与导出 | 60% | 95% |
| 5. 播放器与时间线 | 55% | 90% |
| 6. 视频规格设置 | 50% | 95% |
| 7. 交互体验 | 60% | 95% |

## 开发规范
- WebSocket 默认端口: **9876**（原 8765 在 Windows 上被系统占用）
- 可通过 VITE_WS_URL 环境变量覆盖前端 WS 地址
- 后端支持端口回退（9876 → 9877 → 9878 → 9879 → 9880）
- 所有前端代码在 lsc-electron/src/ 下
- 后端新 handler 在 python-backend/handlers/ 下
- 核心业务逻辑共享在 lsc/ 下
- TypeScript 类型先定义在 types/index.ts
- Zustand store 更新通过 useAppStore
- WebSocket 通信通过 useWebSocket hook
