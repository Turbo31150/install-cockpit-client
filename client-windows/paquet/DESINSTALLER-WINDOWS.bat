@echo off
rem AlkymIA Cockpit - desinstallation Windows (sans droits administrateur)
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0DESINSTALLER-WINDOWS.ps1" %*
set "RC=%ERRORLEVEL%"
if "%~1"=="" (echo %cmdcmdline% | find /i "%~nx0" >nul && pause)
exit /b %RC%
