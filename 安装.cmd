@echo off
title Torch OS setup
rem Torch OS one-click setup: double-click this file in Explorer (no admin rights, no Node).
rem Same as:  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup.ps1
rem Keep this file pure ASCII: cmd.exe reads a .cmd in the OEM code page, so Chinese text in
rem here would turn to mojibake on any locale. Every Chinese message comes from setup.ps1
rem (UTF-8 with BOM, which Windows PowerShell 5.1 reads correctly).
rem %~dp0 is the folder of this file, so it works no matter where it was launched from.
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1" %*
set "code=%ERRORLEVEL%"
echo.
if not "%code%"=="0" echo [Setup failed: exit %code%.] The message above says what to do.
pause
exit /b %code%
