@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title AutoAnime Start
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\autoanime.ps1" -Action start %*
set ERR=%ERRORLEVEL%
if not "%ERR%"=="0" (
  echo.
  echo [AutoAnime] 启动失败，错误码 %ERR%
  pause
  exit /b %ERR%
)
echo.
echo 窗口可关闭；Web 与 Worker 已在后台运行。
echo 停止：双击 stop-autoanime.bat
%SystemRoot%\System32\timeout.exe /t 5 >nul
exit /b 0
