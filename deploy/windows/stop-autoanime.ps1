#Requires -Version 5.1
$ErrorActionPreference = "Continue"
$RootScript = Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path "stop-autoanime.ps1"
if (-not (Test-Path $RootScript)) { throw "Missing $RootScript" }
& $RootScript @args
exit $LASTEXITCODE
