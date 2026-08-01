@echo off
setlocal
cd /d "%~dp0"
title AutoAnime Start
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-autoanime.ps1" %*
set ERR=%ERRORLEVEL%
if not "%ERR%"=="0" (
  echo.
  echo [AutoAnime] 启动失败，错误码 %ERR%
  pause
  exit /b %ERR%
)
echo.
echo 窗口可关闭；服务已在后台运行。
timeout /t 5 >nul
exit /b 0
