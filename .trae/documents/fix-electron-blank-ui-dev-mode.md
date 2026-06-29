# 修复 Electron 程序白屏问题（开发模式启动）

## 摘要

程序启动后 UI 一片空白，只有窗口框架无内容。根因是 Electron 主进程在生产模式下加载 `dist/index.html`，但前端 React 应用从未用 Vite 构建过，`dist/` 目录不存在。

采用开发模式启动（`npm run dev`），由 vite-plugin-electron 同时拉起 Vite dev server 与 Electron 窗口，前端通过 `VITE_DEV_SERVER_URL` 热加载。

## 根因分析

### 现象
- Electron 窗口打开后为空白
- `_electron_stdout.txt` 显示 Python 后端正常启动：`[spawnBackend] backend WebSocket URL: ws://localhost:9876`
- `_electron_stderr.txt` 只有 GPU 缓存权限告警，无致命错误

### 关键代码路径
1. [lsc-electron/electron/main.ts](file:///d:\Project\直播切片多人\lsc-electron\electron\main.ts) 第 486-493 行：
   ```ts
   if (process.env.VITE_DEV_SERVER_URL) {
     mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL)
   } else {
     mainWindow.loadFile(path.join(__dirname, '../../dist/index.html'))
   }
   ```
2. [lsc-electron/_run_electron.bat](file:///d:\Project\直播切片多人\_run_electron.bat) 直接执行 `electron dist-electron/main/main.js`，没有设置 `VITE_DEV_SERVER_URL`，走的是生产分支。
3. [lsc-electron/index.html](file:///d:\Project\直播切片多人\lsc-electron\index.html) 引用 `/src/main.tsx`，是 Vite dev 入口，需要 dev server 转译。
4. `lsc-electron/dist/` 目录经 Glob 确认**不存在** → `loadFile` 找不到 `dist/index.html` → 白屏。

### 后端状态
- Python 后端独立启动正常（PID 41380，ws://localhost:9876），非本次问题原因。

## 修复方案

### 步骤 1：清理遗留进程
当前已有 5 个 electron 进程 + 1 个 python 进程在跑（来自上一次失败启动），占用端口与文件锁，需先终止：
```powershell
Get-Process -Name electron,python -ErrorAction SilentlyContinue | Stop-Process -Force
```

### 步骤 2：以开发模式启动
进入 `lsc-electron` 目录执行：
```powershell
cd D:\Project\直播切片多人\lsc-electron
npm run dev
```
- `npm run dev` 脚本对应 `vite --config vite.config.ts`
- [lsc-electron/vite.config.ts](file:///d:\Project\直播切片多人\lsc-electron\vite.config.ts) 配置了 `vite-plugin-electron`，其中：
  - electron main 入口的 `onstart: (options) => options.startup()` 会在 Vite 就绪后自动拉起 Electron 主进程
  - preload 入口的 `onstart: (options) => options.reload()` 改动时热重载
- Vite 启动后会设置 `VITE_DEV_SERVER_URL`（默认 http://localhost:5173），main.ts 检测到后走 `loadURL` 分支，前端正常加载

### 步骤 3：验证
- 窗口打开后应显示登录/工作台界面（不再白屏）
- DevTools 自动打开（main.ts 第 488 行 `openDevTools()`），Console 无红色错误
- 后端 WebSocket URL 在 stdout 日志中可见

## 注意事项

1. **必须在 Trae IDE 外运行**：项目 memory 约束，IDE 沙箱会导致子进程权限问题。本次仍通过 RunCommand 启动，若出现权限错误需用户手动在终端执行。
2. **dev server 端口冲突**：若 5173 被占用，Vite 会自动递增到 5174 等，但 electron 读取的 `VITE_DEV_SERVER_URL` 会同步更新，无需手动处理。
3. **后端重复启动**：dev 模式下 Electron 主进程会再次 spawnBackend，但上一步已清理旧 python 进程，端口 9876 已释放。
4. **不要再用 `_run_electron.bat`**：该脚本走生产分支，仅适用于 `npm run build` 之后的产物直跑场景。

## 验证步骤

- [ ] 旧进程已全部终止
- [ ] `npm run dev` 输出 Vite ready 与 electron 启动日志
- [ ] Electron 窗口显示正常 UI（非白屏）
- [ ] DevTools Console 无未捕获异常
- [ ] 后端 WebSocket 连接成功（控制台可见 ws 连接日志）
