# ============================================================
# B站大会员大积分 - 卸载计划任务
# ============================================================

$TaskName = "BilibiliVipPointAutoRun"

Write-Host "=" * 60
Write-Host "卸载 B站大会员大积分计划任务"
Write-Host "=" * 60

$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "[成功] 已删除计划任务: $TaskName" -ForegroundColor Green
} else {
    Write-Host "[提示] 未找到计划任务: $TaskName" -ForegroundColor Yellow
}

Read-Host "按回车退出"
