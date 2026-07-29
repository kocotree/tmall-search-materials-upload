@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-ui.ps1" %*
exit /b %errorlevel%
