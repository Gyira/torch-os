$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create virtual environment.' }
}
& '.venv\Scripts\python.exe' -m pip install -r requirements.lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
if (-not (Test-Path -LiteralPath 'data\vault')) {
    New-Item -ItemType Directory -Force -Path data | Out-Null
    Copy-Item -LiteralPath 'examples\vault' -Destination 'data\vault' -Recurse
}
Write-Output 'Setup complete. Run scripts\start.ps1 or double-click Start.cmd.'
