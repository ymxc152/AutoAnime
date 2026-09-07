# AutoAnime 一键停止：按监听端口结束后端（8000）/ 前端（5173）进程
foreach ($p in 8000, 5173) {
    Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {
            Write-Host ("停止进程 PID " + $_ + "（端口 " + $p + "）")
            Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
        }
}
Write-Host "完成。"
