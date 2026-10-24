$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot

& (Join-Path $PSScriptRoot 'build-backend.ps1')
if ($LASTEXITCODE -ne 0) { throw "Python backend packaging failed with status $LASTEXITCODE." }

& npm.cmd run dist
if ($LASTEXITCODE -ne 0) { throw "Electron distribution build failed with status $LASTEXITCODE." }

Write-Host 'Built NSIS and portable packages under data/release/desktop.'
Write-Host 'The unpacked Windows executable is data/release/desktop/win-unpacked/Paper Agent.exe.'
