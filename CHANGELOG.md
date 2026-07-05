# 更新说明 v1.0.1

## 🐛 Bug修复 (2026-06-28)

### 关键修复 #1: 空白窗口问题增强
- 🔧 **进一步优化安装后空白窗口问题**
  - 问题: 安装后程序显示空白,无UI界面
  - 原因: 前端资源加载路径错误(手动拼接asar路径)
  - 解决: 使用`app.isPackaged`判断环境,统一使用相对路径,让Electron自动从asar中提取文件
  - **新增增强功能**:
    - ✅ 添加详细日志输出(`[createWindow]`)
    - ✅ 添加错误捕获和提示对话框
    - ✅ 添加5秒超时检测,强制显示窗口
  - 影响: ⭐⭐⭐⭐⭐ (高优先级,阻塞性问题)
  - 文件: `lsc-electron/electron/main.ts` (第490-535行)
  - 状态: ✅ 已修复并重新打包

### 关键修复 #2: 图标背景优化
- 🎨 **图标改为白色背景**
  - 问题: 透明背景图标在某些Windows主题下显示不佳
  - 解决: 使用sharp库将透明背景转换为白色背景
  - 保持: 6种尺寸(16/32/48/64/128/256px)
  - 工具: 
    - `convert_icon_white_bg.py` (Python版本)
    - `convert_icon_white_bg.js` (Node.js版本)
  - 状态: ✅ 已完成

## 📦 安装包信息

- **版本**: 1.0.1
- **大小**: 84.05 MB
- **架构**: Windows x64
- **构建日期**: 2026-06-28 19:33
- **Electron**: 28.3.3

##  快速开始

### 新用户
1. 下载 `LSC 直播切片系统 Setup 1.0.0.exe`
2. 双击运行安装程序
3. 选择安装目录
4. 启动应用开始使用

### 已安装用户
1. 卸载旧版本(如果有)
2. 删除残留数据: `%APPDATA%\LSC\`
3. 安装新版本
4. 重新配置设置

## 📋 前置要求

- ✅ Windows 10/11 (64位)
- ✅ Python 3.10+ (用于后端服务)
- ️ FFmpeg (可选,用于视频处理)

## 📖 文档

- [快速开始指南](README_BUILD.md)
- [测试指南](TESTING_GUIDE.md)
- [构建文档](lsc-electron/BUILD.md)
- [项目交付总结](PROJECT_DELIVERY_SUMMARY.md)

## 🔧 开发者

### 重新构建
```powershell
cd lsc-electron
.\build-installer.ps1
```

### 更换图标
```bash
# 准备PNG图片(≥256x256像素)
python convert_icon.py
cd lsc-electron
.\build-installer.ps1
```

## ❓ 常见问题

### Q: 安装后仍然是空白窗口?
**A**: 请确保安装了最新版本(v1.0.0),旧版本有此问题。如仍有问题:
1. 卸载旧版本
2. 删除 `%APPDATA%\LSC\` 目录
3. 重新安装新版本

### Q: 图标显示不正确?
**A**: 
1. 确认使用的是v1.0.0或更高版本
2. 清除缓存重新安装
3. 查看日志文件排查问题

### Q: Python后端未启动?
**A**: 
1. 确保Python 3.10+已安装并添加到PATH
2. 查看日志: `%APPDATA%\LSC\logs\backend.log`
3. 手动测试: `cd python-backend && python main.py`

## 📞 技术支持

- **日志位置**: `%APPDATA%\LSC\logs\`
- **问题反馈**: 提交GitHub Issue
- **文档查看**: docs/ 目录

##  致谢

感谢所有参与测试和反馈的用户!

---

**发布日期**: 2026-06-28  
**维护团队**: LSC开发团队  
**许可证**: 仅供学习和研究使用
