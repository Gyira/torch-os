$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
if (Test-Path ".venv\Scripts\python.exe") {
  & ".venv\Scripts\python.exe" -m backend.app.run
} else {
  & python -m backend.app.run
}
