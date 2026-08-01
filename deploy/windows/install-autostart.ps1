#Requires -Version 5.1
$ErrorActionPreference = "Stop"
$RootScript = Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path "install-autostart.ps1"
if (-not (Test-Path $RootScript)) { throw "Missing $RootScript" }
& $RootScript @args
exit $LASTEXITCODE
