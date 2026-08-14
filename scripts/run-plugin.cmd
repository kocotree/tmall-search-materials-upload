@echo off
setlocal
set "RUNTIME_ROOT=%TMALL_RUNTIME_ROOT%"
if not defined RUNTIME_ROOT set "RUNTIME_ROOT=%LOCALAPPDATA%\tmall-search-materials\runtime"
set "RUNTIME_PYTHON=%RUNTIME_ROOT%\.venv\Scripts\python.exe"
if not exist "%RUNTIME_PYTHON%" (
  echo PREPARED_RUNTIME_MISSING: run scripts\bootstrap.cmd first. 1>&2
  exit /b 2
)
"%RUNTIME_PYTHON%" "%~dp0run-plugin.py" %*
exit /b %errorlevel%
