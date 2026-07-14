# LSC Live Stream Clipper - Bundle Resources Preparation
# Downloads embedded Python + pip dependencies + FFmpeg/FFprobe
# Output: .bundle/python and .bundle/ffmpeg (consumed by electron-builder)

$ErrorActionPreference = "Stop"

# ----- Config -----
$PythonVersion = "3.12.10"
$PythonArch = "amd64"
# BtbN/FFmpeg-Builds: GPL shared build (smaller, permissive)
$FfmpegUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl-shared.zip"
$GetPipUrl = "https://bootstrap.pypa.io/get-pip.py"

# ----- Paths -----
# Script lives in lsc-electron/scripts/, project root is two levels up
$ScriptDir = $PSScriptRoot
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..\..")
$BundleDir = Join-Path $ProjectRoot "lsc-electron\.bundle"
$PythonDir = Join-Path $BundleDir "python"
$FfmpegDir = Join-Path $BundleDir "ffmpeg"
$TempDir = Join-Path $BundleDir ".tmp"
$RequirementsPath = Join-Path $ProjectRoot "requirements.txt"
$RequirementsAiPath = Join-Path $ProjectRoot "requirements-ai.txt"

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
Write-Step "LSC Bundle Prep - Python $PythonVersion + FFmpeg"

if (-not (Test-Path $RequirementsPath)) {
    Write-Err "requirements.txt not found: $RequirementsPath"
    exit 1
}

foreach ($d in @($BundleDir, $PythonDir, $FfmpegDir, $TempDir)) {
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
    Set-Content -Path $PthFile.FullName -Value $newContent -NoNewline
    Write-OK "Enabled site-packages: $($PthFile.Name)"
}

# ============ [2/4] pip + Python dependencies ============
Write-Step "[2/4] Install Python deps (PySide6 / numpy / websockets / psutil)"

$PipExe = Join-Path $PythonDir "Scripts\pip.exe"
$SitePackages = Join-Path $PythonDir "Lib\site-packages"
$PySideMarker = Join-Path $SitePackages "PySide6\__init__.py"

if (-not (Test-Path $PipExe)) {
    Write-Host "  Installing pip..." -ForegroundColor Gray
    $GetPipScript = Join-Path $TempDir "get-pip.py"
    Download-File $GetPipUrl $GetPipScript
    # --ignore-installed avoids "no RECORD file" errors when reinstalling after prior cleanup
    & $PythonExe $GetPipScript --no-warn-script-location --ignore-installed
    if ($LASTEXITCODE -ne 0) {
        Write-Err "pip install failed"
        exit 1
    }
    Remove-Item $GetPipScript -Force
    Write-OK "pip installed"
} else {
    # pip exists but may have broken RECORD (from prior bundle cleanup), re-install safely
    Write-Host "  pip already exists, ensuring it works..." -ForegroundColor Gray
    $pipTest = & $PythonExe -m pip --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Re-installing pip (previous install broken)..." -ForegroundColor Gray
        $GetPipScript = Join-Path $TempDir "get-pip.py"
        Download-File $GetPipUrl $GetPipScript
        & $PythonExe $GetPipScript --no-warn-script-location --ignore-installed
        if ($LASTEXITCODE -ne 0) {
            Write-Err "pip re-install failed"
            exit 1
        }
        Remove-Item $GetPipScript -Force
    }
    Write-OK "pip ready"
}

# Install deps if PySide6 marker missing
if (-not (Test-Path $PySideMarker)) {
    Write-Host "  Installing requirements.txt into site-packages..." -ForegroundColor Gray
    & $PythonExe -m pip install --no-warn-script-location -r $RequirementsPath
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Dependency install failed"
        exit 1
    }
    Write-OK "Python deps installed"
} else {
    Write-OK "Python deps already exist, skip install"
}

# Install AI deps (rapidocr, opencv, torch, faster-whisper, open-clip) for OCR round refinement
if (Test-Path $RequirementsAiPath) {
    $RapidOcrMarker = Join-Path $SitePackages "rapidocr_onnxruntime\__init__.py"
    if (-not (Test-Path $RapidOcrMarker)) {
        Write-Host "  Installing requirements-ai.txt into site-packages..." -ForegroundColor Gray
        & $PythonExe -m pip install --no-warn-script-location -r $RequirementsAiPath
        if ($LASTEXITCODE -ne 0) {
            Write-Err "AI dependency install failed"
            exit 1
        }
        Write-OK "AI deps installed"
    } else {
        Write-OK "AI deps already exist, skip install"
    }
} else {
    Write-OK "requirements-ai.txt not found, skip AI deps"
}

# Purge pip cache to save space
$pipCache = Join-Path $env:LOCALAPPDATA "pip\Cache"
if (Test-Path $pipCache) {
    Write-Host "  Purging pip cache..." -ForegroundColor Gray
    & $PythonExe -m pip cache purge 2>&1 | Out-Null
}

# Remove __pycache__ dirs
Get-ChildItem -Path $PythonDir -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }

# Remove Scripts dir (not needed in bundle, saves space and avoids entry-script clutter)
$ScriptsDir = Join-Path $PythonDir "Scripts"
if (Test-Path $ScriptsDir) {
    Remove-Item $ScriptsDir -Recurse -Force -ErrorAction SilentlyContinue
    Write-OK "Cleaned Scripts directory"
}

# Strip RECORD files from dist-info (redundant for bundled runtime)
Get-ChildItem -Path $SitePackages -Directory -Filter "*.dist-info" -ErrorAction SilentlyContinue |
    ForEach-Object {
        $record = Join-Path $_.FullName "RECORD"
        if (Test-Path $record) { Remove-Item $record -Force -ErrorAction SilentlyContinue }
    }

# Verify deps importable
& $PythonExe -c "import PySide6, numpy, websockets, psutil; print('deps ok')" 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor Gray }
if ($LASTEXITCODE -ne 0) {
    Write-Err "Dependency import verification failed"
    exit 1
}
Write-OK "Python deps verified"

# ============ [3/4] FFmpeg ============
Write-Step "[3/4] Download FFmpeg (BtbN GPL shared)"

$FfmpegExe = Join-Path $FfmpegDir "ffmpeg.exe"
$FfprobeExe = Join-Path $FfmpegDir "ffprobe.exe"

if (-not (Test-Path $FfmpegExe) -or -not (Test-Path $FfprobeExe)) {
    $FfmpegZip = Join-Path $TempDir "ffmpeg.zip"
    Download-File $FfmpegUrl $FfmpegZip

    Write-Host "  Extracting FFmpeg..." -ForegroundColor Gray
    $FfmpegExtractDir = Join-Path $TempDir "ffmpeg-extract"
    if (Test-Path $FfmpegExtractDir) { Remove-Item $FfmpegExtractDir -Recurse -Force }
    Expand-Archive -Path $FfmpegZip -DestinationPath $FfmpegExtractDir -Force

    # BtbN layout: ffmpeg-master-latest-win64-gpl-shared/bin/ffmpeg.exe
    $BinDir = Get-ChildItem -Path $FfmpegExtractDir -Recurse -Directory -Filter "bin" | Select-Object -First 1
    if (-not $BinDir) {
        Write-Err "FFmpeg zip layout unexpected, bin/ not found"
        exit 1
    }

    Copy-Item -Path (Join-Path $BinDir.FullName "ffmpeg.exe") -Destination $FfmpegExe -Force
    Copy-Item -Path (Join-Path $BinDir.FullName "ffprobe.exe") -Destination $FfprobeExe -Force

    # Copy shared DLLs (required by shared build)
    Get-ChildItem -Path $BinDir.FullName -Filter "*.dll" | ForEach-Object {
        Copy-Item -Path $_.FullName -Destination $FfmpegDir -Force
    }

    Remove-Item $FfmpegZip -Force
    Remove-Item $FfmpegExtractDir -Recurse -Force
    Write-OK "FFmpeg ready at $FfmpegDir"
} else {
    Write-OK "FFmpeg already exists, skip download"
}

# Verify FFmpeg (capture output first, then check exit code — pipe resets $LASTEXITCODE)
$ffOutput = & $FfmpegExe -version 2>&1
$ffExit = $LASTEXITCODE
if ($ffOutput) {
    $firstLine = ($ffOutput | Select-Object -First 1).ToString()
    Write-Host "  $firstLine" -ForegroundColor Gray
}
if ($ffExit -ne 0) {
    Write-Err "FFmpeg verification failed (exit=$ffExit)"
    exit 1
}

# ============ [4/4] Summary ============
Write-Step "[4/4] Bundle prep complete"

$PythonSize = [math]::Round((Get-ChildItem $PythonDir -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB, 1)
$FfmpegSize = [math]::Round((Get-ChildItem $FfmpegDir -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB, 1)

Write-Host "  Python dir: $PythonDir ($PythonSize MB)" -ForegroundColor White
Write-Host "  FFmpeg dir: $FfmpegDir ($FfmpegSize MB)" -ForegroundColor White
Write-Host "  Total: $([math]::Round($PythonSize + $FfmpegSize, 1)) MB" -ForegroundColor White

# Cleanup temp dir
if (Test-Path $TempDir) { Remove-Item $TempDir -Recurse -Force -ErrorAction SilentlyContinue }

Write-Host ""
Write-Host "  Next: run build-installer.ps1 to trigger electron-builder packaging" -ForegroundColor Yellow
