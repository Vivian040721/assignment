$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
$taskPython = Join-Path $taskRoot '.venv-v2\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    & python.exe -m venv (Join-Path $taskRoot '.venv-v2')
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create .venv-v2.' }
}
& $taskPython -X utf8 -m pip install -e '.[dev,packaging]'
if ($LASTEXITCODE -ne 0) { throw 'Failed to install Python core, development and backend packaging dependencies.' }

if (Test-Path -LiteralPath (Join-Path $taskRoot 'package-lock.json')) {
    & npm.cmd ci
} else {
    & npm.cmd install
}
if ($LASTEXITCODE -ne 0) { throw 'Failed to install the root npm workspaces.' }

Write-Host 'Ready. Run scripts/dev-desktop.ps1 for development or scripts/start-desktop.ps1 for a source build and launch.'
