# One repository-local verification pass: source integrity,
# JavaScript syntax, Python compilation and dependency consistency.
# It never touches personal data and never calls a model.

$ErrorActionPreference = 'Stop'
# 仓库脚本与测试默认 UTF-8（Windows 上尤其重要）：让 check_all 自己设置，
# 避免从不同代码页的终端直接运行时子进程输出乱码导致误报。
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$projectRoot = Split-Path -Parent $PSScriptRoot
$failures = New-Object System.Collections.Generic.List[string]

function Invoke-Step {
  param([string]$Name, [scriptblock]$Command)
  Write-Host "== $Name" -ForegroundColor Cyan
  & $Command
  if ($LASTEXITCODE -ne 0) { $failures.Add($Name) }
}

Push-Location -LiteralPath $projectRoot
try {
  $python = Join-Path $projectRoot '.venv\Scripts\python.exe'
  if (-not (Test-Path -LiteralPath $python)) { $python = 'python' }


  # 把踩过的坑交给机器守住：编码/首字符/section 配对/视图名，以及 import-export 对得上。
  # 这两步很快，而且能在跑测试之前指出“哪个文件被改坏了”。
  Invoke-Step '文本与结构完整性' { & $python (Join-Path $projectRoot 'scripts\check_integrity.py') }
  Invoke-Step '模块面（import/export）' { & node (Join-Path $projectRoot 'scripts\check_module_surface.mjs') }

  Write-Host "== 前端 JS 语法" -ForegroundColor Cyan
  $syntaxBad = $false
  Get-ChildItem -LiteralPath (Join-Path $projectRoot 'frontend') -Recurse -File |
    Where-Object { $_.Extension -in '.js', '.mjs' } |
    ForEach-Object {
      & node --check $_.FullName
      if ($LASTEXITCODE -ne 0) {
        $syntaxBad = $true
        Write-Host "语法错误：$($_.Name)" -ForegroundColor Red
      }
    }
  if ($syntaxBad) { $failures.Add('前端 JS 语法') }

  Invoke-Step 'Obsidian 插件语法' { & node --check (Join-Path $projectRoot 'obsidian-plugin\main.js') }
  $companionRoot = Join-Path $projectRoot 'companion'
  if (Test-Path -LiteralPath (Join-Path $companionRoot 'package.json')) {
    Invoke-Step 'Companion TypeScript 类型' { & npm --prefix $companionRoot run typecheck }
    Invoke-Step 'Companion 同步与文件恢复' { & npm --prefix $companionRoot test }
  }
  Invoke-Step 'Obsidian 插件路径回归' { & node --test (Join-Path $projectRoot 'scripts\test_obsidian_plugin_paths.mjs') }
  Invoke-Step 'UI 输入与交互回归' { & node --test (Join-Path $projectRoot 'scripts\test_ui_polish.mjs') }
  Invoke-Step '记忆可靠性回归' { & $python (Join-Path $projectRoot 'scripts\test_memory_reliability.py') }
  Invoke-Step '角色资料与会话边界回归' { & $python (Join-Path $projectRoot 'scripts\test_role_boundaries.py') }
  Invoke-Step '角色卡与权限契约回归' { & $python (Join-Path $projectRoot 'scripts\test_role_contract.py') }
  Invoke-Step '恋爱小细节回归' { & $python (Join-Path $projectRoot 'scripts\test_love_details.py') }
  Invoke-Step '角色写入与受控动作回归' { & $python (Join-Path $projectRoot 'scripts\test_role_actions.py') }
  Invoke-Step '档案替代一致性与恢复回归' { & $python (Join-Path $projectRoot 'scripts\test_self_archive_ops.py') }
  Invoke-Step '导师自主更新回归' { & $python (Join-Path $projectRoot 'scripts\test_mentor_autonomous.py') }
  Invoke-Step '每日提问归档闭环回归' { & $python (Join-Path $projectRoot 'scripts\test_mentor_questions_flow.py') }
  Invoke-Step '前端目录映射与迁移回归' { & $python (Join-Path $projectRoot 'scripts\test_vault_alignment.py') }

  Invoke-Step 'Python 编译' { & $python -m compileall -q backend scripts }
  Invoke-Step '依赖一致性' { & $python -m pip check }
}
finally {
  Pop-Location
}

if ($failures.Count -gt 0) {
  Write-Host "未通过：$($failures -join '、')" -ForegroundColor Red
  exit 1
}
Write-Host "全部通过：文本与结构完整性、模块面、前端语法、编译、依赖一致性。" -ForegroundColor Green
exit 0
