#Requires -Version 5.1
<#
.SYNOPSIS
  注册当前用户登录时自动启动 AutoAnime Web + Worker。
.DESCRIPTION
  使用「当前用户」计划任务（一般无需管理员）。
  同时在开始菜单「启动」文件夹放一份快捷方式作为备份。
#>
[CmdletBinding()]
param(
    [string]$DataDir = "C:\ProgramData\AutoAnime",
    [string]$HostAddress = "0.0.0.0",
    [int]$Port = 8765,
    [string]$TaskName = "AutoAnime WebUI"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path $PSScriptRoot).Path
$StartScript = Join-Path $ProjectRoot "start-autoanime.ps1"
$StartBat = Join-Path $ProjectRoot "start-autoanime.bat"

if (-not (Test-Path $StartScript)) {
    throw "找不到 $StartScript"
}

$argument = @(
    "-NoProfile"
    "-ExecutionPolicy", "Bypass"
    "-WindowStyle", "Hidden"
    "-File", "`"$StartScript`""
    "-DataDir", "`"$DataDir`""
    "-HostAddress", "`"$HostAddress`""
    "-Port", "$Port"
) -join " "

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argument -WorkingDirectory $ProjectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigger.Delay = "PT20S"
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null

$startup = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startup "AutoAnime WebUI.lnk"
$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $StartBat
$shortcut.WorkingDirectory = $ProjectRoot
$shortcut.WindowStyle = 7
$shortcut.Description = "Start AutoAnime Web console and Worker"
$shortcut.Save()

Write-Host "[AutoAnime] 已注册开机/登录自启" -ForegroundColor Cyan
Write-Host "  计划任务: $TaskName" -ForegroundColor Green
Write-Host "  启动项:   $shortcutPath" -ForegroundColor Green
Write-Host "  数据目录: $DataDir" -ForegroundColor Green
Write-Host "  控制台:   http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host ""
Write-Host "取消自启请运行: .\uninstall-autostart.bat" -ForegroundColor DarkGray
Write-Host "也可手动: 任务计划程序 删除「$TaskName」；启动文件夹删除快捷方式" -ForegroundColor DarkGray
