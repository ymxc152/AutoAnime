#Requires -Version 5.1
param(
    [string]$DataDir = "C:\ProgramData\AutoAnime",
    [string]$HostAddress = "0.0.0.0",
    [int]$Port = 8765,
    [switch]$SecureCookies,
    [switch]$NoBuild
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path $PSScriptRoot).Path
Set-Location $ProjectRoot

function Write-Info([string]$Message) { Write-Host "[AutoAnime] $Message" -ForegroundColor Cyan }
function Write-Warn([string]$Message) { Write-Host "[AutoAnime] $Message" -ForegroundColor Yellow }
function Write-Err([string]$Message)  { Write-Host "[AutoAnime] $Message" -ForegroundColor Red }

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
    # NB: parameter must NOT be named $Args - PowerShell's automatic $args
    # variable shadows it and binding silently fails (args arrive empty).
    $all = @()
    $all += $Python.Prefix
    $all += $PyArgs
    # Capture native stdout so it does not become part of this function's
    # return value (PowerShell adds uncaptured native output to the output
    # stream), then echo it and return only the real exit code.
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

function Test-ProcessRunning([string]$ScriptName) {
    $pattern = [regex]::Escape($ScriptName)
    $procs = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe' OR Name = 'py.exe'" -ErrorAction SilentlyContinue
    foreach ($proc in $procs) {
        if ($proc.CommandLine -and ($proc.CommandLine -match $pattern)) {
            return $true
        }
    }
    return $false
}

function Ensure-Frontend {
    $distIndex = Join-Path $ProjectRoot "webui\dist\index.html"
    if (Test-Path $distIndex) {
        Write-Info "Frontend build found: webui\dist"
        return
    }
    if ($NoBuild) {
        throw "Missing webui\dist. Run: pnpm --dir webui install && pnpm --dir webui build"
    }
    $pnpm = Get-Command pnpm -ErrorAction SilentlyContinue
    if (-not $pnpm) {
        throw "pnpm not found and webui\dist missing. Install Node.js 20+ / pnpm 10+, then build frontend."
    }
    Write-Info "Building frontend for the first time..."
    & pnpm --dir (Join-Path $ProjectRoot "webui") install
    if ($LASTEXITCODE -ne 0) { throw "pnpm install failed" }
    & pnpm --dir (Join-Path $ProjectRoot "webui") build
    if ($LASTEXITCODE -ne 0) { throw "pnpm build failed" }
    if (-not (Test-Path $distIndex)) {
        throw "Build finished but webui\dist\index.html is still missing"
    }
}

function Start-AutoAnimeProcess {
    param(
        [string]$Name,
        [hashtable]$Python,
        [string]$ScriptPath,
        [string[]]$ScriptArgs,
        [string]$StdOutLog,
        [string]$StdErrLog
    )

    $leaf = Split-Path $ScriptPath -Leaf
    if (Test-ProcessRunning $leaf) {
        Write-Warn "$Name is already running; skip"
        return
    }

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
            $errTail = ((Get-Content $StdErrLog -Tail 30 -ErrorAction SilentlyContinue) -join "`n")
        }
        if (-not $errTail -and (Test-Path $StdOutLog)) {
            $errTail = ((Get-Content $StdOutLog -Tail 30 -ErrorAction SilentlyContinue) -join "`n")
        }
        throw ("{0} exited immediately (exit={1}). {2}" -f $Name, $process.ExitCode, $errTail)
    }
    Write-Info ("{0} started (PID {1})" -f $Name, $process.Id)
}

try {
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

    Ensure-PythonDeps $python
    Ensure-Frontend

    $webScript = Join-Path $ProjectRoot "AutoAnimeWeb.py"
    $workerScript = Join-Path $ProjectRoot "AutoAnimeWorker.py"
    if (-not (Test-Path $webScript)) { throw "Missing $webScript" }
    if (-not (Test-Path $workerScript)) { throw "Missing $workerScript" }

    $webArgs = @(
        "--data-dir", $DataDir,
        "--host", $HostAddress,
        "--port", "$Port"
    )
    if (-not $SecureCookies) {
        $webArgs += "--insecure-http"
    }

    $stamp = Get-Date -Format "yyyyMMdd"
    Start-AutoAnimeProcess `
        -Name "Web" `
        -Python $python `
        -ScriptPath $webScript `
        -ScriptArgs $webArgs `
        -StdOutLog (Join-Path $logDir "web-$stamp.log") `
        -StdErrLog (Join-Path $logDir "web-$stamp.err.log")

    Start-AutoAnimeProcess `
        -Name "Worker" `
        -Python $python `
        -ScriptPath $workerScript `
        -ScriptArgs @("--data-dir", $DataDir) `
        -StdOutLog (Join-Path $logDir "worker-$stamp.log") `
        -StdErrLog (Join-Path $logDir "worker-$stamp.err.log")

    if ($HostAddress -eq "0.0.0.0") {
        $url = "http://127.0.0.1:$Port"
    } else {
        $url = "http://${HostAddress}:$Port"
    }

    Write-Host ""
    Write-Info "Startup complete"
    Write-Info "Console: $url"
    Write-Info "Default admin: admin / AutoAnime-Admin-ChangeMe!"
    Write-Info "Local loopback defaults to passwordless login (toggle in Settings)"
    Write-Info "Stop with: .\stop-autoanime.bat"
    Write-Host ""
}
catch {
    Write-Err $_.Exception.Message
    exit 1
}
