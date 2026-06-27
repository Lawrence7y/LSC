# LSC Electron 版本

这是 LSC 直播切片系统的 Electron + React 前端。

## 启动方式

需要先启动 Python WebSocket 后端，再启动 Electron 前端。

### 1. 安装依赖

```bash
# 安装前端依赖
cd lsc-electron
npm install

# 安装后端依赖（在项目根目录）
cd ..
pip install -r requirements.txt
```

### 2. 启动 Python 后端

```bash
cd python-backend
python main.py
```

后端默认监听 `ws://localhost:8765`。

### 3. 启动 Electron 桌面端

```bash
cd lsc-electron
npm run dev
```

开发模式会同时启动 Vite 开发服务器和 Electron 窗口。

### 4. 仅浏览器模式

如果你只想在浏览器中调试 UI：

```bash
cd lsc-electron
npm run preview
```

然后打开输出的本地 URL。注意浏览器模式无法使用 `selectDirectory` 等 Electron 原生 API。

## 构建

```bash
cd lsc-electron
npm run build
```

## 目录结构

- `electron/` - Electron 主进程与预加载脚本
- `src/` - React 前端源码
- `python-backend/` - Python WebSocket 后端（与 Qt 版复用核心逻辑）
