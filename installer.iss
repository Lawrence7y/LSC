; ============================================================
; LSC Live Stream Clipper - Inno Setup 安装脚本
; ============================================================
; 使用方法:
;   1. 先运行 build.bat 构建 dist/LSC/ 目录
;   2. 用 Inno Setup 编译此脚本生成安装程序
;   3. 输出: dist/LSC-Setup-x64.exe
; ============================================================

#define MyAppName "LSC 直播切片系统"
#define MyAppShortName "LSC"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "LSC Team"
#define MyAppExeName "LSC.exe"

[Setup]
; 基本信息
AppId={{B8F1D2C3-4A5E-6F7G-8H9I-0J1K2L3M4N5O}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppShortName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes

; 安装程序输出
OutputDir=dist
OutputBaseFilename=LSC-Setup-x64
SetupIconFile=resources/icon.ico
Compression=lzma2/ultra
SolidCompression=yes
WizardStyle=modern

; 架构
ArchitecturesInstallIn64BitMode=x64
ArchitecturesAllowed=x64

; 权限
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; 卸载程序
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallFilesDir={app}\unins

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标:"; Flags: unchecked
Name: "quicklaunchicon"; Description: "创建快速启动栏快捷方式"; GroupDescription: "附加图标:"; Flags: unchecked; OnlyBelowVersion: 6.1
Name: "associate"; Description: "关联 .lsc 项目文件"; GroupDescription: "文件关联:"; Flags: unchecked

[Files]
; 主程序文件
Source: "dist\LSC\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

; README
Source: "README.md"; DestDir: "{app}"; Flags: isreadme

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: quicklaunchicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\config"
Type: filesandordirs; Name: "{userdocs}\LSC"

[Registry]
Root: HKCU; Subkey: "Software\{#MyAppShortName}"; ValueType: string; ValueName: "InstallPath"; ValueData: "{app}"; Flags: uninsdeletekey
