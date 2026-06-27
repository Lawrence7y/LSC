# Electron 核心运行时修复 Spec

## Why
当前 Electron 程序无法独立可用：主进程不启动 Python 后端、房间预览架构错误（浏览器 `<video>` 无法播放 FLV/HLS 直播流）、通用设置（主题/开机自启/最小化到托盘）只是前端 state 不生效。这导致用户报告的"创建房间后点击连接按键无效"等问题——核心链路断裂，必须先修复才能谈 UI 对齐。

## What Changes
- Electron 主进程在 `app.whenReady()` 时拉起 `python-backend/main.py` 子进程，并捕获 stdout/stderr 写入日志文件；退出时优雅终止（SIGTERM → 超时强杀）。
- RoomCard 移除 `<video src={stream_url}>` 直播播放，改为占位 play-overlay + "启用预览"按钮，通过 `enable_preview` 通道由后端 mpv 渲染预览。
- 前端 Workbench 接入 `enable_preview` 消息发送，`useWebSocket` 监听预览状态更新，`preview_enabled` 字段真正生效。
- 通用设置实际生效：主题切换（tokens.css 增补 light 主题变量 + MainLayout 切换按钮）、开机自启（`app.setLoginItemSettings`）、最小化到托盘（Electron `Tray`），并持久化到后端 `settings.json`。

## Impact
- 受影响代码：
  - `lsc-electron/electron/main.ts`：新增 Python 子进程管理、Tray、开机自启
  - `lsc-electron/electron/preload.ts`：暴露 theme/autoLaunch/tray API
  - `lsc-electron/src/pages/Workbench/components/RoomCard.tsx`：预览架构改造
  - `lsc-electron/src/pages/Workbench/index.tsx`：接入 enable_preview
  - `lsc-electron/src/hooks/useWebSocket.ts`：监听 preview 相关消息
  - `lsc-electron/src/pages/Settings/index.tsx`：通用设置接入实际逻辑
  - `lsc-electron/src/store/appStore.ts`：扩展 settings 类型
  - `lsc-electron/src/styles/tokens.css`：增补 light 主题变量
  - `lsc-electron/src/components/Layout/MainLayout.tsx`：主题切换按钮
  - `lsc-electron/src/types/index.ts`：扩展 RecordSettings/AppSettings 类型
- 不修改后端 Python 代码（`enable_preview` 处理器已存在）。
- 不影响路由、不破坏现有 store 数据结构（仅扩展字段）。

## ADDED Requirements

### Requirement: Python 后端自动启动
The system SHALL 在 Electron 主进程启动时自动拉起 Python 后端子进程，无需用户手动运行。

#### Scenario: 用户启动 Electron 应用
- **WHEN** 用户双击运行打包后的 exe 或开发环境 `npm run dev`
- **THEN** Electron 主进程在 `app.whenReady()` 后 spawn `python-backend/main.py`
- **AND** 后端 stdout/stderr 被写入 `%APPDATA%/LSC/logs/backend.log`
- **AND** 前端 WebSocket 客户端能在 5 秒内成功连接 `ws://localhost:8765`

#### Scenario: 用户退出 Electron 应用
- **WHEN** 用户关闭所有窗口或触发退出
- **THEN** 主进程向 Python 子进程发送 SIGTERM（Windows 下使用 taskkill /T /PID）
- **AND** 等待最多 3 秒，若未退出则强制终止
- **AND** 不留孤儿 Python 进程

### Requirement: 房间预览由后端驱动
The system SHALL 房间预览画面由后端 mpv 渲染，前端不再尝试用浏览器 `<video>` 直接播放直播流。

#### Scenario: 用户查看未启用预览的房间
- **WHEN** 房间未连接或未启用预览
- **THEN** RoomCard 预览区显示 play-overlay 图标 + "启用预览"按钮
- **AND** 不渲染任何 `<video>` 元素

#### Scenario: 用户启用预览
- **WHEN** 用户点击"启用预览"按钮
- **THEN** 前端发送 `enable_preview` 消息（`{ room_id, enabled: true }`）
- **AND** 后端调用 `manager.start_preview(room_id)` 启动 mpv 渲染
- **AND** 前端 `preview_enabled` 状态更新为 true，预览区显示"预览已启用"占位（实际画面推送机制不在本 spec 范围）

#### Scenario: 用户禁用预览
- **WHEN** 用户再次点击预览按钮
- **THEN** 前端发送 `enable_preview` 消息（`{ room_id, enabled: false }`）
- **AND** 后端调用 `manager.stop_preview(room_id)`
- **AND** 前端 `preview_enabled` 状态更新为 false

### Requirement: 通用设置实际生效
The system SHALL 设置页的"通用设置"区域（主题、语言、开机自启、最小化到托盘）修改后实际生效并持久化。

#### Scenario: 用户切换主题
- **WHEN** 用户在设置页选择"浅色"主题
- **THEN** `document.documentElement` 移除 `dark` class，应用 light 主题 CSS 变量
- **AND** MainLayout 侧边栏底部显示主题切换按钮，可快速切换
- **AND** 设置保存后持久化到后端 `settings.json`，重启后恢复

#### Scenario: 用户开启开机自启
- **WHEN** 用户打开"开机自启"开关并保存
- **THEN** 主进程调用 `app.setLoginItemSettings({ openAtLogin: true })`
- **AND** 设置持久化，重启后仍为开启状态

#### Scenario: 用户开启最小化到托盘
- **WHEN** 用户打开"最小化到托盘"开关并保存
- **THEN** 关闭窗口时程序不退出，最小化到系统托盘
- **AND** 托盘图标右键菜单提供"显示"/"退出"选项
- **AND** 设置持久化，重启后仍为开启状态

## MODIFIED Requirements

无

## REMOVED Requirements

### Requirement: 浏览器 video 直接播放直播流
**Reason**: 浏览器 `<video>` 标签无法播放 FLV/HLS 等直播流格式，预览永远黑屏。
**Migration**: 改为后端 mpv 渲染 + `enable_preview` 通道控制，前端只显示状态占位。
