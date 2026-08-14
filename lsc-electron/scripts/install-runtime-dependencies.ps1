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
        $packageDirLiteral = $packageDir.Replace("'", "''")
        $providerProbe = "import sys; sys.path.insert(0, r'$packageDirLiteral'); import onnxruntime as ort; providers=ort.get_available_providers(); print(providers); raise SystemExit(0 if 'DmlExecutionProvider' in providers or 'CUDAExecutionProvider' in providers else 42)"
        $providerOutput = & $PythonExe -c $providerProbe 2>&1
        $providerOutput | Out-File -FilePath $logPath -Encoding utf8 -Append
        if ($LASTEXITCODE -eq 0) {
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
