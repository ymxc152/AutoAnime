# AutoAnime 一键启动（Windows）：依赖安装 + 建库 + 后端/前端 + 打开浏览器
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host "================================================"
Write-Host "  AutoAnime 启动器"
Write-Host "================================================"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "[错误] 未找到 uv，请先安装：https://docs.astral.sh/uv/"
    Read-Host "按回车退出"; exit 1
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host "[错误] 未找到 Node.js（需 >= 20），请先安装：https://nodejs.org/"
    Read-Host "按回车退出"; exit 1
}

if (-not (Test-Path .env)) {
    Write-Host "[初始化] 从 .env.example 生成 .env，密钥请自行填写..."
    Copy-Item .env.example .env
}

Write-Host "[检查] Python 依赖（uv sync，已就绪时秒过）..."
uv sync
if ($LASTEXITCODE -ne 0) { Write-Host "[错误] uv sync 失败，请看上方报错"; Read-Host "按回车退出"; exit 1 }

if (-not (Test-Path frontend\node_modules)) {
    Write-Host "[首次运行] 安装前端依赖（npm install，只需一次）..."
    Push-Location frontend
    npm install
    $npmExit = $LASTEXITCODE
    Pop-Location
    if ($npmExit -ne 0) { Write-Host "[错误] npm install 失败"; Read-Host "按回车退出"; exit 1 }
}

Write-Host "[检查] 数据库（幂等，可重复执行）..."
uv run autoanime init-db
if ($LASTEXITCODE -ne 0) { Write-Host "[错误] init-db 失败，请看上方报错"; Read-Host "按回车退出"; exit 1 }

Write-Host "[启动] 后端 API（新窗口，默认 127.0.0.1:8000）..."
Start-Process cmd -ArgumentList '/k', 'uv run python -m autoanime.api serve --dev' -WorkingDirectory $root

Write-Host "[启动] 前端 WebUI（新窗口，默认 http://localhost:5173，已连真后端）..."
Start-Process cmd -ArgumentList '/k', 'set VITE_USE_MOCK=0&& npm run dev' -WorkingDirectory (Join-Path $root 'frontend')

Write-Host "[等待] 前端就绪后自动打开浏览器（最多 60 秒）..."
# vite 可能只监听 IPv6(::1) 或 IPv4(127.0.0.1)，两个都探
$probeUrls = 'http://[::1]:5173/', 'http://127.0.0.1:5173/'
$ok = $false
for ($i = 0; $i -lt 60; $i++) {
    foreach ($u in $probeUrls) {
        try {
            if ((Invoke-WebRequest $u -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200) { $ok = $true; break }
        } catch {}
    }
    if ($ok) { break }
    Start-Sleep -Seconds 1
}
if ($ok) {
    Write-Host "[完成] WebUI 已就绪，正在打开浏览器：http://localhost:5173"
    Start-Process 'http://localhost:5173'
} else {
    Write-Host "[警告] 60 秒内前端未就绪，请查看两个新窗口里的报错；也可手动打开 http://localhost:5173"
}

Write-Host ""
Write-Host "停止服务：直接关闭两个服务窗口，或双击 stop.cmd"
