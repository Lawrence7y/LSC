# LSC 直播切片系统

一款专注于多直播间同步录制与快速切片的工具。

## 核心功能

- 支持最多 12 路并发录制
- 最多 4 路实时预览（基于 libmpv）
- 使用 FFmpeg 实现片段导出（直拷贝/转码）
- 支持抖音、B站、虎牙、直链等平台
- 提供多房间工作台与单房间录制双视图

## 技术栈

- **前端**: React 18 + TypeScript + Ant Design + Vite
- **桌面框架**: Electron 28
- **后端**: Python 3.10+ + WebSocket
- **视频处理**: FFmpeg
- **平台适配**: Protocol + Registry 模式

## 项目结构

```
├── lsc/                  # Python 核心模块
├── python-backend/       # Electron 后端（WebSocket 服务）
├── lsc-electron/         # Electron 前端应用
├── scripts/              # 辅助脚本
├── data/                 # 数据文件
└── .github/workflows/    # CI/CD 配置
```

## 开发环境

### 前置要求

- Python 3.10+
- Node.js 18+
- FFmpeg

### 安装依赖

```bash
pip install -r requirements.txt
cd lsc-electron && npm install
```

### 启动开发服务器

```bash
cd lsc-electron && npm run dev
```

### 构建安装包

```bash
cd lsc-electron && npm run build
```

## 发布流程

1. 修改 lsc-electron/package.json 中的版本号
2. 创建 git tag: git tag v1.x.x
3. 推送 tag: git push origin v1.x.x
4. GitHub Actions 自动构建并发布到 Releases

## License

MIT
