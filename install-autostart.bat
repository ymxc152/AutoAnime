@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title AutoAnime Install Autostart
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-autostart.ps1" %*
set ERR=%ERRORLEVEL%
if not "%ERR%"=="0" (
  echo.
  echo [AutoAnime] 注册自启失败，错误码 %ERR%
  pause
  exit /b %ERR%
)
echo.
pause
exit /b 0
