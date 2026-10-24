$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot

& npm.cmd run dev
if ($LASTEXITCODE -ne 0) { throw "Electron development server stopped with status $LASTEXITCODE." }
