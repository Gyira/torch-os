# 构建 WeFlow 的开源 WCDB 包装器（Windows x64）
# 来源：https://github.com/olansoft/weflow-wcdb-api-oss
#
# 用法（在 torch-os 仓库根目录）：
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_weflow_wcdb.ps1
#   powershell ... -OutDir assets\weflow
#
# 前置：Git、CMake >= 3.16、Visual Studio 2022 C++ 桌面开发、vcpkg。
# 本脚本只负责构建；不会替换 WeFlow 安装目录里的 DLL，替换在应用设置里完成。

param(
    [string]$WorkDir = "$env:TEMP\torch-weflow-wcdb",
    [string]$OutDir = "assets\weflow",
    [string]$VcpkgRoot = "C:\vcpkg",
    [string]$SourceUrl = "https://github.com/olansoft/weflow-wcdb-api-oss.git"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$outPath = Join-Path $repoRoot $OutDir

function Require-Command([string]$Name, [string]$Hint) {
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $command) { throw "缺少 $Name。$Hint" }
    return $command.Source
}

Require-Command "git" "安装 Git 后重试。" | Out-Null
Require-Command "cmake" "安装 CMake（>= 3.16）并加入 PATH。" | Out-Null

if (-not (Test-Path (Join-Path $VcpkgRoot "vcpkg.exe"))) {
    throw "没有找到 $VcpkgRoot\vcpkg.exe。请安装 vcpkg，或用 -VcpkgRoot 指定路径。"
}

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $vswhere)) {
    throw "没有找到 Visual Studio。请安装 Visual Studio 2022（C++ 桌面开发）。"
}
$vsInstall = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vsInstall) {
    throw "Visual Studio 没有安装 C++ x64 工具集。"
}

New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
$sourceDir = Join-Path $WorkDir "weflow-wcdb-api-oss"
if (Test-Path $sourceDir) {
    Write-Host "更新已有源码：$sourceDir"
    git -C $sourceDir pull --ff-only
} else {
    git clone $SourceUrl $sourceDir
}

# 用 VS 的开发环境变量运行 cmake，避免找不到 MSVC。
$devShell = Join-Path $vsInstall "Common7\Tools\Launch-VsDevShell.ps1"
if (-not (Test-Path $devShell)) {
    throw "没有找到 Launch-VsDevShell.ps1，请确认 Visual Studio 安装完整。"
}
& $devShell -Arch amd64 -HostArch amd64 | Out-Null

Push-Location $sourceDir
try {
    & "$VcpkgRoot\vcpkg.exe" install curl:x64-windows zstd:x64-windows
    if ($LASTEXITCODE -ne 0) { throw "vcpkg install 失败。" }

    $wcdbDll = Join-Path $sourceDir "deps\win32\x64\WCDB.dll"
    $wcdbDef = Join-Path $sourceDir "deps\win32\x64\WCDB.def"
    $wcdbLib = Join-Path $sourceDir "deps\win32\x64\WCDB.lib"
    if (-not (Test-Path $wcdbDll)) { throw "缺少 deps\win32\x64\WCDB.dll。" }
    if (-not (Test-Path $wcdbLib)) {
        if (-not (Test-Path $wcdbDef)) { throw "缺少 WCDB.def。" }
        & lib /def:"$wcdbDef" /machine:x64 /out:"$wcdbLib"
        if ($LASTEXITCODE -ne 0) { throw "生成 WCDB.lib 失败。" }
    }

    $buildDir = Join-Path $sourceDir "build-win-x64"
    cmake -S . -B $buildDir -G "Visual Studio 17 2022" -A x64 `
        -DCMAKE_TOOLCHAIN_FILE="$VcpkgRoot\scripts\buildsystems\vcpkg.cmake" `
        -DVCPKG_TARGET_TRIPLET=x64-windows `
        -DWCDB_LIB_PATH="$wcdbLib"
    if ($LASTEXITCODE -ne 0) { throw "cmake 配置失败。" }
    cmake --build $buildDir --config Release
    if ($LASTEXITCODE -ne 0) { throw "cmake 构建失败。" }

    $built = Join-Path $buildDir "Release\wcdb_api.dll"
    if (-not (Test-Path $built)) { throw "构建完成但没有找到 $built。" }
    New-Item -ItemType Directory -Force -Path $outPath | Out-Null
    Copy-Item -LiteralPath $built -Destination (Join-Path $outPath "wcdb_api.dll") -Force
    $hash = (Get-FileHash -Algorithm SHA256 (Join-Path $outPath "wcdb_api.dll")).Hash.ToLower()
    Write-Host ""
    Write-Host "已输出：$outPath\wcdb_api.dll"
    Write-Host "SHA-256：$hash"
    Write-Host "下一步：打开恋爱系统 → 设置 → 微信组件修复 → 选择已构建的 DLL 并替换。"
} finally {
    Pop-Location
}
