@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0update-skill-entry.ps1" %*
exit /b %errorlevel%
