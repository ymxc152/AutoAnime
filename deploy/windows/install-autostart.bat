@echo off
setlocal
cd /d "%~dp0"
title AutoAnime Install Autostart
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-autostart.ps1" %*
echo.
pause
