# 一轮开发的固定动作，一条命令跑完：
#   全量校验 → 备份 → 部署 8765/8766 → 自检 →（可选）真实浏览器探针 → 插件重装 → 收尾提醒
#
# 为什么要它：这些步骤以前分散在 4~5 次调用里，每次都要重新等 Edge、重新等重启；
# 而且"重装插件"很容易漏（2026-10-05 就漏过，用户看到 dsh 侧栏"没变化"）。
#
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\round.ps1 -Version 0.96.3
#   ... -SkipBrowser          跳过浏览器探针（最快，只做本地校验 + 部署 + 自检）
#   ... -SkipDeploy           只校验，不重启服务
#   ... -Screenshot artifacts\某次验收.png   兼容旧调用；四库探针的截图固定写到输出目录
param(
  [string]$Version = '',
  [switch]$SkipCheck,
  [switch]$SkipDeploy,
  [switch]$SkipBrowser,
  [switch]$SkipPlugin,
  [string]$BaseUrl = 'http://127.0.0.1:8766',
  [string]$Profile = 'web',
  [string]$Screenshot = ''
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { $python = 'python' }
$failed = New-Object System.Collections.Generic.List[string]
$started = Get-Date

function Step([string]$name, [scriptblock]$body) {
  Write-Host "== $name" -ForegroundColor Cyan
  & $body
  if ($LASTEXITCODE -ne 0 -and $null -ne $LASTEXITCODE) { $failed.Add($name) }
}

function Wait-For([string]$what, [scriptblock]$probe, [int]$seconds = 60) {
  $deadline = (Get-Date).AddSeconds($seconds)
  while ((Get-Date) -lt $deadline) {
    try { if (& $probe) { return $true } } catch { }
    Start-Sleep -Milliseconds 700
  }
  Write-Host "  超时：$what" -ForegroundColor Red
  return $false
}

Push-Location -LiteralPath $root
try {
  if (-not $SkipCheck) { Step '全量校验' { & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'check_all.ps1') } }

  # 备份：先 GET / 拿本地会话，再 POST /api/backups。
  Step '备份' {
    $session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
    Invoke-WebRequest -Uri 'http://127.0.0.1:8765/' -WebSession $session -UseBasicParsing | Out-Null
    $result = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/backups' -Method Post -WebSession $session `
      -ContentType 'application/json' -Body '{}'
    Write-Host ("  {0} verified={1}" -f $result.filename, $result.verified)
    if (-not $result.verified) { throw "备份未通过校验：$($result.filename)" }
    $global:lastBackup = $result.filename
  }

  if (-not $SkipDeploy) {
    Step '部署' {
      # 并发重启会把两个服务一起弄没：两次 `_restart_tmp.ps1` 互相 kill 对方的子进程
      # （2026-10-05 实测：旧的后台部署任务和这个脚本同时跑，8765/8766 全部下线）。
      # 所以用一把带时间戳的锁把重启串行化。
      $lock = Join-Path $root 'data\logs\.restart.lock'
      if (Test-Path -LiteralPath $lock) {
        $age = (Get-Date) - (Get-Item -LiteralPath $lock).LastWriteTime
        if ($age.TotalSeconds -lt 180) {
          throw "已有一次重启在进行（$([math]::Round($age.TotalSeconds)) 秒前开始）——等它跑完再重试"
        }
      }
      New-Item -ItemType File -Path $lock -Force | Out-Null
      try {
        $matched = $false
        Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
          Where-Object { $_.CommandLine -like '*backend.app.run*' -or $_.CommandLine -like '*backend.app.qa_runtime*' } |
          ForEach-Object { $matched = $true; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        if (-not $matched) {
          # CommandLine 可能读不出来（服务进程跑在更高完整性级别时就是空串）：
          # 退回按端口占用找 PID。见 AGENTS.md「踩过的坑」。
          foreach ($connection in @(Get-NetTCPConnection -State Listen -LocalPort 8765, 8766 -ErrorAction SilentlyContinue)) {
            Stop-Process -Id $connection.OwningProcess -Force -ErrorAction SilentlyContinue
          }
        }
        Start-Sleep -Seconds 2
        # 杀不掉就别继续：明确说清是权限问题，别让后面的版本断言给出误导信息。
        $still = @(Get-NetTCPConnection -State Listen -LocalPort 8765, 8766 -ErrorAction SilentlyContinue)
        if ($still.Count) {
          $pids = ($still.OwningProcess | Sort-Object -Unique) -join '、'
          throw "8765/8766 仍被占用（PID $pids）——进程可能运行在更高完整性级别，当前会话停不掉它；请用管理员权限重启，或先手动结束这些进程后重跑"
        }
        # 同步调用重启脚本：Windows PowerShell 5.1 的 `Start-Process -ArgumentList` 不会给
        # 含空格的路径加引号（本仓库路径里就有空格），子脚本会直接启动失败、日志全空
        # （2026-10-05 实测踩到）。同步执行既躲开这个坑，也让“起来了没”一目了然。
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'data\logs\_restart_tmp.ps1')
        # 轮询代替固定 sleep：起来了就走，通常比等 8 秒快。
        $ready = Wait-For '两个服务都起来' {
          $a = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/health' -TimeoutSec 3
          $b = Invoke-RestMethod -Uri 'http://127.0.0.1:8766/api/health' -TimeoutSec 3
          $a.version -and ($a.version -eq $b.version)
        } 150
        if (-not $ready) {
          throw '服务未在 150 秒内就绪；手动跑一次 data\logs\_restart_tmp.ps1 看日志（并确认没有另一个重启在跑）'
        }
        foreach ($port in 8765, 8766) {
          $health = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/health" -TimeoutSec 5
          Write-Host "  $port -> $($health.version)"
        }
        if ($Version) {
          $live = (Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/health' -TimeoutSec 5).version
          if ($live -ne $Version) { throw "运行版本 $live 与期望的 $Version 不一致" }
        }
      } finally {
        Remove-Item -LiteralPath $lock -Force -ErrorAction SilentlyContinue
      }
    }
  }

  Step '发布自检' {
    $report = if ($Version) { "data\diagnostics\release-$Version-smoke.json" } else { 'data\diagnostics\release-smoke.json' }
    & $python (Join-Path $root 'scripts\smoke_release.py') --report $report
  }

  if (-not $SkipBrowser) {
    # 四库探针自己拉起 headless Edge；只访问本机 BaseUrl，不执行提案、不调用模型。
    Step '四库真实浏览器探针' {
      $edge = @(
        'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
        'C:\Program Files\Microsoft\Edge\Application\msedge.exe'
      ) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
      if ($edge) {
        $output = if ($Version) { "data\diagnostics\pillar-probe-$Version" } else { 'data\diagnostics\pillar-probe' }
        & $python (Join-Path $root 'scripts\browser_pillars_probe.py') --base-url $BaseUrl --output $output --port 9431
        if ($LASTEXITCODE -ne 0) { throw '四库真实浏览器探针失败' }
      } else {
        Write-Host '  没找到 Edge，跳过（用 -SkipBrowser 可以静默跳过）' -ForegroundColor Yellow
      }
    }
  }
}
finally {
  Pop-Location
}

$minutes = [math]::Round(((Get-Date) - $started).TotalMinutes, 1)
Write-Host ''
if ($failed.Count) {
  Write-Host "未通过：$($failed -join '、')（用时 $minutes 分钟）" -ForegroundColor Red
  exit 1
}
Write-Host "一轮动作全部完成（用时 $minutes 分钟）：校验、备份、部署、自检$(if (-not $SkipBrowser) { '、浏览器探针' })" -ForegroundColor Green
Write-Host '收尾提醒：' -ForegroundColor Yellow
Write-Host '  1) 改了前端模块 → 刷新工作台；改了 dsh 版本 → 在「与神对话」点「重启 dsh」'
Write-Host '  2) 文档：DEVELOPMENT_LOG.md / RUN_HISTORY.md / USER_FEEDBACK.md 追加本轮条目'
Write-Host '  3) 版本三处同步：application.py 的 APP_VERSION、README.md 首行、WAKE_UP_START.md'
Write-Host '  4) git add -A && git commit -m "<一句话，at X.Y.0>"'
exit 0
