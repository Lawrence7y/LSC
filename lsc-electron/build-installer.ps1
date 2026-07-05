# LSC 直播切片系统 - PowerShell 安装包构建脚本

$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "LSC Live Stream Clipper - Build Installer" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Change to script directory
Set-Location $PSScriptRoot

# [1/5] Check Node.js environment
Write-Host "[1/5] Checking Node.js environment..." -ForegroundColor Yellow
try {
    $nodeVersion = node --version
    Write-Host "OK Node.js installed ($nodeVersion)" -ForegroundColor Green
} catch {
    Write-Host "ERROR: Node.js not found, please install Node.js first" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# [2/5] Install dependencies
Write-Host ""
Write-Host "[2/5] Installing dependencies..." -ForegroundColor Yellow
try {
    npm install
    Write-Host "OK Dependencies installed" -ForegroundColor Green
} catch {
    Write-Host "ERROR: npm install failed" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# [3/5] Compile TypeScript
Write-Host ""
Write-Host "[3/5] Compiling TypeScript..." -ForegroundColor Yellow
try {
    npx tsc --noEmit
    Write-Host "OK TypeScript compiled" -ForegroundColor Green
} catch {
    Write-Host "WARNING: TypeScript has warnings, continuing build..." -ForegroundColor Yellow
}

# [4/5] Build frontend resources
Write-Host ""
Write-Host "[4/5] Building frontend resources..." -ForegroundColor Yellow
try {
    npx vite build
    Write-Host "OK Frontend built" -ForegroundColor Green
} catch {
    Write-Host "ERROR: Vite build failed" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# [5/5] Build Electron installer
Write-Host ""
Write-Host "[5/5] Building Electron installer..." -ForegroundColor Yellow
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
