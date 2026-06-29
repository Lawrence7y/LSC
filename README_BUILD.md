# LSC 直播切片系统 - 快速开始

## 🚀 快速安装

### Windows用户

1. **下载安装包**
   - 位置: `lsc-electron/release/LSC 直播切片系统 Setup 1.0.0.exe`
   - 大小: ~84 MB

2. **运行安装程序**
   ```
   双击安装包 → 选择安装目录 → 点击安装 → 完成
   ```

3. **启动应用**
   - 桌面快捷方式(如果创建)
   - 或从开始菜单启动

### 开发者/自定义构建

```bash
cd lsc-electron

# 开发模式(热重载)
npm run dev

# 生产构建
.\build-installer.ps1
```

详细文档请查看: [lsc-electron/BUILD.md](lsc-electron/BUILD.md)

## 📋 前置要求

- **Windows 10/11** (64位)
- **Python 3.10+** (用于后端服务)
- **FFmpeg** (可选,用于视频处理)

## 🎨 特性

- ✅ 多直播间同时录制
- ✅ 智能切片功能
- ✅ 视频预览与编辑
- ✅ 多平台支持(抖音、虎牙等)
- ✅ 自定义应用图标
- ✅ 托盘最小化
- ✅ 开机自启动(可选)

## 📁 项目结构

```
直播切片多人/
├── lsc-electron/           # Electron前端应用
│   ├── assets/             # 应用资源(图标等)
│   ├── release/            # 生成的安装包
│   ├── build-installer.ps1 # 构建脚本
│   ── BUILD.md            # 构建文档
── python-backend/         # Python后端服务
── lsc/                    # LSC核心库
├── docs/                   # 文档
└── tests/                  # 测试代码
```

## 🔧 常见问题

### Q: 安装后无法启动?

**A**: 确保已安装Python 3.10+并添加到PATH环境变量

### Q: 录制失败?

**A**: 
1. 检查网络连接
2. 确认直播间URL正确
3. 查看日志: `%APPDATA%/LSC/logs/backend.log`

### Q: 如何更换应用图标?

**A**: 
1. 准备PNG图片(≥256x256像素)
2. 替换 `extracted_icon.png`
3. 运行: `python convert_icon.py`
4. 重新构建: `cd lsc-electron && .\build-installer.ps1`

## 📞 技术支持

- 日志位置: `%APPDATA%/LSC/logs/`
- 问题反馈: 查看 [docs/](docs/) 目录中的相关文档

## 📝 许可证

本项目仅供学习和研究使用。

---

**版本**: 1.0.0  
**更新日期**: 2026-06-28  
**构建状态**: ✅ 成功
