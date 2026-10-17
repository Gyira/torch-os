# 薪火OS · Obsidian 插件安装器（0.99.1）
# 把仓库里的 obsidian-plugin/ 复制进指定 vault 的 .obsidian/plugins/torch/，
# 首次安装时预置 data.json（本机仓库路径），并把 torch 加进 community-plugins.json（预启用）。
# 只写这两类文件，不碰 vault 里的任何笔记。
param([string]$VaultPath = '')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not $VaultPath) { throw "Pass -VaultPath <your Obsidian vault folder>" }
$source = Join-Path $root 'obsidian-plugin'
$target = Join-Path $VaultPath '.obsidian\plugins\torch'
New-Item -ItemType Directory -Force -Path $target | Out-Null
foreach ($file in 'manifest.json', 'main.js', 'styles.css') {
  Copy-Item -LiteralPath (Join-Path $source $file) -Destination $target -Force
}
$utf8 = New-Object System.Text.UTF8Encoding($false)
$dataPath = Join-Path $target 'data.json'
if (-not (Test-Path -LiteralPath $dataPath)) {
  $repoForwards = $root -replace '\\', '/'
  $data = '{' + [char]13 + [char]10 + '  "baseUrl": "http://127.0.0.1:8765",' + [char]13 + [char]10 + '  "repoRoot": "' + $repoForwards + '",' + [char]13 + [char]10 + '  "pythonPath": "",' + [char]13 + [char]10 + '  "autoStart": true' + [char]13 + [char]10 + '}'
  [System.IO.File]::WriteAllText($dataPath, $data, $utf8)
}
$communityPath = Join-Path $VaultPath '.obsidian\community-plugins.json'
$ids = @()
if (Test-Path -LiteralPath $communityPath) {
  $ids = @((Get-Content -LiteralPath $communityPath -Raw | ConvertFrom-Json))
}
if ($ids -notcontains 'torch') { $ids += 'torch' }
$json = '[' + (($ids | ForEach-Object { '"' + $_ + '"' }) -join ',') + ']'
[System.IO.File]::WriteAllText($communityPath, $json, $utf8)
Write-Host "installed to $target"
Get-ChildItem -LiteralPath $target | Select-Object Name, Length | Format-Table -AutoSize
