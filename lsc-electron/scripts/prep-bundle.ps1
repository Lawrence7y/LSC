# LSC Live Stream Clipper - Bundle Resources Preparation
# Downloads embedded Python + uv dependency installer.
# With -WithDeps, also bundles ALL Python dependencies and FFmpeg inside the
# installer (required for Microsoft Store / MSIX builds, policy 10.2.5:
# the product must not download/install software at runtime).
#
# Output:
#   .bundle/python            - embedded Python (consumed by electron-builder)
#   .bundle/uv                - uv dependency installer (for dev/runtime fallback)
#   .bundle/python/python-packages  - bundled site-packages (only with -WithDeps)
#   .bundle/ffmpeg            - bundled FFmpeg binaries (only with -WithDeps)
#
# Usage:
#   .\scripts\prep-bundle.ps1             # small installer (~60MB, runtime download)
#   .\scripts\prep-bundle.ps1 -WithDeps   # self-contained installer (~1.5GB, offline)

param(
    [switch]$WithDeps
)

$ErrorActionPreference = "Stop"

# ----- Config -----
$PythonVersion = "3.12.10"
$PythonArch = "amd64"
$UvUrl = "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip"
$PipIndexUrl = "https://pypi.tuna.tsinghua.edu.cn/simple"
$FfmpegUrls = @(
    "https://ghfast.top/https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl-shared.zip",
    "https://gh-proxy.com/https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl-shared.zip",
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl-shared.zip"
)
$VcRedistUrl = "https://aka.ms/vs/17/release/vc_redist.x64.exe"

# ----- Paths -----
# Script lives in lsc-electron/scripts/, project root is two levels up
$ScriptDir = $PSScriptRoot
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..\..")
$BundleDir = Join-Path $ProjectRoot "lsc-electron\.bundle"
$PythonDir = Join-Path $BundleDir "python"
$UvDir = Join-Path $BundleDir "uv"
$PackagesDir = Join-Path $PythonDir "python-packages"
$FfmpegDir = Join-Path $BundleDir "ffmpeg"
$TempDir = Join-Path $BundleDir ".tmp"

# ----- Helpers -----
function Write-Step($msg) {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host $msg -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
}

function Write-OK($msg) {
    Write-Host "OK $msg" -ForegroundColor Green
}

function Write-Err($msg) {
    Write-Host "ERROR $msg" -ForegroundColor Red
}

function Download-File($url, $dest) {
    Write-Host "  Download: $url" -ForegroundColor Gray
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12
    $ProgressPreference = 'SilentlyContinue'  # speeds up download
    Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
}

# ----- Main -----
$mode = if ($WithDeps) { "with bundled dependencies (offline Store build)" } else { "runtime-download build" }
Write-Step "LSC Bundle Prep - Python $PythonVersion ($mode)"

foreach ($d in @($BundleDir, $PythonDir, $UvDir, $TempDir)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
}

# ============ [1/4] Embedded Python ============
Write-Step "[1/4] Prepare embedded Python $PythonVersion ($PythonArch)"

$PythonExe = Join-Path $PythonDir "python.exe"
if (-not (Test-Path $PythonExe)) {
    $PythonZipUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-$PythonArch.zip"
    $PythonZip = Join-Path $TempDir "python-embed.zip"
    Download-File $PythonZipUrl $PythonZip
    Expand-Archive -Path $PythonZip -DestinationPath $PythonDir -Force
    Remove-Item $PythonZip -Force
    Write-OK "Embedded Python extracted to $PythonDir"
} else {
    Write-OK "Embedded Python already exists, skip download"
}

# Enable site-packages (uncomment `import site` and add site-packages path in ._pth)
$PthFile = Get-ChildItem -Path $PythonDir -Filter "python*._pth" | Select-Object -First 1
if ($PthFile) {
    $pthContent = Get-Content $PthFile.FullName -Raw
    $newContent = $pthContent -replace "#import site", "import site`nLib\site-packages"
    # 内置依赖目录：python-packages 相对 python.exe 目录（不存在时 Python 自动忽略）。
    # electron-builder 还会把同一份依赖放到 resources/python-packages（python.exe 的同级），
    # 所以同时写入 ../python-packages，两种布局都能被嵌入式 Python 发现。
    foreach ($pkgLine in @("python-packages", "../python-packages")) {
        if ($newContent -notmatch "(?m)^$([regex]::Escape($pkgLine))\s*$") {
            $newContent = $newContent.TrimEnd() + "`n$pkgLine`n"
        }
    }
    Set-Content -Path $PthFile.FullName -Value $newContent -NoNewline
    Write-OK "Enabled site-packages + bundled packages: $($PthFile.Name)"
}

# Verify Python runs
& $PythonExe -c "import sys; print(f'Python {sys.version}')" 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor Gray }
if ($LASTEXITCODE -ne 0) {
    Write-Err "Python verification failed"
    exit 1
}
Write-OK "Embedded Python verified"

# ============ [2/4] uv dependency installer ============
Write-Step "[2/4] Prepare uv dependency installer"

$UvExe = Join-Path $UvDir "uv.exe"
if (-not (Test-Path $UvExe)) {
    $UvZip = Join-Path $TempDir "uv.zip"
    $UvExtract = Join-Path $TempDir "uv-extract"
    Download-File $UvUrl $UvZip
    Expand-Archive -LiteralPath $UvZip -DestinationPath $UvExtract -Force
    $UvSource = Get-ChildItem -LiteralPath $UvExtract -Recurse -Filter "uv.exe" | Select-Object -First 1
    if (-not $UvSource) {
        Write-Err "uv.exe not found in downloaded archive"
        exit 1
    }
    Copy-Item -LiteralPath $UvSource.FullName -Destination $UvExe -Force
}
& $UvExe --version
if ($LASTEXITCODE -ne 0) {
    Write-Err "uv verification failed"
    exit 1
}
Write-OK "uv dependency installer ready"

# ============ [3/4] Bundled Python dependencies (only with -WithDeps) ============
if ($WithDeps) {
    Write-Step "[3/4] Install ALL Python dependencies into bundle (offline Store build)"

    $Requirements = Join-Path $ProjectRoot "requirements.txt"
    $RequirementsAi = Join-Path $ProjectRoot "requirements-ai.txt"
    if (-not (Test-Path $Requirements)) {
        Write-Err "requirements.txt not found: $Requirements"
        exit 1
    }
    if (-not (Test-Path $RequirementsAi)) {
        Write-Err "requirements-ai.txt not found: $RequirementsAi"
        exit 1
    }

    if (Test-Path $PackagesDir) {
        Remove-Item $PackagesDir -Recurse -Force
    }
    New-Item -ItemType Directory -Path $PackagesDir -Force | Out-Null

    Write-Host "  Installing Python packages (core + AI, torch CPU) to $PackagesDir" -ForegroundColor Gray
    & $UvExe pip install `
        --system-certs `
        --no-python-downloads `
        --python $PythonExe `
        --target $PackagesDir `
        --torch-backend cpu `
        --index-url $PipIndexUrl `
        --requirements $Requirements `
        --requirements $RequirementsAi
    if ($LASTEXITCODE -ne 0) {
        Write-Err "uv pip install failed (exit $LASTEXITCODE)"
        exit 1
    }

    # DirectML：用 onnxruntime-directml 替换 rapidocr 拉取的 CPU onnxruntime
    # （Store 版离线可用，DML 为 Windows 系统组件，无需额外安装）
    Write-Host "  Installing onnxruntime-directml (Windows GPU acceleration)..." -ForegroundColor Gray
    Get-ChildItem -LiteralPath $PackagesDir -ErrorAction SilentlyContinue |
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
        --target $PackagesDir `
        --upgrade `
        --index-url $PipIndexUrl `
        'onnxruntime-directml>=1.18,<2'
    if ($LASTEXITCODE -ne 0) {
        Write-Err "onnxruntime-directml install failed (exit $LASTEXITCODE)"
        exit 1
    }

    # 校验：DirectML provider 可用性（打包机必须通过，否则 Store 版分析退回 CPU）
    $PackagesDirLiteral = $PackagesDir.Replace("'", "''")
    $probe = "import sys; sys.path.insert(0, r'$PackagesDirLiteral'); import onnxruntime as ort; p=ort.get_available_providers(); print(p); raise SystemExit(0 if ('DmlExecutionProvider' in p or 'CUDAExecutionProvider' in p) else 42)"
    & $PythonExe -c $probe 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor Gray }
    if ($LASTEXITCODE -ne 0) {
        Write-Err "DirectML probe failed on build machine; Store build would run AI on CPU"
        exit 1
    }
    Write-OK "onnxruntime DirectML enabled"

    $pkgSize = [math]::Round((Get-ChildItem $PackagesDir -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB, 1)
    Write-OK "Bundled Python packages: $PackagesDir ($pkgSize MB)"
} else {
    Write-Step "[3/4] Skip bundling Python dependencies (runtime-download build)"
    if (-not (Test-Path $PackagesDir)) {
        New-Item -ItemType Directory -Path $PackagesDir -Force | Out-Null
    }
}

# ============ [4/4] Bundled FFmpeg + VC++ runtime DLLs (only with -WithDeps) ============
if ($WithDeps) {
    Write-Step "[4/4] Bundle FFmpeg + VC++ runtime DLLs"

    # ---- FFmpeg ----
    if (-not (Test-Path (Join-Path $FfmpegDir "ffmpeg.exe"))) {
        $ffmpegZip = Join-Path $TempDir "ffmpeg.zip"
        $ffmpegExtract = Join-Path $TempDir "ffmpeg-extract"
        $downloaded = $false
        foreach ($ffUrl in $FfmpegUrls) {
            try {
                Write-Host "  Download FFmpeg: $ffUrl" -ForegroundColor Gray
                Invoke-WebRequest -UseBasicParsing -Uri $ffUrl -OutFile $ffmpegZip -TimeoutSec 600
                if ((Get-Item -LiteralPath $ffmpegZip).Length -gt 1MB) {
                    $downloaded = $true
                    break
                }
            } catch {
                Write-Host "  Mirror failed ($ffUrl): $_" -ForegroundColor Yellow
            }
        }
        if (-not $downloaded) {
            Write-Err "FFmpeg download failed (all mirrors tried)"
            exit 1
        }
        if (Test-Path $ffmpegExtract) { Remove-Item $ffmpegExtract -Recurse -Force }
        Expand-Archive -LiteralPath $ffmpegZip -DestinationPath $ffmpegExtract -Force
        $binDir = Get-ChildItem -LiteralPath $ffmpegExtract -Recurse -Directory |
            Where-Object { Test-Path (Join-Path $_.FullName 'ffmpeg.exe') } |
            Select-Object -First 1
        if (-not $binDir) {
            Write-Err "ffmpeg.exe not found in downloaded archive"
            exit 1
        }
        if (-not (Test-Path $FfmpegDir)) { New-Item -ItemType Directory -Path $FfmpegDir -Force | Out-Null }
        Get-ChildItem -LiteralPath $binDir.FullName -File | ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination $FfmpegDir -Force
        }
        Write-OK "FFmpeg bundled to $FfmpegDir"
    } else {
        Write-OK "FFmpeg already bundled, skip"
    }

    # ---- VC++ 2015-2022 runtime DLLs ----
    # torch/numpy/onnxruntime 的 C 扩展依赖 msvcp140/vcruntime140 等 DLL。
    # MSIX 应用无法提权安装系统级 vc_redist（medium IL），且 10.2.5 禁止运行时
    # 下载安装——因此把 DLL 直接放到 python.exe 旁边（Windows DLL 搜索顺序
    # 优先 exe 目录），不依赖目标机系统组件安装。
    # 必需：torch/onnxruntime/opencv 加载所依赖；可选：OpenMP 扩展（vcomp140_1/2）。
    $vcRequiredDllNames = @(
        'msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll',
        'vcruntime140.dll', 'vcruntime140_1.dll',
        'concrt140.dll', 'vcomp140.dll'
    )
    $vcOptionalDllNames = @('vcomp140_1.dll', 'vcomp140_2.dll')
    $missingVc = @($vcRequiredDllNames + $vcOptionalDllNames) |
        Where-Object { -not (Test-Path (Join-Path $PythonDir $_)) }
    if ($missingVc) {
        # 方案 1：从本机 System32 复制（Windows 10/11 内置 VC++ 运行库组件，
        # 版本随系统更新，构建机通常已是最新）
        $copied = 0
        $stillMissing = @()
        foreach ($dll in $missingVc) {
            $sys = Join-Path $env:WINDIR "System32\$dll"
            if (Test-Path $sys) {
                Copy-Item -LiteralPath $sys -Destination (Join-Path $PythonDir $dll) -Force
                $copied++
            } else {
                $stillMissing += $dll
            }
        }
        Write-OK "VC++ runtime DLLs copied from System32 ($copied DLLs)"
        # 方案 2（兜底）：从 vc_redist 提取剩余 DLL
        if ($stillMissing) {
            $vcRedist = Join-Path $TempDir "vc_redist.x64.exe"
            if (-not (Test-Path $vcRedist)) {
                Write-Host "  Download VC++ redistributable: $VcRedistUrl" -ForegroundColor Gray
                Invoke-WebRequest -UseBasicParsing -Uri $VcRedistUrl -OutFile $vcRedist -TimeoutSec 600
            }
            # vc_redist 是 WiX Burn 引导程序：/layout 只复制自身；
            # 尝试用 7-Zip 解压（若可用）提取内嵌 cab
            $sevenZip = Get-Command 7z -ErrorAction SilentlyContinue
            if ($sevenZip) {
                $cabExtract = Join-Path $TempDir "vc-cab"
                if (Test-Path $cabExtract) { Remove-Item $cabExtract -Recurse -Force }
                New-Item -ItemType Directory -Path $cabExtract -Force | Out-Null
                & 7z x -y -o"$cabExtract" $vcRedist | Out-Null
                foreach ($dll in $stillMissing) {
                    $src = Get-ChildItem -LiteralPath $cabExtract -Recurse -Filter $dll -ErrorAction SilentlyContinue | Select-Object -First 1
                    if ($src) {
                        Copy-Item -LiteralPath $src.FullName -Destination (Join-Path $PythonDir $dll) -Force
                        $copied++
                        $stillMissing = $stillMissing | Where-Object { $_ -ne $dll }
                    }
                }
            }
            # 可选 DLL（OpenMP 扩展）缺失仅警告；必需 DLL 缺失才视为致命
            $missingRequired = $stillMissing | Where-Object { $_ -in $vcRequiredDllNames }
            if ($stillMissing) {
                Write-Host "  WARNING: missing VC++ DLLs: $($stillMissing -join ', ')" -ForegroundColor Yellow
            }
            if ($missingRequired -and $copied -eq 0) {
                Write-Err "No VC++ runtime DLLs copied; torch/onnxruntime will fail to load"
                exit 1
            }
        }
    } else {
        Write-OK "VC++ runtime DLLs already bundled, skip"
    }

    $ffSize = [math]::Round((Get-ChildItem $FfmpegDir -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB, 1)
    Write-OK "Bundled FFmpeg: $FfmpegDir ($ffSize MB)"
} else {
    Write-Step "[4/4] Skip bundling FFmpeg (runtime-download build)"
    if (-not (Test-Path $FfmpegDir)) {
        New-Item -ItemType Directory -Path $FfmpegDir -Force | Out-Null
    }
}

# ============ Summary ============
Write-Step "Bundle prep complete"

$PythonSize = [math]::Round((Get-ChildItem $PythonDir -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB, 1)
$UvSize = [math]::Round((Get-ChildItem $UvDir -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB, 1)

Write-Host "  Python dir: $PythonDir ($PythonSize MB)" -ForegroundColor White
Write-Host "  uv dir: $UvDir ($UvSize MB)" -ForegroundColor White
if ($WithDeps) {
    $pkgSize = [math]::Round((Get-ChildItem $PackagesDir -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB, 1)
    Write-Host "  Bundled packages: $PackagesDir ($pkgSize MB)" -ForegroundColor White
    $ffSize = [math]::Round((Get-ChildItem $FfmpegDir -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB, 1)
    Write-Host "  Bundled FFmpeg: $FfmpegDir ($ffSize MB)" -ForegroundColor White
    Write-Host "  Build mode: self-contained (offline, Store-compliant)" -ForegroundColor Green
} else {
    Write-Host "  Note: Python packages and FFmpeg will be downloaded at first run" -ForegroundColor White
}

# Cleanup temp dir
if (Test-Path $TempDir) { Remove-Item $TempDir -Recurse -Force -ErrorAction SilentlyContinue }

Write-Host ""
if ($WithDeps) {
    Write-Host "  Next: run build-installer.ps1 or build-msix.ps1 to trigger packaging" -ForegroundColor Yellow
} else {
    Write-Host "  Next: run build-installer.ps1 to trigger electron-builder packaging" -ForegroundColor Yellow
}
