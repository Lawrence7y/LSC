# LSC Live Stream Clipper - Generate Microsoft Store (appx/msix) tile logos
# Fix for Store policy 10.1.1.11 "On Device Tiles".
#
# Delegates to gen-appx-icons.mjs (resvg SVG raster + scale-100/125/150/200/400
# + Square44 targetsize unplated). Do not use GDI+ / icon.ico upscaling.
#
# Usage: .\scripts\gen-appx-icons.ps1

$ErrorActionPreference = "Stop"
$ScriptDir = $PSScriptRoot
$AppDir = Resolve-Path (Join-Path $ScriptDir "..")

Push-Location $AppDir
try {
    $node = Get-Command node -ErrorAction Stop
    & $node.Source (Join-Path $ScriptDir "gen-appx-icons.mjs")
    if ($LASTEXITCODE -ne 0) {
        throw "gen-appx-icons.mjs failed with exit $LASTEXITCODE"
    }
} finally {
    Pop-Location
}
