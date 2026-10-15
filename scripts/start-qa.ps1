param(
  [ValidateRange(1, 65535)]
  [int]$Port = 8766,
  [ValidatePattern('^qa-[a-z0-9][a-z0-9-]{0,63}$')]
  [string]$Workspace,
  [switch]$Check
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$qaArguments = @('-m', 'backend.app.qa_runtime', '--port', "$Port")
if ($Workspace) { $qaArguments += @('--workspace', $Workspace) }
if ($Check) { $qaArguments += '--check' }
Push-Location -LiteralPath $projectRoot
try {
  $qaPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
  if (Test-Path -LiteralPath $qaPython) {
    & $qaPython @qaArguments
  } else {
    & python @qaArguments
  }
  $qaExitCode = $LASTEXITCODE
} finally {
  Pop-Location
}
exit $qaExitCode
