# Tasks

- [x] Task 1: Electron 主进程集成 Python 后端
  - [x] SubTask 1.1: 在 `electron/main.ts` 中实现 `spawnBackend()`：用 `child_process.spawn` 拉起 `python-backend/main.py`，捕获 stdout/stderr 写入 `app.getPath('userData')/logs/backend.log`
  - [x] SubTask 1.2: 在 `app.whenReady()` 中调用 `spawnBackend()`，并延迟创建 BrowserWindow（或先创建 loading 窗口，后端就绪后加载主界面）
  - [x] SubTask 1.3: 实现 `killBackend()`：Windows 下 `taskkill /T /PID`，POSIX 下 `SIGTERM`，等待 3 秒未退出则 `SIGKILL`
  - [x] SubTask 1.4: 在 `before-quit` 事件中调用 `killBackend()`，确保无孤儿进程
  - [x] SubTask 1.5: 处理开发/打包环境路径差异（开发用相对路径，打包用 `process.resourcesPath`）

- [x] Task 2: 房间预览架构改造
  - [x] SubTask 2.1: `RoomCard.tsx` 移除 `<video>` 元素及 `onError` 空函数，未启用预览时显示 play-overlay 图标 + "启用预览"按钮
  - [x] SubTask 2.2: `RoomCard.tsx` 接收 `onTogglePreview` 回调，按钮点击时调用
  - [x] SubTask 2.3: `Workbench/index.tsx` 实现 `handleTogglePreview(roomId, enabled)`，通过 WebSocket 发送 `enable_preview` 消息
  - [x] SubTask 2.4: `useWebSocket.ts` 确认已处理 `rooms_updated` 中的 `preview_enabled` 字段（后端广播已含），store 自动同步
  - [x] SubTask 2.5: 预览已启用时，预览区显示"预览已启用（mpv 渲染中）"占位文案，避免黑屏误导

- [x] Task 3: 通用设置实际生效
  - [x] SubTask 3.1: `types/index.ts` 扩展 `AppSettings` 类型，增加 `theme: 'dark'|'light'`、`language: 'zh'|'en'`、`autoLaunch: boolean`、`minimizeToTray: boolean`
  - [x] SubTask 3.2: `tokens.css` 增补 `:root:not(.dark)` / `html:not(.dark)` 下的 light 主题 CSS 变量
  - [x] SubTask 3.3: `MainLayout.tsx` 侧边栏底部增加主题切换按钮（太阳/月亮图标），点击切换 `document.documentElement` 的 `dark` class 并调用 store 持久化
  - [x] SubTask 3.4: `preload.ts` 暴露 `window.app.setAutoLaunch(bool)` / `window.app.getMinimizeToTray()` 等 IPC API
  - [x] SubTask 3.5: `main.ts` 实现 `app.setLoginItemSettings({ openAtLogin })` 的 IPC handler
  - [x] SubTask 3.6: `main.ts` 实现最小化到托盘：`Tray` 模块 + 右键菜单"显示/退出"，`window.on('close')` 拦截
  - [x] SubTask 3.7: `Settings/index.tsx` 通用设置区域接入实际逻辑：主题切换、autoLaunch 调用 IPC、minimizeToTray 调用 IPC，保存时发送 `settings` 消息持久化到后端 `settings.json`

- [x] Task 4: 验证
  - [x] SubTask 4.1: `npx tsc --noEmit` 通过
  - [x] SubTask 4.2: `npm run build` 通过
  - [ ] SubTask 4.3: 用户视角验证：启动 exe → 后端自动启动 → 添加房间 → 连接 → 启用预览 → 切换主题 → 开启最小化到托盘 → 关闭窗口验证托盘 → 退出验证无孤儿进程

- [x] Task 5: 验证发现的缺陷修复
  - [x] SubTask 5.1: 修复 theme/language 重启不恢复：`useWebSocket.ts` 的 `handleSettings` 解构 `data.appSettings` 调用 `setAppSettings`
  - [x] SubTask 5.2: 修复打包后缺失 python-backend：`package.json` 的 `build` 配置增加 `extraResources`，将 `../python-backend` 打包到安装包 `python-backend` 目录

# Task Dependencies

- Task 2 依赖 Task 1（后端需先启动，WebSocket 才能连接）
- Task 3 的 SubTask 3.7 依赖 SubTask 3.4-3.6（IPC 与 Tray 需先就绪）
- Task 4 依赖 Task 1-3 全部完成
- Task 1、Task 3 的 SubTask 3.1-3.3 可并行（主进程改造与前端类型/主题变量无强依赖）
- Task 5 依赖 Task 4（验证发现问题后修复）
