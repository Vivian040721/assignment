param([ValidateSet('start', 'status', 'stop')][string]$Action = 'status')
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot

& node.exe (Join-Path $PSScriptRoot 'manage-service.mjs') $Action
if ($LASTEXITCODE -ne 0) { throw "Service $Action failed with status $LASTEXITCODE." }
