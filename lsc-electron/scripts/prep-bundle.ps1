# LSC Live Stream Clipper - Bundle Resources Preparation
# Downloads embedded Python ONLY (dependencies are downloaded at first run)
# Output: .bundle/python (consumed by electron-builder)
#
# NOTE: Python packages (PySide6, numpy, etc.) and FFmpeg are now downloaded
# at runtime via dependency_manager.py to keep the installer small (~60MB).

$ErrorActionPreference = "Stop"

# ----- Config -----
$PythonVersion = "3.12.10"
$PythonArch = "amd64"
$UvUrl = "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip"

# ----- Paths -----
# Script lives in lsc-electron/scripts/, project root is two levels up
$ScriptDir = $PSScriptRoot
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..\..")
$BundleDir = Join-Path $ProjectRoot "lsc-electron\.bundle"
$PythonDir = Join-Path $BundleDir "python"
$UvDir = Join-Path $BundleDir "uv"
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
Write-Step "LSC Bundle Prep - Python $PythonVersion (embedded only)"

foreach ($d in @($BundleDir, $PythonDir, $UvDir, $TempDir)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
}

# ============ [1/2] Embedded Python ============
Write-Step "[1/2] Prepare embedded Python $PythonVersion ($PythonArch)"

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

# Verify Python runs
& $PythonExe -c "import sys; print(f'Python {sys.version}')" 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor Gray }
if ($LASTEXITCODE -ne 0) {
    Write-Err "Python verification failed"
    exit 1
}
Write-OK "Embedded Python verified"

# ============ [2/3] uv dependency installer ============
Write-Step "[2/3] Prepare uv dependency installer"

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

# ============ [3/3] Summary ============
Write-Step "[3/3] Bundle prep complete"

$PythonSize = [math]::Round((Get-ChildItem $PythonDir -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB, 1)
$UvSize = [math]::Round((Get-ChildItem $UvDir -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB, 1)

Write-Host "  Python dir: $PythonDir ($PythonSize MB)" -ForegroundColor White
Write-Host "  uv dir: $UvDir ($UvSize MB)" -ForegroundColor White
Write-Host "  Note: Python packages and FFmpeg will be downloaded by the installer" -ForegroundColor White

# Cleanup temp dir
if (Test-Path $TempDir) { Remove-Item $TempDir -Recurse -Force -ErrorAction SilentlyContinue }

Write-Host ""
Write-Host "  Next: run build-installer.ps1 to trigger electron-builder packaging" -ForegroundColor Yellow
