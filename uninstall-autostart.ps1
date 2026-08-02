#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$TaskName = "AutoAnime WebUI"
)

$ErrorActionPreference = "Continue"

try {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
    Write-Host "[AutoAnime] 已删除计划任务: $TaskName" -ForegroundColor Cyan
} catch {
    Write-Host "[AutoAnime] 计划任务不存在或无法删除: $TaskName" -ForegroundColor DarkGray
}

$startup = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startup "AutoAnime WebUI.lnk"
if (Test-Path $shortcutPath) {
    Remove-Item $shortcutPath -Force
    Write-Host "[AutoAnime] 已删除启动项: $shortcutPath" -ForegroundColor Cyan
} else {
    Write-Host "[AutoAnime] 启动项不存在: $shortcutPath" -ForegroundColor DarkGray
}

Write-Host "[AutoAnime] 取消自启完成" -ForegroundColor Green
