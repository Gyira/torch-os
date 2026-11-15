param([Parameter(Mandatory = $true)][string]$Destination)
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$companion = Join-Path $project 'companion'
$destinationPath = [System.IO.Path]::GetFullPath($Destination)
if (Test-Path -LiteralPath $destinationPath) { throw 'Package already exists; choose a new filename.' }
Push-Location -LiteralPath $project
try {
    & npm --prefix $companion run build
    if ($LASTEXITCODE -ne 0) { throw 'Companion build failed.' }
    $source = Join-Path $companion 'dist\torch-companion'
    $manifest = Get-Content -LiteralPath (Join-Path $source 'manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.id -ne 'torch-companion' -or $manifest.isDesktopOnly -ne $false) { throw 'Plugin identity or mobile declaration mismatch.' }
    Copy-Item -LiteralPath (Join-Path $project 'docs\COMPANION_RUNBOOK.md') -Destination (Join-Path $source 'INSTALL.md')
    $info = [ordered]@{
        version = $manifest.version
        protocol = 1
        built_at = [DateTime]::UtcNow.ToString('o')
        main_sha256 = (Get-FileHash -LiteralPath (Join-Path $source 'main.js') -Algorithm SHA256).Hash.ToLowerInvariant()
        release_kind = 'independent_test_vault'
        external_acceptance = 'not_assessed_by_packager'
    }
    $info | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $source 'BUILD.json') -Encoding UTF8
    $allowed = @('main.js', 'manifest.json', 'styles.css', 'THIRD-PARTY-LICENSE.txt', 'INSTALL.md', 'BUILD.json')
    $entries = @(Get-ChildItem -LiteralPath $source -Force)
    if ($entries.Count -ne $allowed.Count -or @($entries | Where-Object { $_.PSIsContainer -or $_.Name -notin $allowed -or $_.Attributes -band [System.IO.FileAttributes]::ReparsePoint }).Count) {
        throw 'Unexpected build files; refusing to package configuration, journals or notes.'
    }
    $parent = Split-Path -Parent $destinationPath
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    Compress-Archive -LiteralPath $source -DestinationPath $destinationPath -CompressionLevel Optimal
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($destinationPath)
    try {
        $names = @($zip.Entries | Where-Object { -not $_.FullName.EndsWith('/') } | ForEach-Object { $_.FullName.Replace('\', '/') })
        if ($names.Count -ne $allowed.Count -or @($names | Where-Object { $_ -notin @($allowed | ForEach-Object { 'torch-companion/' + $_ }) }).Count) { throw 'ZIP entries differ from the allowlist.' }
    } finally { $zip.Dispose() }
    [PSCustomObject]@{ Package=$destinationPath; Version=$manifest.version; SHA256=(Get-FileHash -LiteralPath $destinationPath -Algorithm SHA256).Hash.ToLowerInvariant(); Files=$allowed.Count } | ConvertTo-Json
} finally { Pop-Location }
