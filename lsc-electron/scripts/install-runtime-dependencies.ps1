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
        --upgrade `
        --reinstall-package onnxruntime-directml `
        'onnxruntime-directml>=1.18,<2' 2>&1 | ForEach-Object {
            $_ | Out-File -FilePath $logPath -Encoding utf8 -Append
            Write-Host $_
        }
    $directmlExitCode = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($directmlExitCode -ne 0) {
        exit $directmlExitCode
    }

    $packageDirLiteral = $packageDir.Replace("'", "''")
    $providerProbe = "import sys; sys.path.insert(0, r'$packageDirLiteral'); import onnxruntime as ort; providers=ort.get_available_providers(); print(providers); raise SystemExit(0 if 'DmlExecutionProvider' in providers or 'CUDAExecutionProvider' in providers else 42)"
    $providerOutput = & $PythonExe -c $providerProbe 2>&1
    $providerOutput | Out-File -FilePath $logPath -Encoding utf8 -Append
    if ($LASTEXITCODE -ne 0) {
        throw "GPU inference provider verification failed: $providerOutput"
    }

    $ffmpegExe = Join-Path $ffmpegDir 'ffmpeg.exe'
    $ffprobeExe = Join-Path $ffmpegDir 'ffprobe.exe'
    if (-not (Test-Path $ffmpegExe) -or -not (Test-Path $ffprobeExe)) {
        $ffmpegZip = Join-Path $env:TEMP 'lsc-ffmpeg.zip'
        $ffmpegExtract = Join-Path $env:TEMP 'lsc-ffmpeg-extract'
        Invoke-WebRequest -UseBasicParsing `
            -Uri 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl-shared.zip' `
            -OutFile $ffmpegZip
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
