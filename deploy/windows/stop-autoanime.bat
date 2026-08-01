@echo off
setlocal
cd /d "%~dp0"
title AutoAnime Stop
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-autoanime.ps1" %*
echo.
pause
