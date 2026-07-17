@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "POWERSHELL=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%POWERSHELL%" set "POWERSHELL=powershell.exe"

"%POWERSHELL%" -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%service.ps1" start
if errorlevel 1 exit /b %ERRORLEVEL%

start "" "http://127.0.0.1:8765/"
exit /b %ERRORLEVEL%
