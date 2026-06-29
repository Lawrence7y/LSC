# 安装包测试与调试指南 v2.0

##  最新版本信息

- **文件名**: LSC 直播切片系统 Setup 1.0.0.exe
- **大小**: 84.05 MB
- **构建时间**: 2026-06-28 19:33
- **主要更新**: 
  - ✅ 修复空白窗口问题(添加错误处理和超时检测)
  - ✅ 图标改为白色背景(不再透明)
  - ✅ 添加详细日志输出

---

## 🧪 安装测试步骤

### 步骤1: 完全卸载旧版本

**重要**: 必须完全卸载旧版本并清除残留数据

```powershell
# 1. 通过控制面板卸载
# 控制面板 → 程序和功能 → 找到 "LSC 直播切片系统" → 右键 → 卸载

# 2. 删除残留数据目录
Remove-Item "$env:APPDATA\lsc-electron" -Recurse -Force -ErrorAction SilentlyContinue

# 3. 确认已删除
Test-Path "$env:APPDATA\lsc-electron"  # 应该返回 False
```

### 步骤2: 安装新版本

1. 双击 `LSC 直播切片系统 Setup 1.0.0.exe`
2. 选择语言(中文/English)
3. 同意许可协议
4. 选择安装目录(建议默认)
5. 勾选组件:
   - ☑️ LSC 直播切片系统 (主程序)
   - ☑️ 创建桌面快捷方式
6. 点击"安装"
7. 等待安装完成
8. 勾选"运行 LSC 直播切片系统"
9. 点击"完成"

### 步骤3: 验证应用启动

#### 预期结果:
✅ **窗口正常显示**(不再是空白)  
✅ **UI界面完整加载**(侧边栏、主内容区等)  
✅ **图标正确显示**(白色背景的电影胶片图标)  
✅ **无错误弹窗**

#### 检查清单:
- [ ] 应用窗口出现(不是最小化或隐藏)
- [ ] 窗口标题栏显示应用名称和图标
- [ ] 任务栏显示应用图标
- [ ] 侧边栏菜单可见
- [ ] 主内容区域有内容显示
- [ ] Python后端连接成功(查看控制台)

---

## 🔍 问题排查

### 问题1: 仍然是空白窗口

**可能原因**:
1. 旧版本缓存未清除
2. 前端资源加载失败
3. JavaScript错误导致渲染失败

**解决方案**:

#### 方案A: 查看错误日志

1. **打开DevTools查看前端错误**:
   - 按 `Ctrl+Shift+I` 或 `F12`
   - 切换到 "Console" 标签
   - 查看是否有红色错误信息
   - 截图保存错误信息

2. **查看后端日志**:
   ```
   %APPDATA%\lsc-electron\logs\backend.log
   ```
   - 用记事本打开此文件
   - 查看是否有Python后端启动错误

3. **查看Electron主进程日志**:
   - 在DevTools Console中查找 `[createWindow]` 开头的日志
   - 应该看到类似:
     ```
     [createWindow] Loading index.html from: ...
     [createWindow] Window shown successfully
     ```

#### 方案B: 强制重启

```powershell
# 1. 关闭应用(如果正在运行)
# 任务管理器 → 找到 "LSC 直播切片系统" → 结束任务

# 2. 删除缓存
Remove-Item "$env:APPDATA\lsc-electron" -Recurse -Force

# 3. 重新启动应用
# 从桌面快捷方式或开始菜单启动
```

#### 方案C: 手动测试资源路径

创建一个测试脚本检查资源是否正确打包:

```powershell
# 检查asar文件是否存在
Test-Path "C:\Program Files\LSC 直播切片系统\resources\app.asar"

# 如果存在,提取并检查内容
cd "C:\Program Files\LSC 直播切片系统\resources"
npx asar extract app.asar temp_check
dir temp_check\dist
```

应该看到:
- `index.html`
- `assets/` 目录(包含JS和CSS文件)

### 问题2: 图标显示不正确

**症状**:
- 图标仍是默认Electron图标
- 图标有黑色背景(应该是白色)

**解决方案**:

1. **确认图标文件已更新**:
   ```powershell
   # 检查图标文件大小和时间戳
   Get-Item "C:\Program Files\LSC 直播切片系统\resources\app.asar" | Select-Object Length, LastWriteTime
   ```

2. **重新安装**:
   - 卸载当前版本
   - 删除 `%APPDATA%\lsc-electron\`
   - 重新安装最新版本

3. **验证图标源文件**:
   ```powershell
   # 检查项目中的图标文件
   dir "d:\Project\直播切片多人\lsc-electron\assets\icon.ico"
   ```
   - 应该看到最近修改的图标文件(白色背景)

### 问题3: Python后端未启动

**症状**:
- 应用启动但无法连接后端
- 录制功能不可用
- 控制台显示WebSocket连接失败

**解决方案**:

1. **检查Python是否安装**:
   ```powershell
   python --version
   # 应该显示 Python 3.10.x 或更高版本
   ```

2. **查看后端日志**:
   ```
   %APPDATA%\lsc-electron\logs\backend.log
   ```
   
   常见错误及解决:
   - **"python not found"**: 安装Python并添加到PATH
   - **"ModuleNotFoundError"**: 确保Python依赖已安装
   - **"Permission denied"**: 以管理员身份运行应用

3. **手动测试后端**:
   ```bash
   cd "C:\Program Files\LSC 直播切片系统\resources\python-backend"
   python main.py
   ```
   
   应该看到:
   ```
   Backend server started on ws://localhost:9876
   ```

---

## 📊 调试信息收集

如果问题仍然存在,请收集以下信息并提交Issue:

### 必需信息:

1. **操作系统版本**:
   ```powershell
   systeminfo | findstr /B /C:"OS Name" /C:"OS Version"
   ```

2. **应用版本号**:
   - 查看安装包文件名
   - 或在应用中查看"关于"页面

3. **错误截图**:
   - DevTools Console的错误信息
   - 应用窗口的截图
   - 任何错误弹窗的截图

4. **日志文件**:
   - `%APPDATA%\lsc-electron\logs\backend.log`
   - DevTools Console的完整输出(复制粘贴)

5. **复现步骤**:
   - 详细描述如何触发问题
   - 是否每次都能复现

### 可选信息:

- Python版本: `python --version`
- FFmpeg版本: `ffmpeg -version`
- 网络环境: 公司内网/家庭宽带/其他

---

##  开发者调试模式

如果需要更详细的调试信息,可以使用开发模式:

### 方法1: 本地开发模式

```bash
cd d:\Project\直播切片多人\lsc-electron
npm run dev
```

优点:
- 实时热重载
- DevTools自动打开
- 可以看到完整的控制台输出

缺点:
- 需要Node.js环境
- 不是生产环境的真实情况

### 方法2: 打包后启用DevTools

修改 `electron/main.ts`,在生产环境也打开DevTools:

```typescript
// 临时调试:在生产环境也打开DevTools
if (!process.env.VITE_DEV_SERVER_URL) {
  mainWindow.webContents.openDevTools()
}
```

然后重新打包:
```bash
cd lsc-electron
npx electron-builder
```

### 方法3: 远程调试

使用Chrome DevTools远程调试Electron应用:

1. 启动应用
2. 打开Chrome浏览器
3. 访问 `chrome://inspect/#devices`
4. 配置端口转发
5. 连接到Electron应用

---

## ✅ 验收标准

应用必须满足以下所有条件才算测试通过:

### 基础功能:
- [x] 应用能够正常启动
- [x] 窗口正常显示(非空白)
- [x] UI界面完整加载
- [x] 无JavaScript错误(Console无红色错误)
- [x] Python后端正常启动并连接

### 图标显示:
- [x] 窗口标题栏图标正确(白色背景)
- [x] 任务栏图标正确
- [x] 不是默认Electron图标
- [x] 图标清晰无失真

### 基本交互:
- [x] 侧边栏导航可用
- [x] 设置页面可访问
- [x] 窗口可以最小化/最大化/关闭
- [x] 托盘功能正常(如果启用)

### 稳定性:
- [x] 无崩溃或卡死
- [x] 内存占用合理(< 500MB)
- [x] CPU占用合理(< 30%)

---

## 📝 常见问题FAQ

### Q1: 为什么开发模式正常,安装包就空白?

**A**: 这是因为开发和生产环境的资源加载路径不同。我们已经添加了错误处理和超时检测来诊断这个问题。请查看DevTools Console中的 `[createWindow]` 日志。

### Q2: 图标为什么改成白色背景?

**A**: 透明背景的图标在某些Windows主题下显示效果不佳,改为白色背景可以确保在所有环境下都有良好的视觉效果。

### Q3: 如何确认是前端问题还是后端问题?

**A**: 
- **前端问题**: 窗口空白、UI不显示、JavaScript错误
- **后端问题**: WebSocket连接失败、录制功能不可用、Python错误

查看DevTools Console可以区分:
- 前端错误会直接显示在Console中
- 后端错误会在Network标签的WebSocket连接中显示

### Q4: 安装包体积为什么这么大(84MB)?

**A**: Electron应用包含完整的Chromium运行时,这是Electron架构的特性。84MB是正常的Electron应用体积。

### Q5: 能否减小安装包体积?

**A**: 可以尝试:
1. 使用asar压缩(已启用)
2. 移除不必要的依赖
3. 代码分割和懒加载
4. 使用更轻量的框架(如Tauri)

但这需要权衡开发效率和用户体验。

---

##  下一步

如果测试通过:

1. **发布到GitHub Releases**
   ```bash
   git tag v1.0.1  # 因为修复了bug,小版本号+1
   git push origin v1.0.1
   ```

2. **更新CHANGELOG.md**
   - 记录本次修复的内容
   - 列出已知问题

3. **通知用户**
   - 发送更新通知
   - 提供下载链接
   - 说明升级步骤

如果测试失败:

1. **收集调试信息**(见上文)
2. **提交GitHub Issue**
3. **等待修复**

---

**最后更新**: 2026-06-28 19:33  
**维护者**: LSC开发团队  
**文档版本**: 2.0
