param([switch]$SeedExamples)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    $python = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $python -or $python -like '*WindowsApps*') {
        throw 'Python not found (the Windows Store alias does not count). Install it first: winget install --id Python.Python.3.12 --source winget --scope user , then open a NEW terminal and re-run this script.'
    }
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create virtual environment.' }
}
$venvPython = '.venv\Scripts\python.exe'
# 装依赖先体检：requirements.lock.txt 里钉住的版本本机已经全对上，就不重复安装（也不用再等一轮网络）。
# check_deps.py 只读、不联网：0 = 已装好；1 = 有缺失或版本不符；2 = 读不了文件。
& $venvPython scripts\check_deps.py
$depCheck = $LASTEXITCODE
if ($depCheck -eq 0) {
    Write-Output '依赖已经装好，跳过 pip install。'
} elseif ($depCheck -eq 2) {
    throw 'Could not read requirements.lock.txt (see the output of scripts\check_deps.py above).'
} else {
    & $venvPython -m pip install -r requirements.lock.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. If pypi.org times out on your network, retry with a mirror: .\.venv\Scripts\python.exe -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.lock.txt' }
    # 装完复核：pip 说成功了版本却仍对不上（镜像/代理给了别的版本），当场说清楚，别留到起服务才炸。
    & $venvPython scripts\check_deps.py
    if ($LASTEXITCODE -ne 0) { throw 'Dependencies still do not match requirements.lock.txt after installation. Check the entries listed above (a mirror or proxy may have served different versions), then reinstall from https://pypi.org/simple .' }
}
if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
$vault = 'data\vault'
# 示例数据只在 vault 还不存在时自动铺（起过一次服务就会建出目录，那时自动判断已无从下手）。
# 已有库想补一份示例，用 -SeedExamples 显式合并；robocopy 不删文件，但同名文件会被示例覆盖。
if ($SeedExamples -or -not (Test-Path -LiteralPath $vault)) {
    New-Item -ItemType Directory -Force -Path $vault | Out-Null
    robocopy 'examples\vault' $vault /E /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Could not copy examples\vault into $vault (robocopy exit $LASTEXITCODE)." }
}
Write-Output 'Setup complete. Next: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start.ps1'
