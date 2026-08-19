# LSC Live Stream Clipper - Create a self-signed code-signing certificate (PFX)
# Used for LOCAL MSIX install/verification (sideloading). The Microsoft Store
# replaces the signature with its own when the package is submitted, so this
# certificate is NOT what Store customers see.
#
# The certificate Subject MUST match AppX Identity Publisher, otherwise
# electron-builder / signtool will refuse to sign the package.
#
# Usage:
#   .\scripts\create-test-cert.ps1
#   .\scripts\create-test-cert.ps1 -Force -Subject "CN=B2250643-15B9-4016-82B3-C97EAFA5DABD"

param(
    [string]$Password = "lsc-store-test",
    [string]$Subject = "CN=B2250643-15B9-4016-82B3-C97EAFA5DABD",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$ScriptDir = $PSScriptRoot
$AppDir = Resolve-Path (Join-Path $ScriptDir "..")
$CertDir = Join-Path $AppDir "build"
$CertPath = Join-Path $CertDir "cert.pfx"
$CerPath = Join-Path $CertDir "cert.cer"

if (-not (Test-Path $CertDir)) {
    New-Item -ItemType Directory -Path $CertDir -Force | Out-Null
}

if (Test-Path $CertPath) {
    if (-not $Force) {
        Write-Host "Certificate already exists: $CertPath (delete it or pass -Force to regenerate)" -ForegroundColor Yellow
        exit 0
    }
    Remove-Item $CertPath -Force
    if (Test-Path $CerPath) { Remove-Item $CerPath -Force }
}

Write-Host "Creating self-signed code-signing certificate: $Subject" -ForegroundColor Cyan

$cert = New-SelfSignedCertificate `
    -Type CodeSigningCert `
    -Subject $Subject `
    -CertStoreLocation Cert:\CurrentUser\My `
    -NotAfter (Get-Date).AddYears(3)

$securePassword = ConvertTo-SecureString -String $Password -Force -AsPlainText
Export-PfxCertificate -Cert $cert -FilePath $CertPath -Password $securePassword | Out-Null
Export-Certificate -Cert $cert -FilePath $CerPath | Out-Null

Write-Host ""
Write-Host "PFX: $CertPath" -ForegroundColor Green
Write-Host "CER: $CerPath" -ForegroundColor Green
Write-Host ""
Write-Host "For local MSIX install, trust the certificate first (admin PowerShell):" -ForegroundColor Yellow
Write-Host "  Import-Certificate -FilePath `"$CerPath`" -CertStoreLocation Cert:\LocalMachine\Root" -ForegroundColor Yellow
Write-Host ""
Write-Host "NOTE: For Microsoft Store submission, identityName/publisher must match" -ForegroundColor Yellow
Write-Host "Partner Center Product identity. Microsoft re-signs the package on ingest." -ForegroundColor Yellow
exit 0
