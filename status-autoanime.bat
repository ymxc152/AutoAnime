@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title AutoAnime Status
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\autoanime.ps1" -Action status %*
exit /b %ERRORLEVEL%
