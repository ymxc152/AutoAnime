#Requires -Version 5.1
<#
.SYNOPSIS
  停止由 start-autoanime 启动的 Web / Worker 进程。
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Continue"

function Stop-MatchingPython([string]$ScriptLeaf) {
    $pattern = [regex]::Escape($ScriptLeaf)
    $procs = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe' OR Name = 'py.exe'" -ErrorAction SilentlyContinue
    $stopped = 0
    foreach ($proc in $procs) {
        if ($proc.CommandLine -and ($proc.CommandLine -match $pattern)) {
            Write-Host "[AutoAnime] 停止 PID $($proc.ProcessId): $ScriptLeaf" -ForegroundColor Yellow
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
            $stopped++
        }
    }
    if ($stopped -eq 0) {
        Write-Host "[AutoAnime] 未发现运行中的 $ScriptLeaf" -ForegroundColor DarkGray
    }
}

Stop-MatchingPython "AutoAnimeWeb.py"
Stop-MatchingPython "AutoAnimeWorker.py"
Write-Host "[AutoAnime] 已完成停止" -ForegroundColor Cyan
