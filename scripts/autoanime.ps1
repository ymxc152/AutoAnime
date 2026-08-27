#Requires -Version 5.1
# AutoAnime Web + Worker launcher. Called by start-autoanime.bat / stop-autoanime.bat.
# Encoding: UTF-8 with BOM, CRLF. Do not name a parameter $Args.

param(
    [ValidateSet("start", "stop", "status")]
    [string]$Action = "start",
    [string]$DataDir = "C:\ProgramData\AutoAnime",
    [string]$HostAddress = "0.0.0.0",
    [int]$Port = 8765,
    [switch]$SecureCookies,
    [switch]$NoBuild,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

function Write-Info([string]$Message) { Write-Host "[AutoAnime] $Message" -ForegroundColor Cyan }
function Write-Warn([string]$Message) { Write-Host "[AutoAnime] $Message" -ForegroundColor Yellow }
function Write-Err([string]$Message) { Write-Host "[AutoAnime] $Message" -ForegroundColor Red }

function Get-RunDir([string]$Directory) {
    $runDir = Join-Path $Directory "run"
    New-Item -ItemType Directory -Path $runDir -Force | Out-Null
    return $runDir
}

function Get-PidPath([string]$Directory, [string]$Name) {
    return Join-Path (Get-RunDir $Directory) "$Name.pid"
}

function Read-PidFile([string]$Path) {
    if (-not (Test-Path $Path)) { return $null }
    $raw = (Get-Content -Path $Path -TotalCount 1 -ErrorAction SilentlyContinue)
    if (-not $raw) { return $null }
    $id = 0
    if ([int]::TryParse("$raw".Trim(), [ref]$id) -and $id -gt 0) { return $id }
    return $null
}

function Write-PidFile([string]$Path, [int]$ProcessId) {
    Set-Content -Path $Path -Value "$ProcessId" -Encoding ascii
}

function Get-PythonProcesses {
    return @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe' OR Name = 'py.exe'" -ErrorAction SilentlyContinue)
}

function Test-CommandMatches([string]$CommandLine, [string]$ScriptLeaf, [string]$Directory) {
    if (-not $CommandLine) { return $false }
    if ($CommandLine -notmatch [regex]::Escape($ScriptLeaf)) { return $false }
    $normalized = $Directory.TrimEnd("\", "/")
    $escaped = [regex]::Escape($normalized)
    if ($CommandLine -match ("--data-dir\s+[""']?" + $escaped)) { return $true }
    $forward = $normalized -replace "\\", "/"
    $escapedFwd = [regex]::Escape($forward)
    return ($CommandLine -match ("--data-dir\s+[""']?" + $escapedFwd))
}

function Get-ManagedProcess([string]$ScriptLeaf, [string]$Directory, [string]$PidFile) {
    $recorded = Read-PidFile $PidFile
    $procs = Get-PythonProcesses
    foreach ($proc in $procs) {
        if ($recorded -and [int]$proc.ProcessId -eq $recorded) {
            return $proc
        }
    }
    foreach ($proc in $procs) {
        if (Test-CommandMatches $proc.CommandLine $ScriptLeaf $Directory) {
            return $proc
        }
    }
    return $null
}

function Find-Python {
    foreach ($rel in @(".venv\Scripts\python.exe", "venv\Scripts\python.exe")) {
        $candidate = Join-Path $ProjectRoot $rel
        if (Test-Path $candidate) { return $candidate }
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notmatch "WindowsApps") { return $cmd.Source }
    $pyLauncher = Join-Path $env:WINDIR "py.exe"
    if (Test-Path $pyLauncher) { return $pyLauncher }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { return $py.Source }
    return $null
}

function Get-PythonInvocation([string]$PythonPath) {
    if ((Split-Path $PythonPath -Leaf) -ieq "py.exe") {
        return @{ Exe = $PythonPath; Prefix = @("-3") }
    }
    return @{ Exe = $PythonPath; Prefix = @() }
}

function Invoke-Python {
    param(
        [hashtable]$Python,
        [string[]]$PyArgs
    )
    $all = @()
    $all += $Python.Prefix
    $all += $PyArgs
    $nativeOut = & $Python.Exe @all
    $exitCode = $LASTEXITCODE
    if ($nativeOut) { $nativeOut | Out-Host }
    return $exitCode
}

function Ensure-PythonDeps([hashtable]$Python) {
    $marker = Join-Path $ProjectRoot ".venv\.autoanime-deps.ok"
    $requirements = Join-Path $ProjectRoot "requirements.txt"
    if ((Test-Path $marker) -and (Test-Path $requirements)) {
        if ((Get-Item $marker).LastWriteTimeUtc -ge (Get-Item $requirements).LastWriteTimeUtc) {
            return
        }
    }
    Write-Info "Installing Python dependencies..."
    $code = Invoke-Python -Python $Python -PyArgs @("-m", "pip", "install", "-r", "requirements.txt")
    if ($code -ne 0) { throw "pip install -r requirements.txt failed (exit=$code)" }
    "ok" | Set-Content -Path $marker -Encoding ascii
}

function Test-FrontendStale {
    $distIndex = Join-Path $ProjectRoot "webui\dist\index.html"
    if (-not (Test-Path $distIndex)) { return $true }
    $distTime = (Get-Item $distIndex).LastWriteTimeUtc
    $watch = @(
        (Join-Path $ProjectRoot "webui\package.json"),
        (Join-Path $ProjectRoot "webui\index.html"),
        (Join-Path $ProjectRoot "webui\vite.config.ts"),
        (Join-Path $ProjectRoot "webui\src")
    )
    foreach ($path in $watch) {
        if (-not (Test-Path $path)) { continue }
        $item = Get-Item $path
        if ($item.PSIsContainer) {
            $newer = Get-ChildItem -Path $path -Recurse -File -ErrorAction SilentlyContinue |
                Where-Object { $_.LastWriteTimeUtc -gt $distTime } |
                Select-Object -First 1
            if ($newer) { return $true }
        } elseif ($item.LastWriteTimeUtc -gt $distTime) {
            return $true
        }
    }
    return $false
}

function Ensure-Frontend {
    $distIndex = Join-Path $ProjectRoot "webui\dist\index.html"
    $nodeModules = Join-Path $ProjectRoot "webui\node_modules"
    if ($NoBuild) {
        if (-not (Test-Path $distIndex)) {
            throw "Missing webui\dist. Build once: npm --prefix webui install && npm --prefix webui run build"
        }
        Write-Info "NoBuild: using existing webui\dist"
        return
    }
    if ((Test-Path $distIndex) -and -not (Test-FrontendStale)) {
        Write-Info "Frontend dist is current; skip rebuild"
        return
    }
    $pkg = Get-Command pnpm -ErrorAction SilentlyContinue
    if (-not $pkg) { $pkg = Get-Command npm -ErrorAction SilentlyContinue }
    if (-not $pkg) {
        throw "No pnpm/npm found. Install Node.js 20+, then build webui."
    }
    $tool = $pkg.Name
    $webui = Join-Path $ProjectRoot "webui"
    if (-not (Test-Path $nodeModules)) {
        Write-Info "Installing frontend dependencies ($tool)..."
        if ($tool -eq "pnpm") { & pnpm --dir $webui install } else { & npm --prefix $webui install }
        if ($LASTEXITCODE -ne 0) { throw "$tool install failed" }
    }
    Write-Info "Building frontend ($tool)..."
    if ($tool -eq "pnpm") { & pnpm --dir $webui build } else { & npm --prefix $webui run build }
    if ($LASTEXITCODE -ne 0) { throw "$tool build failed" }
    if (-not (Test-Path $distIndex)) {
        throw "Build finished but webui\dist\index.html is still missing"
    }
}

function Get-ListenerPid([int]$ListenPort) {
    try {
        $conn = Get-NetTCPConnection -LocalPort $ListenPort -State Listen -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($conn) { return [int]$conn.OwningProcess }
    } catch { }
    $line = netstat -ano | Select-String -Pattern (":$ListenPort\s+.+LISTENING\s+(\d+)") | Select-Object -First 1
    if ($line -and $line.Matches[0].Groups[1].Value) {
        return [int]$line.Matches[0].Groups[1].Value
    }
    return $null
}

function Wait-Health([int]$ListenPort, [int]$TimeoutSeconds = 30) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $url = "http://127.0.0.1:$ListenPort/health/live"
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) { return $true }
        } catch { }
        Start-Sleep -Milliseconds 400
    }
    return $false
}

function Stop-Managed {
    param(
        [string]$Name,
        [string]$ScriptLeaf,
        [string]$Directory
    )
    $pidFile = Get-PidPath $Directory $Name
    $proc = Get-ManagedProcess $ScriptLeaf $Directory $pidFile
    if (-not $proc) {
        if (Test-Path $pidFile) { Remove-Item $pidFile -Force -ErrorAction SilentlyContinue }
        Write-Info "$Name is not running for this data dir"
        return
    }
    Write-Info ("Stopping {0} (PID {1})" -f $Name, $proc.ProcessId)
    Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    $deadline = (Get-Date).AddSeconds(8)
    while ((Get-Date) -lt $deadline) {
        $still = Get-Process -Id $proc.ProcessId -ErrorAction SilentlyContinue
        if (-not $still) { break }
        Start-Sleep -Milliseconds 200
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

function Show-Status {
    $webPidFile = Get-PidPath $DataDir "web"
    $workerPidFile = Get-PidPath $DataDir "worker"
    $web = Get-ManagedProcess "AutoAnimeWeb.py" $DataDir $webPidFile
    $worker = Get-ManagedProcess "AutoAnimeWorker.py" $DataDir $workerPidFile
    $listener = Get-ListenerPid $Port
    Write-Info "Project: $ProjectRoot"
    Write-Info "DataDir: $DataDir"
    if ($web) { Write-Info ("Web: PID {0}" -f $web.ProcessId) } else { Write-Warn "Web: not running" }
    if ($worker) { Write-Info ("Worker: PID {0}" -f $worker.ProcessId) } else { Write-Warn "Worker: not running (qB/scan/execute will queue and wait)" }
    if ($listener) { Write-Info ("Port {0}: listening PID {1}" -f $Port, $listener) } else { Write-Warn ("Port {0}: not listening" -f $Port) }
    if (Wait-Health $Port 2) { Write-Info "Health: live" } else { Write-Warn "Health: not ready" }
}

function Start-AutoAnimeProcess {
    param(
        [string]$Name,
        [string]$ScriptLeaf,
        [hashtable]$Python,
        [string]$ScriptPath,
        [string[]]$ScriptArgs,
        [string]$StdOutLog,
        [string]$StdErrLog
    )

    $pidFile = Get-PidPath $DataDir $Name
    $existing = Get-ManagedProcess $ScriptLeaf $DataDir $pidFile
    if ($existing) {
        Write-PidFile $pidFile $existing.ProcessId
        Write-Warn ("{0} already running for this data dir (PID {1}); skip" -f $Name, $existing.ProcessId)
        return $existing.ProcessId
    }

    if (Test-Path $StdOutLog) { Remove-Item $StdOutLog -Force -ErrorAction SilentlyContinue }
    if (Test-Path $StdErrLog) { Remove-Item $StdErrLog -Force -ErrorAction SilentlyContinue }

    $argList = @()
    $argList += $Python.Prefix
    $argList += $ScriptPath
    $argList += $ScriptArgs

    Write-Info "Starting $Name ..."
    Write-Info ("  cmd: {0} {1}" -f $Python.Exe, ($argList -join " "))
    Write-Info "  log: $StdOutLog"

    $process = Start-Process `
        -FilePath $Python.Exe `
        -ArgumentList $argList `
        -WorkingDirectory $ProjectRoot `
        -RedirectStandardOutput $StdOutLog `
        -RedirectStandardError $StdErrLog `
        -WindowStyle Hidden `
        -PassThru

    Start-Sleep -Seconds 1
    if ($process.HasExited) {
        $errTail = ""
        if (Test-Path $StdErrLog) {
            $errTail = ((Get-Content $StdErrLog -Tail 40 -ErrorAction SilentlyContinue) -join "`n")
        }
        if (-not $errTail -and (Test-Path $StdOutLog)) {
            $errTail = ((Get-Content $StdOutLog -Tail 40 -ErrorAction SilentlyContinue) -join "`n")
        }
        throw ("{0} exited immediately (exit={1}). {2}" -f $Name, $process.ExitCode, $errTail)
    }
    Write-PidFile $pidFile $process.Id
    Write-Info ("{0} started (PID {1})" -f $Name, $process.Id)
    return $process.Id
}

function Start-Services {
    Write-Info "Project: $ProjectRoot"
    Write-Info "DataDir: $DataDir"

    $pythonPath = Find-Python
    if (-not $pythonPath) {
        throw "Python not found. Install Python 3.11+ or create .venv and pip install -r requirements.txt"
    }
    $python = Get-PythonInvocation $pythonPath
    Write-Info "Python: $($python.Exe)"

    $logDir = Join-Path $DataDir "logs"
    $dataDbDir = Join-Path $DataDir "data"
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    New-Item -ItemType Directory -Path $dataDbDir -Force | Out-Null
    Get-RunDir $DataDir | Out-Null

    Ensure-PythonDeps $python
    Ensure-Frontend

    $webScript = Join-Path $ProjectRoot "AutoAnimeWeb.py"
    $workerScript = Join-Path $ProjectRoot "AutoAnimeWorker.py"
    if (-not (Test-Path $webScript)) { throw "Missing $webScript" }
    if (-not (Test-Path $workerScript)) { throw "Missing $workerScript" }

    $webPidFile = Get-PidPath $DataDir "web"
    $existingWeb = Get-ManagedProcess "AutoAnimeWeb.py" $DataDir $webPidFile
    $listener = Get-ListenerPid $Port
    if ($listener) {
        if ($existingWeb -and [int]$existingWeb.ProcessId -eq $listener) {
            Write-Warn ("Port {0} already served by this instance (PID {1})" -f $Port, $listener)
        } else {
            $other = Get-Process -Id $listener -ErrorAction SilentlyContinue
            $otherName = if ($other) { $other.ProcessName } else { "unknown" }
            throw ("Port {0} is already in use by PID {1} ({2}). Stop that process, or pass -Port, or run stop-autoanime.bat -DataDir `"$DataDir`"." -f $Port, $listener, $otherName)
        }
    }

    $webArgs = @(
        "--data-dir", $DataDir,
        "--host", $HostAddress,
        "--port", "$Port"
    )
    if (-not $SecureCookies) { $webArgs += "--insecure-http" }

    $stamp = Get-Date -Format "yyyyMMdd"
    Start-AutoAnimeProcess `
        -Name "web" `
        -ScriptLeaf "AutoAnimeWeb.py" `
        -Python $python `
        -ScriptPath $webScript `
        -ScriptArgs $webArgs `
        -StdOutLog (Join-Path $logDir "web-$stamp.log") `
        -StdErrLog (Join-Path $logDir "web-$stamp.err.log") | Out-Null

    Start-AutoAnimeProcess `
        -Name "worker" `
        -ScriptLeaf "AutoAnimeWorker.py" `
        -Python $python `
        -ScriptPath $workerScript `
        -ScriptArgs @("--data-dir", $DataDir) `
        -StdOutLog (Join-Path $logDir "worker-$stamp.log") `
        -StdErrLog (Join-Path $logDir "worker-$stamp.err.log") | Out-Null

    if (-not (Wait-Health $Port 40)) {
        $errLog = Join-Path $logDir "web-$stamp.err.log"
        $tail = ""
        if (Test-Path $errLog) { $tail = ((Get-Content $errLog -Tail 20 -ErrorAction SilentlyContinue) -join "`n") }
        throw ("Web did not become ready at http://127.0.0.1:{0}/health/live. {1}" -f $Port, $tail)
    }

    $url = if ($HostAddress -eq "0.0.0.0") { "http://127.0.0.1:$Port" } else { "http://${HostAddress}:$Port" }
    Write-Host ""
    Write-Info "Ready"
    Write-Info "Console: $url"
    Write-Info "Worker must stay running for qB webhook, scheduled scan, execute, and rollback"
    Write-Info "Default admin: admin / AutoAnime-Admin-ChangeMe!"
    Write-Info "Loopback is passwordless unless disabled in Settings"
    Write-Info "Stop: .\stop-autoanime.bat"
    Write-Host ""
}

function Stop-Services {
    if ($Force) {
        Write-Warn "Force: stopping every AutoAnimeWeb.py / AutoAnimeWorker.py (includes leftover e2e)"
        $procs = Get-PythonProcesses
        foreach ($proc in $procs) {
            if ($proc.CommandLine -and ($proc.CommandLine -match "AutoAnimeWeb\.py|AutoAnimeWorker\.py")) {
                Write-Info ("Force stop PID {0}" -f $proc.ProcessId)
                Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
            }
        }
        return
    }
    Stop-Managed -Name "worker" -ScriptLeaf "AutoAnimeWorker.py" -Directory $DataDir
    Stop-Managed -Name "web" -ScriptLeaf "AutoAnimeWeb.py" -Directory $DataDir
    Write-Info "Stopped instance for $DataDir"
}

try {
    switch ($Action) {
        "start" { Start-Services }
        "stop" { Stop-Services }
        "status" { Show-Status }
    }
} catch {
    Write-Err $_.Exception.Message
    exit 1
}
exit 0
