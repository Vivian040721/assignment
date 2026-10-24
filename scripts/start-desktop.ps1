$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot

& npm.cmd run build
if ($LASTEXITCODE -ne 0) { throw "Electron source build failed with status $LASTEXITCODE." }

& npm.cmd start
if ($LASTEXITCODE -ne 0) { throw "Electron startup failed with status $LASTEXITCODE." }
