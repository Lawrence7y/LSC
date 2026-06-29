# LSC 直播切片系统 - 安装包构建指南

## 📦 快速开始

### 前置要求

- **Node.js** (推荐 v18+): [下载链接](https://nodejs.org/)
- **npm**: 随 Node.js 一起安装
- **Python 3.10+**: 用于后端服务

### 构建步骤

#### 方法一: 使用 PowerShell 脚本(推荐)

```powershell
cd lsc-electron
.\build-installer.ps1
```

#### 方法二: 使用批处理脚本

```cmd
cd lsc-electron
build-installer.bat
```

#### 方法三: 手动执行

```bash
cd lsc-electron

# 1. 安装依赖
npm install

# 2. 编译 TypeScript
npx tsc --noEmit

# 3. 构建前端
npx vite build

# 4. 构建安装包
npx electron-builder
```

## 🎨 图标配置

项目已配置自定义应用图标,位于 `assets/icon.ico`。如需更换图标:

1. 准备一张 PNG 格式的图片(建议尺寸 ≥ 256x256)
2. 运行 `convert_icon.py` 脚本转换为 ICO 格式:
   ```bash
   python convert_icon.py
   ```
3. 重新构建安装包

## 📁 输出位置

构建完成后,安装包将生成在 `release/` 目录下:

- **Windows**: `LSC 直播切片系统 Setup X.X.X.exe`
- **安装程序类型**: NSIS (支持选择安装目录)

## ⚙️ 配置选项

### electron-builder.yml

主要配置项:

```yaml
appId: com.lsc.app           # 应用唯一标识
productName: LSC 直播切片系统 # 应用名称
icon: assets/icon.ico        # 应用图标
directories:
  output: release            # 输出目录
win:
  target: nsis               # Windows 安装程序类型
nsis:
  oneClick: false            # 非一键安装,允许选择目录
  allowToChangeInstallationDirectory: true
```

### package.json build 配置

与 `electron-builder.yml` 保持一致的配置。

## 🔧 常见问题

### Q: 构建时提示 "python not found"

**A**: 确保 Python 已安装并添加到 PATH 环境变量,或在 `extraResources/python/` 目录下放置嵌入式 Python。

### Q: 图标显示不正确

**A**: 
1. 确认 `assets/icon.ico` 文件存在且有效
2. 检查 `electron-builder.yml` 和 `package.json` 中的 icon 路径配置
3. 清除缓存后重新构建:
   ```bash
   rm -rf dist dist-electron node_modules/.cache
   npm run electron:build
   ```

### Q: 安装包体积过大

**A**: 
- 检查 `extraResources` 配置,只包含必要的资源
- 启用代码压缩和 tree-shaking
- 使用 `asar` 打包 Electron 资源

### Q: macOS/Linux 构建

当前配置仅针对 Windows。如需跨平台构建,请修改 `electron-builder.yml`:

```yaml
win:
  target: nsis
mac:
  target: dmg
linux:
  target: AppImage
```

## 📝 开发模式

开发时使用热重载:

```bash
cd lsc-electron
npm run dev
```

这将启动 Vite 开发服务器和 Electron 窗口,支持实时预览。

## 🚀 部署注意事项

1. **Python 后端**: 确保 `python-backend/` 目录完整复制到安装包
2. **FFmpeg**: 用户需单独安装 FFmpeg 或将其放入 `extraResources/ffmpeg/`
3. **libmpv**: 视频预览依赖,可选但推荐包含
4. **权限**: Windows 安装可能需要管理员权限

## 📞 技术支持

如遇问题,请查看:

- 日志文件: `%APPDATA%/lsc-electron/logs/backend.log`
- Electron 主进程日志: 控制台输出
- 前端错误: DevTools Console

---

**版本**: 1.0.0  
**最后更新**: 2026-06-28
