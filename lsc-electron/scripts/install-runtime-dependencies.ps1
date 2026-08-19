param(
    [Parameter(Mandatory = $true)]
    [string]$UvExe,
    [Parameter(Mandatory = $true)]
    [string]$PythonExe,
    [Parameter(Mandatory = $true)]
    [string]$Requirements,
    [Parameter(Mandatory = $true)]
    [string]$RequirementsAi
)

$ErrorActionPreference = 'Stop'

# ── 内置依赖检测 ──────────────────────────────────────────────────────
# 若安装包内置了完整 Python 依赖（Store/内置构建，resources/python-packages），
# 则无需联网安装：直接返回成功（应用运行时使用包内目录，见 dependency_manager.py）。
$bundledPackagesDir = Join-Path $PSScriptRoot 'python-packages'
if (Test-Path (Join-Path $bundledPackagesDir 'numpy')) {
    "=== $(Get-Date -Format o) Bundled dependencies detected, skipping network install ===" |
        Out-File -FilePath (Join-Path $env:TEMP 'lsc-deps-skip.log') -Encoding utf8 -Append -ErrorAction SilentlyContinue
    Write-Host 'Bundled dependencies detected (resources/python-packages), skipping network install.'
    exit 0
}

# ── 国内镜像配置 ──────────────────────────────────────────────────────
# pypi 走清华镜像（国内直连 pypi 官方极慢/易超时）
$PipIndexUrl = 'https://pypi.tuna.tsinghua.edu.cn/simple'
# FFmpeg 走 GitHub 加速代理回退链（按顺序尝试，全部失败才报错）
$FfmpegUrls = @(
    'https://ghfast.top/https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl-shared.zip',
    'https://gh-proxy.com/https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl-shared.zip',
    'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl-shared.zip'
)

# Use the interactive user's Roaming AppData, matching Electron userData.
$appData = [Environment]::GetFolderPath([Environment+SpecialFolder]::ApplicationData)
$runtimeHome = Join-Path $appData 'lsc-electron'
$runtimeDir = Join-Path $runtimeHome 'runtime'
$packageDir = Join-Path $runtimeDir 'python-packages'
$ffmpegDir = Join-Path $runtimeDir 'ffmpeg'
$logDir = Join-Path $runtimeHome 'logs'
$logPath = Join-Path $logDir 'dependency-install.log'
New-Item -ItemType Directory -Path $logDir,$packageDir,$ffmpegDir -Force | Out-Null

$env:APPDATA = $appData
$env:UV_SYSTEM_CERTS = 'true'
$env:UV_NO_PROGRESS = 'true'
$env:UV_LINK_MODE = 'copy'
"=== $(Get-Date -Format o) Runtime dependency installation started ===" | Out-File -FilePath $logPath -Encoding utf8 -Append
"uv: $UvExe" | Out-File -FilePath $logPath -Encoding utf8 -Append
"Python: $PythonExe" | Out-File -FilePath $logPath -Encoding utf8 -Append
"Packages: $packageDir" | Out-File -FilePath $logPath -Encoding utf8 -Append

try {
    # ── VC++ 2015-2022 运行库检测与安装 ────────────────────────────────
    # torch / numpy / onnxruntime 的 C 扩展 DLL 都依赖 msvcp140/vcruntime140。
    # 干净/精简的 Windows（尤其是虚拟机）通常没有 → DLL load failed；
    # 版本过旧（< 14.40，2024 年中）时新版 onnxruntime（1.24+）的 DLL 也可能
    # 加载失败（缺新导出符号）。检测注册表，缺失或过旧则静默升级 vc_redist。
    $vcKey = 'HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64'
    $vcNeedInstall = $false
    $vcInstalledVersion = ''
    try {
        $vcInfo = Get-ItemProperty -Path $vcKey -ErrorAction Stop
        if ($vcInfo.Version) {
            $vcInstalledVersion = $vcInfo.Version
            try {
                $vcVer = [version]($vcInstalledVersion.TrimStart('v'))
                $vcNeedInstall = ($vcVer -lt [version]'14.40.30817')
            } catch {
                $vcNeedInstall = $false
            }
        } else {
            $vcNeedInstall = $true
        }
    } catch {
        $vcNeedInstall = $true
    }
    if ($vcNeedInstall) {
        $reason = if ($vcInstalledVersion) { "too old ($vcInstalledVersion < 14.40)" } else { 'missing' }
        "VC++ 2015-2022 runtime $reason, installing/upgrading..." | Out-File -FilePath $logPath -Encoding utf8 -Append
        Write-Host "VC++ runtime $reason, installing..."
        $vcRedist = Join-Path $env:TEMP 'lsc-vc_redist.x64.exe'
        try {
            Invoke-WebRequest -UseBasicParsing `
                -Uri 'https://aka.ms/vs/17/release/vc_redist.x64.exe' `
                -OutFile $vcRedist -TimeoutSec 300
            $vcProc = Start-Process -FilePath $vcRedist `
                -ArgumentList '/install','/quiet','/norestart' `
                -Verb RunAs -Wait -PassThru
            $vcExit = $vcProc.ExitCode
            # 3010 = 安装成功需重启；0 = 成功
            if ($vcExit -eq 0 -or $vcExit -eq 3010) {
                "VC++ runtime installed/upgraded (exit $vcExit)" | Out-File -FilePath $logPath -Encoding utf8 -Append
            } else {
                "VC++ runtime install failed (exit $vcExit), continuing anyway" | Out-File -FilePath $logPath -Encoding utf8 -Append
            }
        } catch {
            "VC++ runtime install error: $_" | Out-File -FilePath $logPath -Encoding utf8 -Append
        }
    } else {
        "VC++ 2015-2022 runtime already up-to-date: $vcInstalledVersion" | Out-File -FilePath $logPath -Encoding utf8 -Append
    }

    # uv writes normal status output to stderr. PowerShell 5.1 must not promote
    # those lines to terminating NativeCommandError exceptions.
    $ErrorActionPreference = 'Continue'
    & $UvExe pip install `
        --system-certs `
        --no-python-downloads `
        --python $PythonExe `
        --target $packageDir `
        --torch-backend cpu `
        --index-url $PipIndexUrl `
        --requirements $Requirements `
        --requirements $RequirementsAi 2>&1 | ForEach-Object {
            $_ | Out-File -FilePath $logPath -Encoding utf8 -Append
            Write-Host $_
        }
    $uvExitCode = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($uvExitCode -ne 0) {
        exit $uvExitCode
    }

    # rapidocr-onnxruntime 会拉取仅 CPU 的 onnxruntime。Windows 持续分析需要
    # DirectML，否则视觉分类器会退回 CPU 并在录制增长时不断超时。
    # 新版 onnxruntime-directml（1.24.x）在部分旧环境 DLL 加载失败（缺系统
    # DirectML 组件/VC++ 运行库较旧）→ 回退旧版 1.21.1 再校验；
    # 仍失败则降级 CPU onnxruntime 并继续安装（分析降速但应用可用，不中止安装）。
    $directmlOk = $false
    foreach ($dmlSpec in @('onnxruntime-directml>=1.18,<2', 'onnxruntime-directml==1.21.1')) {
        $ErrorActionPreference = 'Continue'
        Get-ChildItem -LiteralPath $packageDir -ErrorAction SilentlyContinue |
            Where-Object {
                $_.Name -eq 'onnxruntime' -or
                $_.Name -like 'onnxruntime-*.dist-info' -or
                $_.Name -like 'onnxruntime_directml-*.dist-info'
            } |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        "Installing DirectML: $dmlSpec" | Out-File -FilePath $logPath -Encoding utf8 -Append
        & $UvExe pip install `
            --system-certs `
            --no-python-downloads `
            --python $PythonExe `
            --target $packageDir `
            --upgrade `
            --index-url $PipIndexUrl `
            --reinstall-package onnxruntime-directml `
            $dmlSpec 2>&1 | ForEach-Object {
                $_ | Out-File -FilePath $logPath -Encoding utf8 -Append
                Write-Host $_
            }
        $dmlExitCode = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'
        if ($dmlExitCode -ne 0) {
            "uv install failed for $dmlSpec (exit $dmlExitCode), trying next..." | Out-File -FilePath $logPath -Encoding utf8 -Append
            continue
        }
        # probe 前必须切回 Continue：PS 5.1 在 Stop 模式下会把外部命令的
        # stderr 行（Python traceback）提升为终止性错误，导致降级链不执行
        $ErrorActionPreference = 'Continue'
        $packageDirLiteral = $packageDir.Replace("'", "''")
        $providerProbe = "import sys; sys.path.insert(0, r'$packageDirLiteral'); import onnxruntime as ort; providers=ort.get_available_providers(); print(providers); raise SystemExit(0 if 'DmlExecutionProvider' in providers or 'CUDAExecutionProvider' in providers else 42)"
        $providerOutput = & $PythonExe -c $providerProbe 2>&1
        $providerOutput | Out-File -FilePath $logPath -Encoding utf8 -Append
        $probeExit = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'
        if ($probeExit -eq 0) {
            $directmlOk = $true
            break
        }
        "DirectML probe failed for $dmlSpec, trying next..." | Out-File -FilePath $logPath -Encoding utf8 -Append
    }
    if (-not $directmlOk) {
        # 降级 CPU：恢复 CPU onnxruntime（rapidocr 可用），安装继续。
        "WARNING: DirectML unavailable on this machine, falling back to CPU onnxruntime. AI analysis will run on CPU (slower)." | Out-File -FilePath $logPath -Encoding utf8 -Append
        Write-Host "WARNING: DirectML unavailable, falling back to CPU onnxruntime..."
        $ErrorActionPreference = 'Continue'
        Get-ChildItem -LiteralPath $packageDir -ErrorAction SilentlyContinue |
            Where-Object {
                $_.Name -eq 'onnxruntime' -or
                $_.Name -like 'onnxruntime-*.dist-info' -or
                $_.Name -like 'onnxruntime_directml-*.dist-info'
            } |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        & $UvExe pip install `
            --system-certs `
            --no-python-downloads `
            --python $PythonExe `
            --target $packageDir `
            --index-url $PipIndexUrl `
            'onnxruntime>=1.18,<2' 2>&1 | ForEach-Object {
                $_ | Out-File -FilePath $logPath -Encoding utf8 -Append
                Write-Host $_
            }
        $cpuExitCode = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'
        if ($cpuExitCode -ne 0) {
            exit $cpuExitCode
        }
    }

    $ffmpegExe = Join-Path $ffmpegDir 'ffmpeg.exe'
    $ffprobeExe = Join-Path $ffmpegDir 'ffprobe.exe'
    if (-not (Test-Path $ffmpegExe) -or -not (Test-Path $ffprobeExe)) {
        $ffmpegZip = Join-Path $env:TEMP 'lsc-ffmpeg.zip'
        $ffmpegExtract = Join-Path $env:TEMP 'lsc-ffmpeg-extract'
        # 国内镜像链下载：加速代理优先，GitHub 直连兜底
        $downloaded = $false
        foreach ($ffUrl in $FfmpegUrls) {
            try {
                "Downloading FFmpeg from: $ffUrl" | Out-File -FilePath $logPath -Encoding utf8 -Append
                Invoke-WebRequest -UseBasicParsing -Uri $ffUrl -OutFile $ffmpegZip -TimeoutSec 300
                if ((Get-Item -LiteralPath $ffmpegZip).Length -gt 1MB) {
                    $downloaded = $true
                    break
                }
                "FFmpeg download too small, trying next mirror..." | Out-File -FilePath $logPath -Encoding utf8 -Append
            } catch {
                "FFmpeg mirror failed ($ffUrl): $_" | Out-File -FilePath $logPath -Encoding utf8 -Append
            }
        }
        if (-not $downloaded) {
            throw 'FFmpeg download failed (all mirrors and official source tried)'
        }
        if (Test-Path $ffmpegExtract) {
            Remove-Item -LiteralPath $ffmpegExtract -Recurse -Force
        }
        Expand-Archive -LiteralPath $ffmpegZip -DestinationPath $ffmpegExtract -Force
        $binDir = Get-ChildItem -LiteralPath $ffmpegExtract -Recurse -Directory |
            Where-Object { Test-Path (Join-Path $_.FullName 'ffmpeg.exe') } |
            Select-Object -First 1
        if (-not $binDir) {
            throw 'ffmpeg.exe was not found in the downloaded archive'
        }
        Copy-Item -LiteralPath (Join-Path $binDir.FullName 'ffmpeg.exe') -Destination $ffmpegExe -Force
        Copy-Item -LiteralPath (Join-Path $binDir.FullName 'ffprobe.exe') -Destination $ffprobeExe -Force
        Get-ChildItem -LiteralPath $binDir.FullName -Filter '*.dll' | ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination $ffmpegDir -Force
        }
    }

    "Runtime dependencies and FFmpeg installed successfully." | Out-File -FilePath $logPath -Encoding utf8 -Append
    exit 0
}
catch {
    $_ | Out-String | Out-File -FilePath $logPath -Encoding utf8 -Append
    exit 1
}
