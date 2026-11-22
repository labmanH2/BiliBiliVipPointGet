# ============================================================
# B站大会员大积分 - Windows 计划任务安装脚本
#
# 触发：每次登录系统后 1 分钟自动运行（可选额外加每日定时兜底）
# 每天只跑一次：由脚本内 run_state.json + 服务端任务状态共同保证
#
# 用法（必须【管理员】PowerShell；系统禁止运行脚本时加 -ExecutionPolicy Bypass）：
#   powershell -ExecutionPolicy Bypass -File .\install_scheduled_task.ps1
#   powershell -ExecutionPolicy Bypass -File .\install_scheduled_task.ps1 -DailyAt "12:30"
#
# 注意：本文件必须保存为 UTF-8 with BOM，否则 Windows PowerShell 按 GBK 读取会乱码报错。
# ============================================================

param(
    [string]$DailyAt = "",          # 例："12:30"；留空表示只使用登录触发
    [switch]$UseConsole             # 加此开关则用 python.exe（运行时会弹出控制台窗口）
)

$ErrorActionPreference = "Stop"

$ScriptDir    = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonScript = Join-Path $ScriptDir "bilibili_vip_point.py"
$LogFile      = Join-Path $ScriptDir "bilibili_vip_point.log"
$ConsoleLog   = Join-Path $ScriptDir "bilibili_vip_point.console.log"
$CmdFile      = Join-Path $ScriptDir "run_daily.cmd"
$TaskName     = "BilibiliVipPointAutoRun"

Write-Host ("=" * 62)
Write-Host "B站大会员大积分 - 计划任务安装"
Write-Host ("=" * 62)

if (-not (Test-Path $PythonScript)) {
    Write-Host "[错误] 找不到脚本: $PythonScript" -ForegroundColor Red
    Read-Host "按回车退出"; exit 1
}

# ---------------- 1. Python ----------------
Write-Host "`n[1/4] 检测 Python 环境..."
$pythonPath = $null
foreach ($cmd in @("python", "python3")) {
    try { $pythonPath = (Get-Command $cmd -ErrorAction Stop).Source; break } catch { }
}
if (-not $pythonPath) {
    Write-Host "  [错误] 未找到 Python，请安装 Python 3.9+ 并加入 PATH" -ForegroundColor Red
    Read-Host "按回车退出"; exit 1
}
Write-Host "  Python: $pythonPath" -ForegroundColor Green

# 调用外部程序必须用【纯路径】变量：带引号的字符串不能直接跟在 & 后面
$pythonExe = $pythonPath

# pythonw.exe：GUI 子系统，运行时不产生任何控制台窗口（计划任务默认用它）
$pythonwPath = Join-Path (Split-Path -Parent $pythonPath) "pythonw.exe"
if (-not (Test-Path $pythonwPath)) { $pythonwPath = $null }
$runnerExe = if ($pythonwPath) { $pythonwPath } else { $pythonExe }
if ($pythonwPath) {
    Write-Host "  pythonw.exe: $pythonwPath（无控制台窗口）" -ForegroundColor Green
} else {
    Write-Host "  [提示] 未找到 pythonw.exe，将改用 VBS 隐藏启动" -ForegroundColor Yellow
}

# ---------------- 2. 依赖 ----------------
Write-Host "`n[2/4] 检查依赖库..."

function Ensure-Pkg([string]$importName, [string]$pipName) {
    & $pythonExe -c "import $importName" 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  $pipName 已安装" -ForegroundColor Green
        return $true
    }
    Write-Host "  正在安装 $pipName ..." -ForegroundColor Yellow
    & $pythonExe -m pip install $pipName
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  $pipName 安装成功" -ForegroundColor Green
        return $true
    }
    Write-Host "  [警告] $pipName 安装失败，可手动执行: pip install $pipName" -ForegroundColor Red
    return $false
}

Ensure-Pkg "requests" "requests" | Out-Null

# 观看剧集默认走纯 HTTP（app_api），不需要 playwright；仅 watch_mode=browser 才需要
& $pythonExe -c "import playwright" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [提示] 未安装 playwright（默认 app_api 模式不需要；如想用 browser 模式请 pip install playwright）" -ForegroundColor Yellow
} else {
    Write-Host "  playwright 已安装" -ForegroundColor Green
}

# ---------------- 3. 选择启动方式（关键：不要弹黑窗） ----------------
# 说明：若用 cmd.exe 作为任务动作，因为它是【控制台子系统】程序，交互式登录时会弹出
#       一个可见的黑窗并持续整个运行（约 12 分钟），必须手动关闭。
#       pythonw.exe / wscript.exe 都是【GUI 子系统】，不会产生任何窗口。
Write-Host "`n[3/4] 选择启动方式..."

$Action = $null
if ($UseConsole) {
    # 调试用：cmd.exe + run_daily.cmd（会弹出控制台窗口，可实时观察输出）
    $cmdContent = "@echo off`r`n`"$runnerExe`" `"$PythonScript`" >> `"$ConsoleLog`" 2>&1`r`n"
    [System.IO.File]::WriteAllText($CmdFile, $cmdContent, [System.Text.Encoding]::Default)
    Write-Host "  [调试模式] 生成包装脚本: $CmdFile" -ForegroundColor Yellow
    Write-Host "  [调试模式] 运行时会出现控制台窗口（这是预期行为）" -ForegroundColor Yellow
    $Action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$CmdFile`"" -WorkingDirectory $ScriptDir
    $launchDesc = "cmd.exe /c run_daily.cmd（会显示窗口）"
} elseif ($pythonwPath) {
    # 推荐：直接调用 pythonw.exe（GUI 子系统 → 完全无窗口）
    $Action = New-ScheduledTaskAction -Execute $pythonwPath -Argument "`"$PythonScript`"" -WorkingDirectory $ScriptDir
    $launchDesc = "pythonw.exe 直接运行（无任何窗口）"
} else {
    # 找不到 pythonw.exe 时，用 wscript + VBS 隐藏调用（wscript 也是 GUI 子系统）
    $vbsPath = Join-Path $ScriptDir "run_hidden.vbs"
    $vbs = 'Set WshShell = CreateObject("WScript.Shell")' + "`r`n" +
           'WshShell.Run """' + $pythonExe + '"" ""' + $PythonScript + '""", 0, False' + "`r`n"
    [System.IO.File]::WriteAllText($vbsPath, $vbs, [System.Text.Encoding]::Default)
    Write-Host "  未找到 pythonw.exe，改用 VBS 隐藏启动: $vbsPath" -ForegroundColor Yellow
    $Action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$vbsPath`"" -WorkingDirectory $ScriptDir
    $launchDesc = "wscript + run_hidden.vbs（无窗口）"
}
Write-Host "  启动方式: $launchDesc" -ForegroundColor Green

# 语法自检（不实际运行任务，避免误触发一次完整流程）
Write-Host "  语法自检..."
& $pythonExe -m py_compile (Join-Path $ScriptDir "bilibili_vip_point.py") `
    (Join-Path $ScriptDir "oqi_blindbox.py") (Join-Path $ScriptDir "notify.py")
if ($LASTEXITCODE -eq 0) {
    Write-Host "  脚本语法检查通过" -ForegroundColor Green
} else {
    Write-Host "  [警告] 脚本语法检查失败，请先修复再运行" -ForegroundColor Red
}

# ---------------- 4. 注册计划任务 ----------------
Write-Host "`n[4/4] 注册计划任务..."
Write-Host "  任务名称: $TaskName"

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "  发现同名任务，先删除..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# 登录触发：登录后延迟 1 分钟运行（等网络/桌面就绪）
$Trigger = New-ScheduledTaskTrigger -AtLogOn
try { $Trigger.Delay = "PT1M" } catch { Write-Host "  [警告] 未能设置登录延迟" -ForegroundColor Yellow }

$Triggers = @($Trigger)
if ($DailyAt -ne "") {
    Write-Host "  额外每日定时兜底: $DailyAt" -ForegroundColor Yellow
    $Triggers += New-ScheduledTaskTrigger -Daily -At $DailyAt
}

$currentUser = "$env:USERDOMAIN\$env:USERNAME"
$Principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited

# 观看剧集要等满 10 分钟倒计时，整体约 12 分钟，超时上限给 40 分钟
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 40) `
    -RestartCount 1 -RestartInterval (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName $TaskName -Description "B站大会员大积分自动获取（本地）" `
    -Action $Action -Trigger $Triggers -Principal $Principal -Settings $Settings -Force | Out-Null

Write-Host "  计划任务创建成功!" -ForegroundColor Green

Write-Host ("=" * 62)
Write-Host "触发时机 : 每次登录系统后 1 分钟" + $(if ($DailyAt) { "，以及每天 $DailyAt" } else { "" })
Write-Host "启动方式 : $launchDesc"
Write-Host "运行频率 : 每天实际只跑一次（run_state.json + 服务端任务状态判断）"
Write-Host "应用日志 : $LogFile"
if ($UseConsole) { Write-Host "控制台日志: $ConsoleLog" }
Write-Host "手动运行 : Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "立即调试 : `"$pythonExe`" `"$PythonScript`""
Write-Host "强制重跑 : `"$pythonExe`" `"$PythonScript`" --force"
Write-Host "卸载     : powershell -ExecutionPolicy Bypass -File .\uninstall_scheduled_task.ps1"
Write-Host ("=" * 62)
Write-Host "提示：默认启动方式不会出现任何黑窗；若想看到运行窗口可用 -UseConsole 重新安装。" -ForegroundColor DarkGray

Read-Host "按回车退出"
