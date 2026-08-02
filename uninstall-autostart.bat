@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title AutoAnime Uninstall Autostart
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall-autostart.ps1" %*
echo.
pause
