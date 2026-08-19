# LSC Live Stream Clipper - Build the Microsoft Store (MSIX/AppX) package
#
# Native electron-builder AppX — NOT MSIX Packaging Tool conversion.
# Wrapping the NSIS "Setup.exe" with MSIX Packaging Tool is policy 10.2.5
# ("product is primarily an installer for another app") and will be rejected.
#
# Partner Center product: Live Stream Clipper (9NQWM5KPRWF1)
#
# Usage:
#   .\scripts\build-msix.ps1
#   .\scripts\build-msix.ps1 -SkipPrep
#   .\scripts\build-msix.ps1 -IdentityName "Lawrence7YY.LiveStreamClipper" -Publisher "CN=B2250643-15B9-4016-82B3-C97EAFA5DABD"

param(
    [switch]$SkipPrep,
    [string]$Certificate = "build\cert.pfx",
    [string]$Password = "lsc-store-test",
    [string]$IdentityName = "Lawrence7YY.LiveStreamClipper",
    [string]$Publisher = "CN=B2250643-15B9-4016-82B3-C97EAFA5DABD",
    [string]$PublisherDisplayName = "Lawrence7YY"
)

$ErrorActionPreference = "Stop"
$Utf8NoBom = New-Object System.Text.UTF8Encoding $false

$ScriptDir = $PSScriptRoot
$AppDir = Resolve-Path (Join-Path $ScriptDir "..")

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "LSC MSIX Build (Store, self-contained)" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  identityName: $IdentityName"
Write-Host "  publisher:    $Publisher"
Write-Host "  NEVER wrap NSIS Setup.exe with MSIX Packaging Tool." -ForegroundColor Yellow

# ----- 1. Bundle all dependencies -----
if (-not $SkipPrep) {
    Write-Host "[1/6] prep-bundle.ps1 -WithDeps (downloads ~1.5GB on first run)..." -ForegroundColor Cyan
    & (Join-Path $ScriptDir "prep-bundle.ps1") -WithDeps
    if (-not $?) {
        Write-Error "prep-bundle failed"
        exit 1
    }
} else {
    Write-Host "[1/6] Skip prep-bundle (-SkipPrep)" -ForegroundColor Cyan
}

$packagesDir = Join-Path $AppDir ".bundle\python\python-packages"
$ffmpegExe = Join-Path $AppDir ".bundle\ffmpeg\ffmpeg.exe"
if (-not (Test-Path (Join-Path $packagesDir "numpy"))) {
    Write-Error "Bundled python-packages missing numpy. Run without -SkipPrep, or: .\scripts\prep-bundle.ps1 -WithDeps"
    exit 1
}
if (-not (Test-Path $ffmpegExe)) {
    Write-Error "Bundled ffmpeg.exe missing. Run without -SkipPrep, or: .\scripts\prep-bundle.ps1 -WithDeps"
    exit 1
}

# ----- 2. Code-signing certificate (Subject must match Publisher) -----
Write-Host "[2/6] Ensure code-signing certificate matches Publisher..." -ForegroundColor Cyan
$CertFull = Join-Path $AppDir $Certificate
$needNewCert = $true
if (Test-Path $CertFull) {
    try {
        $secure = ConvertTo-SecureString $Password -AsPlainText -Force
        $existing = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 $CertFull, $secure, 'Exportable'
        if ($existing.Subject -eq $Publisher) {
            $needNewCert = $false
            Write-Host "  Using certificate: $CertFull ($($existing.Subject))" -ForegroundColor Gray
        } else {
            Write-Host "  Certificate subject '$($existing.Subject)' != Publisher '$Publisher'; regenerating." -ForegroundColor Yellow
        }
    } catch {
        Write-Host "  Existing certificate unreadable; regenerating. $_" -ForegroundColor Yellow
    }
}
if ($needNewCert) {
    & (Join-Path $ScriptDir "create-test-cert.ps1") -Password $Password -Subject $Publisher -Force
    if (-not $?) {
        Write-Error "create-test-cert failed"
        exit 1
    }
}

# ----- 3. TypeScript check + renderer build -----
Write-Host "[3/6] tsc --noEmit + vite build..." -ForegroundColor Cyan
Push-Location $AppDir
try {
    npx tsc --noEmit
    if ($LASTEXITCODE -ne 0) { throw "tsc failed" }
    npx vite build
    if ($LASTEXITCODE -ne 0) { throw "vite build failed" }
} finally {
    Pop-Location
}

# ----- 4. electron-builder AppX (native container, not a converted installer) -----
Write-Host "[4/6] electron-builder --win appx (native Store package)..." -ForegroundColor Cyan
Push-Location $AppDir
try {
    $pkgPath = Join-Path $AppDir "package.json"
    $pkg = [System.IO.File]::ReadAllText($pkgPath, $Utf8NoBom) | ConvertFrom-Json
    $build = $pkg.build | ConvertTo-Json -Depth 30 | ConvertFrom-Json

    function Set-Prop($obj, [string]$name, $value) {
        if ($obj.PSObject.Properties.Name -contains $name) {
            $obj.$name = $value
        } else {
            $obj | Add-Member -NotePropertyName $name -NotePropertyValue $value -Force
        }
    }

    Set-Prop $build.win "certificateFile" $Certificate
    Set-Prop $build.win "certificatePassword" $Password
    Set-Prop $build.win "sign" "scripts\sign-no-timestamp.js"
    # Only AppX — do not also emit the NSIS Setup.exe that caused 10.2.5.
    Set-Prop $build.win "target" @("appx")

    Set-Prop $build.appx "identityName" $IdentityName
    Set-Prop $build.appx "publisher" $Publisher
    Set-Prop $build.appx "publisherDisplayName" $PublisherDisplayName
    Set-Prop $build.appx "displayName" "Live Stream Clipper"
    Set-Prop $build.appx "applicationId" "LiveStreamClipper"

    # Store package must not ship a second installer / package manager.
    $filtered = @($build.extraResources | Where-Object {
        $_.to -ne "uv" -and $_.to -ne "install-runtime-dependencies.ps1"
    })
    $build.extraResources = $filtered

    $configOverride = Join-Path $AppDir "build\msix.config.json"
    $json = $build | ConvertTo-Json -Depth 30
    [System.IO.File]::WriteAllText($configOverride, $json, $Utf8NoBom)

    $env:LSC_CERT_FILE = $CertFull
    $env:LSC_CERT_PASSWORD = $Password

    npx electron-builder --win appx -c $configOverride
    if ($LASTEXITCODE -ne 0) { throw "electron-builder appx/msix failed" }
} finally {
    Pop-Location
}

# ----- 5. Reject converted-installer packages (the 10.2.5 failure mode) -----
Write-Host "[5/6] Verify AppxManifest is a native app, not a Setup wrapper..." -ForegroundColor Cyan
$pkgFile = Get-ChildItem -Path (Join-Path $AppDir "release") -Include "*.appx","*.msix" -File -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -notmatch 'blockmap' } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if (-not $pkgFile) {
    Write-Error "No .appx/.msix found in release\"
    exit 1
}

$inspectRoot = Join-Path $env:TEMP "lsc-msix-verify"
if (Test-Path $inspectRoot) { Remove-Item $inspectRoot -Recurse -Force }
New-Item -ItemType Directory -Path $inspectRoot | Out-Null

Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::OpenRead($pkgFile.FullName)
try {
    $manifestEntry = $zip.Entries | Where-Object { $_.FullName -eq "AppxManifest.xml" } | Select-Object -First 1
    if (-not $manifestEntry) {
        Write-Error "AppxManifest.xml missing inside $($pkgFile.Name)"
        exit 1
    }
    $reader = New-Object System.IO.StreamReader($manifestEntry.Open())
    try { $manifest = $reader.ReadToEnd() } finally { $reader.Close() }
    $exeEntries = @($zip.Entries | Where-Object { $_.FullName -match '\.exe$' } | ForEach-Object { $_.FullName })
} finally {
    $zip.Dispose()
}

$failures = @()
if ($manifest -match "MSIX Packaging Tool") {
    $failures += "Manifest was produced by MSIX Packaging Tool (converted installer)."
}
if ($manifest -match "(?i)Setup\.exe|LSCSETUP|Application Id=`"LSCSETUP") {
    $failures += "Entry point is still an installer (Setup.exe / LSCSETUPOne)."
}
if ($manifest -notmatch [regex]::Escape($IdentityName)) {
    $failures += "Identity Name is not $IdentityName"
}
if ($manifest -notmatch [regex]::Escape($Publisher)) {
    $failures += "Identity Publisher is not $Publisher"
}

$setupExes = @($exeEntries | Where-Object {
    $_ -match '(?i)(^|/)[^/]*Setup[^/]*\.exe$' -and $_ -notmatch '(?i)setuptools'
})
if ($setupExes) {
    $failures += ("Package still contains installer EXE: " + ($setupExes -join ", "))
}

if ($failures.Count -gt 0) {
    Write-Host $manifest
    Write-Error ("Store package failed 10.2.5 sanity check:`n  - " + ($failures -join "`n  - "))
    exit 1
}

Write-Host "  AppxManifest OK (native Electron app, Store identity)." -ForegroundColor Green

# ----- 6. Result -----
Write-Host "[6/6] Build complete" -ForegroundColor Cyan
$sizeMB = [math]::Round($pkgFile.Length / 1MB, 1)
Write-Host ""
Write-Host "Package: $($pkgFile.FullName) ($sizeMB MB)" -ForegroundColor Green
Write-Host ""
Write-Host "Upload THIS file to Partner Center. Do not run MSIX Packaging Tool on it." -ForegroundColor Yellow
Write-Host "Local install (admin PowerShell, after trusting build\cert.cer):" -ForegroundColor Yellow
Write-Host "  Add-AppxPackage -Path `"$($pkgFile.FullName)`"" -ForegroundColor Yellow
