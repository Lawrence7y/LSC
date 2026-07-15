# LSC Electron 前端

LSC 直播切片系统的 Electron + React 桌面前端。后端为仓库根目录的 `python-backend/`（WebSocket 默认 `ws://localhost:9876`）。

完整产品说明、架构与功能清单见根目录 [README.md](../README.md)。

## 开发启动

```bash
# 推荐：一键拉起后端 + Electron
cd lsc-electron
npm install
npm run dev
```

或分别启动：

```bash
# 终端 1：Python 后端
cd python-backend
python main.py

# 终端 2：Electron
cd lsc-electron
npm run dev
```

仅浏览器调 UI（无 Electron 原生 API）：

```bash
npx vite --config vite.dev.config.ts
```

## 构建安装包

```powershell
.\build-installer.ps1
```

## 目录

- `electron/` — 主进程、托盘、Python 生命周期、更新检测
- `src/pages/` — Dashboard / 工作台 / 设置
- `src/components/` — 预览、时间线、分析进度、导出队列
- `src/services/` — WebSocket、MSE 播放器
- `scripts/prep-bundle.ps1` — 嵌入式 Python + FFmpeg
