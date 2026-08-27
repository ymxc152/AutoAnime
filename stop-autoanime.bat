@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title AutoAnime Stop
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\autoanime.ps1" -Action stop %*
set ERR=%ERRORLEVEL%
if not "%ERR%"=="0" (
  echo.
  echo [AutoAnime] 停止失败，错误码 %ERR%
  pause
  exit /b %ERR%
)
exit /b 0
