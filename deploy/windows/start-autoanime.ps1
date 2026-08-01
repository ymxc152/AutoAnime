#Requires -Version 5.1
# Thin wrapper: canonical scripts live at the project root.
$ErrorActionPreference = "Stop"
$RootScript = Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path "start-autoanime.ps1"
if (-not (Test-Path $RootScript)) { throw "Missing $RootScript" }
& $RootScript @args
exit $LASTEXITCODE
