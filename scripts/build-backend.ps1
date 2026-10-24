$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
$taskPython = Join-Path $taskRoot '.venv-v2\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Run scripts/setup.ps1 first.' }
& $taskPython -X utf8 -m PyInstaller --noconfirm --distpath data/release/backend --workpath data/build/backend scripts/backend.spec
if ($LASTEXITCODE -ne 0) { throw 'Python backend packaging failed.' }
