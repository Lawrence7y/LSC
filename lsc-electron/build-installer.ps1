# LSC 直播切片系统 - PowerShell 安装包构建脚本

$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "LSC Live Stream Clipper - Build Installer" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Change to script directory
Set-Location $PSScriptRoot

# [1/6] Check Node.js environment
Write-Host "[1/6] Checking Node.js environment..." -ForegroundColor Yellow
try {
    $nodeVersion = node --version
    Write-Host "OK Node.js installed ($nodeVersion)" -ForegroundColor Green
} catch {
    Write-Host "ERROR: Node.js not found, please install Node.js first" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# [2/6] Prepare bundled runtime resources (Python embedded + FFmpeg)
Write-Host ""
Write-Host "[2/6] Preparing bundled runtime resources..." -ForegroundColor Yellow
$PrepScript = Join-Path $PSScriptRoot "scripts\prep-bundle.ps1"
if (-not (Test-Path $PrepScript)) {
    Write-Host "ERROR: prep-bundle.ps1 not found at $PrepScript" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
try {
    & powershell -ExecutionPolicy Bypass -File $PrepScript
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: prep-bundle.ps1 failed (exit code $LASTEXITCODE)" -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }
    Write-Host "OK Runtime resources ready" -ForegroundColor Green
} catch {
    Write-Host "ERROR: prep-bundle.ps1 failed: $_" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# [3/6] Install dependencies
Write-Host ""
Write-Host "[3/6] Installing dependencies..." -ForegroundColor Yellow
try {
    npm install
    Write-Host "OK Dependencies installed" -ForegroundColor Green
} catch {
    Write-Host "ERROR: npm install failed" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# [4/6] Compile TypeScript
Write-Host ""
Write-Host "[4/6] Compiling TypeScript..." -ForegroundColor Yellow
try {
    npx tsc --noEmit
    Write-Host "OK TypeScript compiled" -ForegroundColor Green
} catch {
    Write-Host "WARNING: TypeScript has warnings, continuing build..." -ForegroundColor Yellow
}

# [5/6] Build frontend resources
Write-Host ""
Write-Host "[5/6] Building frontend resources..." -ForegroundColor Yellow
try {
    npx vite build
    Write-Host "OK Frontend built" -ForegroundColor Green
} catch {
    Write-Host "ERROR: Vite build failed" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# [6/6] Build Electron installer
Write-Host ""
Write-Host "[6/6] Building Electron installer..." -ForegroundColor Yellow
try {
    npx electron-builder
    Write-Host "OK Installer built" -ForegroundColor Green
} catch {
    Write-Host "ERROR: electron-builder failed" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# Show results
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "Build Successful!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Installer location: release\" -ForegroundColor Cyan

# List generated installers
if (Test-Path "release\*.exe") {
    Get-ChildItem -Path "release\*.exe" | ForEach-Object {
        $sizeMB = [math]::Round($_.Length / 1MB, 2)
        Write-Host "  - $($_.Name) ($sizeMB MB)" -ForegroundColor White
    }
} else {
    Write-Host "  No installer found" -ForegroundColor Red
}

Write-Host ""
Read-Host "Press Enter to exit"
