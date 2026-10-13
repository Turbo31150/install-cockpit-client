@echo off
rem AlkymIA Cockpit - installation Windows (sans droits administrateur)
rem Double-clic : la cle d'acces est demandee a l'ecran.
rem Invite de commandes : INSTALLER-WINDOWS.bat --cle VOTRE_CLE [--passerelle URL] [--port N] [--sans-ouvrir]
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0INSTALLER-WINDOWS.ps1" %*
set "RC=%ERRORLEVEL%"
if "%~1"=="" (echo %cmdcmdline% | find /i "%~nx0" >nul && pause)
exit /b %RC%
