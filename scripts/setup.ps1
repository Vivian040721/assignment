$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create virtual environment' }
}
& '.\.venv\Scripts\python.exe' -m pip install -r requirements-dev.lock
if ($LASTEXITCODE -ne 0) { throw 'Failed to install locked dependencies' }
& '.\.venv\Scripts\python.exe' -m pip install --no-deps -e .
if ($LASTEXITCODE -ne 0) { throw 'Failed to install project' }
Write-Host 'Ready. Run: .\.venv\Scripts\python.exe -m paper_agent.cli --mode demo'
