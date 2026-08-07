# 部署与打包规范（LSC）

> **权威参考**：以代码实际实现为准（`lsc-electron/package.json`、`lsc-electron/electron/main.ts`、`lsc-electron/scripts/prep-bundle.ps1`、`python-backend/dependency_manager.py`、`.github/workflows/*.yml`）。修正 `CLAUDE.md` §10.3.1 旧版路径描述（见 §8）。
> 命令均为 Windows PowerShell。

---

## 1. 工具链与版本要求

| 组件 | 版本要求 | 说明 |
| :--- | :--- | :--- |
| Node.js | `20.x` | CI 固定 `node-version: "20"`；无 `engines` 字段强制 |
| npm | 随 Node 20 | `npm ci` 用于干净安装 |
| Electron | `^30.5.1`（devDependency） | 不进入 dependencies |
| electron-builder | `^24.9.1` | Windows 目标仅 `nsis` |
| TypeScript | `^5.3.3` | `npx tsc --noEmit` 类型检查 |
| Vite | `^5.0.8` + `vite-plugin-electron ^1.1.0` | dev 时启动 Electron |
| Python（开发） | `>=3.10` | `pyproject.toml:5`；CI 矩阵 `3.10/3.11/3.12` |
| Python（打包） | **嵌入式 3.12.10 amd64** | `prep-bundle.ps1:11` 下载，仅 Python 本体（~60MB 安装包策略） |
| uv | latest release | 运行时按需装包（`prep-bundle.ps1:13,89-107`） |
| FFmpeg/FFprobe | 运行时下载 | 经 `dependency_manager.py` 拉取，不预打进安装包 |

环境自检：`python scripts/check_deps.py`（检查 Python 版本、FFmpeg、关键依赖）。

---

## 2. 开发模式启动（三种方式）

### 2.1 纯后端
```
cd python-backend
python main.py
```
- WebSocket 监听 `127.0.0.1:9876`，占用时依次回退 `19877–19880`。
- `$env:LSC_WS_TOKEN_REQUIRED="0"` 可免鉴权（纯开发）；默认要求 `LSC_WS_TOKEN`。

### 2.2 纯前端（连已启动的后端）
```
cd lsc-electron
npx vite --config vite.dev.config.ts
```
- dev server 端口 `5250`，`/ws` 代理到后端 `9876`。

### 2.3 一体化（推荐）
```
cd lsc-electron
npm run dev        # = vite --config vite.config.ts（vite-plugin-electron 自动拉起 main.ts）
```

---

## 3. 打包与发布

### 3.1 本地打包命令（`lsc-electron/package.json`）

| 命令 | 内容 | 用途 |
| :--- | :--- | :--- |
| `npm run build` | `tsc && vite build && electron-builder` | 别名（含 tsc 全量 emit） |
| `npm run electron:build` | `tsc --noEmit && vite build && electron-builder` | **推荐的完整打包** |
| `npm run prep-bundle` | 跑 `scripts/prep-bundle.ps1` | **必须先跑**：下载嵌入式 Python + uv 到 `lsc-electron/.bundle/` |
| `npm run build:full` | `prep-bundle.ps1 && tsc --noEmit && vite build && electron-builder` | 一键全流程 |
| `npm run release` | `vite build && electron-builder --publish always` | 本地直接发 GitHub Release |

> 内容物构建：`tsc` 产出 `dist-electron/`，`vite build` 产出 `dist/`，二者是打包 `files` 仅有的两项。

### 3.2 prep-bundle.ps1 行为（`scripts/prep-bundle.ps1`）
1. 下载 `python-3.12.10-embed-amd64.zip` 解压至 `.bundle/python`；
2. 修改 `python*._pth` 启用 site-packages（`:70-76`，`#import site` → `import site` + `Lib\site-packages`）；
3. 下载 uv release zip，提取 `uv.exe` 至 `.bundle/uv`；
4. 验证 `python.exe`、`uv.exe --version` 可运行；
5. **不再下载 Python 包与 FFmpeg**——由安装后首次运行 `dependency_manager.py` 按需拉取，以控制安装包体积。
- 输出目录：`lsc-electron/.bundle/{python,uv}`。

### 3.3 electron-builder 配置要点（`package.json:52-124`）
- `appId: com.lsc.app`、`productName: LSC 直播切片系统`、icon `assets/icon.ico`、输出 `release/`。
- `extraResources`（8 项，拷贝到 `resources/`）：
  `python-backend/`、`lsc/`、`scripts/`、`requirements.txt`、`requirements-ai.txt`、`install-runtime-dependencies.ps1`、`.bundle/python → python`、`.bundle/uv → uv`。
- Windows 目标：`nsis`（`oneClick:false`、可改安装目录、含 `build/installer.nsh`）。
- `publish`: GitHub provider（owner `Lawrence7y`，repo `LSC`，`releaseType: release`）。
- 未配置代码签名证书——安装包无签名，SmartScreen 会提示。

### 3.4 安装后首次运行
1. Electron 校验依赖（损坏/缺失 → SplashScreen 展示安装进度）。
2. `dependency_manager.py` 用 uv 安装核心 / AI / FFmpeg 依赖（GitHub API 拉版本清单、SHA-256 fail-closed 校验、防 zip-slip，进度经 IPC 上报 UI）。

---

## 4. Python 解释器定位（`electron/main.ts`）

`detectPython()` 查找顺序（`main.ts:288-331`）：
1. 打包内：`resourcesPath/python/python.exe`（开发模式不可用则忽略）；
2. 系统 PATH：`python`、`python3`；
3. WorkBuddy 自带 Python：`~/.workbuddy/binaries/python/versions/`（`main.ts:313`）。

`isPackaged` 路径映射（`main.ts:343-357`）：
- 打包：`backendRoot = resourcesPath/`，`ffmpeg = resourcesPath/ffmpeg`；
- 开发：`backendRoot = <仓库>/python-backend`，ffmpeg 走 PATH。

---

## 5. 运行时目录与环境变量

### 5.1 关键目录（Electron 注入）
| 变量 | 值 | 说明 |
| :--- | :--- | :--- |
| `LSC_DATA_DIR` | `<userData>`（如 `%APPDATA%/lsc-electron`） | **后端全部数据根**（settings/rooms/logs） |
| `LSC_LOG_DIR` | `<userData>/logs` | Python 日志（含 stdout/stderr 转储） |
| `LSC_RUNTIME_DIR` | `<userData>/runtime` | 运行时下载区（FFmpeg 等） |
| `LSC_PYTHON_PACKAGES` | `<userData>/runtime/packages` | uv target 目录，也是 `PYTHONPATH` 首项 |
| `LSC_REQUIREMENTS_DIR` | 打包内 `resources/` 或仓库根 | requirements.txt 所在 |
| `LSC_BUNDLED_FFMPEG_DIR` | 打包:`resources/ffmpeg`；开发:`''` | FFmpeg 查找首选（`lsc/config.py` `_find_executable`） |
| `LSC_CONFIG_PATH` | `<userData>/lsc_config.json`（可被外部覆盖/传入） | LscConfig 覆盖文件 |

### 5.2 进程与环境变量
| 变量 | 值 | 说明 |
| :--- | :--- | :--- |
| `LSC_WS_TOKEN` | Electron 生成的随机 token | 注入后端进程 env，与渲染层传递值一致 |
| `LSC_WS_TOKEN_REQUIRED` | `'1'`（打包恒置） | 后端强制鉴权 |
| `LSC_PARENT_PID` | Electron `process.pid` | 后端存活监控 |
| `LSC_LOG_LEVEL` / `LSC_LOG_DEBUG_LOGGERS` | 透传 | 日志级别调试（见 CLAUDE.md 日志规范） |
| `LSC_VALORANT_MODEL_DIR` / `LSC_VALORANT_VISION_SHADOW` | 透传 | 帧分析扩展开关 |
| `PYTHONPATH` | packages 目录 + 原值 | 后端 import 路径 |

后端进程由 Electron spawn（env 白名单传递，`main.ts:706-738`），崩溃自动拉起并重连。

---

## 6. CI 流水线（`.github/workflows/`）

### 6.1 ci.yml（push main/master 或 `v*` tag，PR）
| Job | Runner | 内容 |
| :--- | :--- | :--- |
| `lint` | ubuntu | ruff `lsc/ python-backend/`、mypy；scripts 仅 informational |
| `electron-typecheck` | ubuntu（Node 20） | `npm ci` + `npx tsc --noEmit` |
| `frontend-test` | ubuntu（Node 20） | `npx vitest run` |
| `test` | 矩阵 ubuntu/windows × py3.10/3.11/3.12 | 装 ffmpeg、`pytest -v --cov`（`QT_QPA_PLATFORM=offscreen`） |
| `release` | **windows-latest**，仅 `v*` tag | `vite build && electron-builder --publish always`（`GH_TOKEN`=GITHUB_TOKEN） |

### 6.2 core-guard.yml
- `CODEOWNERS`（`@project-core`）保护的路径变更 → 需项目核心成员 approve；含 `core-guard` 检查。

### 6.3 版本号与发布
- 版本号：`lsc-electron/package.json` `version`（当前 `3.0.20`）；打 `v*` tag 即触发 release。
- GitHub Releases 自动生成（releaseType `release`，经 `latest.yml` 提供增量更新）。

> ⚠️ 已发现：`ci.yml` 的 `release` job **未执行 `npm run prep-bundle`**——publish 前必须确保 `.bundle/{python,uv}` 已存在于工作区/仓库（或本地先行跑过 prep-bundle），否则 `extraResources` 引用会解析失败。

---

## 7. 跨进程部署注意点

1. 三进程模型：Electron 主进程（Node 20）↔ Python 后端（>=3.10/site-packages）↔ Vite 前端；版本不强制对齐。
2. 数据迁移：所有持久化路径锚定 `LSC_DATA_DIR`，需随 userData 目录整体迁移。
3. Windows 独占：目标仅 `nsis`，FFmpeg 下载与 nvenc 推断均按 win32 路径实现。
4. 依赖一致性：`requirements.txt`/`requirements-ai.txt` 与 `lsc_config.json` 允许键之间无版本耦合；新增依赖须双写 requirements + dependency_manager 清单。

---

## 8. 与 CLAUDE.md 的差异（以本文件为准）

| # | CLAUDE.md 说法 | 代码实际 |
| :--- | :--- | :--- |
| 1 | §10.3.1 描述的后端路径映射 | 以 `main.ts:343-357` `app.isPackaged` 分支为准：打包 `resourcesPath`、开发仓库根 |
| 2 | §10.3.2 描述的"打包内 Python 附带全部依赖" | 嵌入式 Python **仅本体**，依赖由 `dependency_manager.py` 运行时下载（安装包 ~60MB 策略） |
| 3 | `npm run dev` 启动 | 即 `vite --config vite.config.ts`（vite-plugin-electron），非独立 Electron 命令 |
| 4 | build 未提 `prep-bundle` 前置 | 必须先（或 `build:full`）生成 `.bundle/` |