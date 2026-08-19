# Microsoft Store 上架指南（修复 10.2.5）

> 本指南解决认证报告中的 **10.2.5 Security - Installing and Updating Store Apps**：
> *"The product is primarily an installer for another app. Products distributed through the Store may only be installed through the Store."*

## 禁止用 MSIX Packaging Tool 包装 NSIS Setup.exe

2026-08-17 被拒的包 `Lawrence7YY.LiveStreamClipper_1.0.0.0_x64__9nqdfk2wdejvt.msix`
是 **MSIX Packaging Tool 把 NSIS 安装器包进去** 的结果，不是可运行的应用：

| 字段 | 被拒包里的实际值 |
| :--- | :--- |
| 工具注释 | `Package created by MSIX Packaging Tool version: 1.2024.405.0` |
| 入口 Executable | `VFS\LSC 直播切片系统 Setup 1.0.0.exe` |
| Application Id | `LSCSETUPOne` |
| 开始菜单显示名 | `LSC 直播切片系统 Setup 1.0.0` |
| 模板 `<Installer>` | `lsc-electron\release\LSC 直播切片系统 Setup 1.0.0.zip` |

审核员从商店启动应用，看到的就是安装向导 → 直接判 10.2.5。

**正确提交物**：`electron-builder --win appx` 打出的原生 `.appx`（入口是 Electron 主程序，不是 Setup.exe）。
把 `.appx` **直接上传 Partner Center**。不要再对该文件、对 NSIS Setup.exe、对 Setup.zip 运行 MSIX Packaging Tool。

## 修复方案

1. **改 Store 打包**：产品通过 Store 的 AppX/MSIX 容器机制安装（`electron-builder --win appx`，
   .appx 与 .msix 同源容器格式，Partner Center 均接受）
2. **依赖全内置**：Python 依赖（含 AI 推理）与 FFmpeg 打包进应用内（`prep-bundle.ps1 -WithDeps`），
   运行时直接使用包内资源，**零联网安装**；联网下载路径仅保留给开发模式/旧版安装包兜底
3. **身份必须与商店一致**（产品 **9NQWM5KPRWF1** / Live Stream Clipper）：
   - identityName：`Lawrence7YY.LiveStreamClipper`
   - publisher：`CN=B2250643-15B9-4016-82B3-C97EAFA5DABD`
   - publisherDisplayName：`Lawrence7YY`
   - 每次重新提交必须升高版本号（已用过 `1.0.0.0`，本次起用 `1.0.1.0`）

## 一键构建

```powershell
cd lsc-electron
.\scripts\build-msix.ps1
```

脚本流程：`prep-bundle.ps1 -WithDeps`（下载内置 ~1.5GB 依赖，仅首次）→ 生成与商店 Publisher 一致的测试证书
→ `tsc --noEmit` → `vite build` → `electron-builder --win appx` → 校验 AppxManifest 不是 Setup 包装。
产物：`release\LiveStreamClipper-<version>.appx`。

身份已写进 `package.json` / `build-msix.ps1` 默认值。若 Partner Center 产品标识变更：

```powershell
.\scripts\build-msix.ps1 -IdentityName "{商店 identityName}" -Publisher "{商店 CN=...}"
```

## 本地安装验证（可选）

```powershell
# 管理员 PowerShell：信任测试证书（自签名）
Import-Certificate -FilePath "lsc-electron\build\cert.cer" -CertStoreLocation Cert:\LocalMachine\Root

# 安装包
Add-AppxPackage -Path "lsc-electron\release\Live Stream Clipper-<version>.appx"

# 启动后验证：首次启动不联网、不出现"下载依赖"界面，直接进入主界面
# 卸载：Get-AppxPackage *LiveStreamClipper* | Remove-AppxPackage
```

## Partner Center 提交步骤

1. **创建产品**：Partner Center → 应用和游戏 → 创建新应用 → 保留名称 **Live Stream Clipper**
2. **获取 Product identity**：产品页 → 产品管理 → 产品标识（Product identity），记下：
   - **Package family name**（如 `LiveStreamClipper.LSC_xxxxxxxxxxxxx`）
   - **Publisher ID**（如 `CN=...` 对应的发布者 ID，形如 `9xxxxxxxxxxx` 或自定义值）
   - 页面会给出 **identityName**（形如 `{PublisherId}.LiveStreamClipper`）
3. **用商店标识重新打包**（当前产品已填好默认值，一般直接 `.\scripts\build-msix.ps1` 即可）：
   ```powershell
   .\scripts\build-msix.ps1 -IdentityName "Lawrence7YY.LiveStreamClipper" -Publisher "CN=B2250643-15B9-4016-82B3-C97EAFA5DABD"
   ```
   > 注意：商店最终会用自己的证书重新签名包，本地证书仅用于构建/自测；
   > identityName/publisher 必须与 Partner Center 一致，否则提交被拒。
4. **准备提交**：Partner Center → 你的产品 → 提交 → 应用包，上传 `release\LiveStreamClipper-*.appx`
   （不要上传 NSIS Setup.exe，也不要上传 MSIX Packaging Tool 转出来的 .msix）
   （如 Partner Center 要求 `.appxupload`：可用 `MakeAppx.exe pack` 生成或改用商店工具链）
   - 商店列表（**10.7 本地化**）：en-US 使用 `docs/microsoft-store-listing-en.md` 文案，
     zh-CN 保留中文；截图建议英文界面
5. **提交后**：等待认证（通常 1-3 天）。10.2.5 与 10.7 均应在本次提交中通过。

## 技术说明

### 内置依赖如何工作

```
打包机（prep-bundle.ps1 -WithDeps）
  uv pip install --target .bundle/python/python-packages   ← 全部 Python 依赖（torch CPU 等）
  onnxruntime-directml 替换 CPU onnxruntime                ← Windows GPU 加速
  FFmpeg 下载解压 → .bundle/ffmpeg                         ← ffmpeg.exe/ffprobe.exe + DLL
  VC++ 运行库 DLL → .bundle/python/（python.exe 旁边）      ← torch/onnxruntime C 扩展依赖

打包（electron-builder）
  .bundle/python/python-packages → resources/python-packages
  .bundle/ffmpeg                 → resources/ffmpeg
  python312._pth 追加 "python-packages" 行 → 嵌入版 Python 启动即发现内置依赖

运行时（零联网）
  dependency_manager.py：检测到 resources/python-packages 含 numpy → 直接使用，
    跳过 pip 安装；FFmpeg 经 LSC_BUNDLED_FFMPEG_DIR 直接使用包内目录
  electron/main.ts：getRuntimePackagesDir() 优先返回包内目录，依赖就绪校验直接通过
```

### 关键设计决策

| 决策 | 原因 |
| :--- | :--- |
| 依赖打进 `resources/`，运行时不复制到 userData | MSIX 安装目录只读；直接运行包内目录免去首次复制 1.5GB |
| 嵌入式 Python 的 `._pth` 加 `python-packages` 行 | 嵌入版忽略 PYTHONPATH，`_pth` 相对路径是唯一可靠的 sys.path 注入方式 |
| VC++ DLL 内置到 python.exe 旁边 | MSIX 应用（medium IL）无法提权安装 vc_redist；DLL 放 exe 目录即可被 C 扩展加载 |
| onnxruntime-directml 在打包机预装并探测 | Store 版离线；DML 是 Windows 10 1809+ 系统组件，无需额外安装 |
| 移除 PySide6 | 运行链路（python-backend + lsc.core）已无 Qt 导入，省 ~150MB |

### 签名方案

- `build-msix.ps1` 使用自定义签名脚本 `scripts/sign-no-timestamp.js`：
  1. **跳过时间戳服务器**：RFC3161 时间戳服务器（digicert 等）在本机网络不可达，
     不带时间戳的签名在证书有效期内有效；商店提交后微软重新签名并补时间戳
  2. **优先系统 Windows SDK 的 signtool**：electron-builder 缓存版 signtool
     （winCodeSign）在部分 Windows 11 版本上签名 .appx/.msix 报
     "SignTool Error: A required function is not present"（exe 签名正常，
     仅容器签名失败），Windows SDK 自带 signtool 无此问题
- 本地验证证书：`scripts/create-test-cert.ps1`（自签名 3 年，输出 build/cert.pfx + cert.cer）

### 包体积

- 内置依赖后 MSIX 约 **1.5–2GB**（torch/faster-whisper/rapidocr/opencv 为大头）。
  微软商店 .appxupload 上限 25GB、单包 10GB 级，远低于限制（以官方文档为准：
  [MSIX 应用包要求](https://learn.microsoft.com/zh-cn/windows/apps/publish/publish-your-app/msix/app-package-requirements)）。

### 已知边界

- **旧版 NSIS 用户不受影响**：NSIS 版仍走运行时下载（`build-installer.ps1` 不变），
  两套安装机制互不干扰；后续如需统一，可让 NSIS 版也用 `-WithDeps` 内置。
- **development 模式**：prep-bundle 不带 `-WithDeps` 时，依赖仍按原有联网方式
  下载到 `%APPDATA%\lsc-electron\runtime`，行为不变。
- **升级策略**：Store 版后续升级直接提交新 MSIX（版本号递增），Store 自动更新。
