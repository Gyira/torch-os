@echo off
title Torch OS backend
rem Torch OS launcher: double-click this file to start the local backend.
rem Keep this file pure ASCII (cmd.exe reads .cmd in the OEM code page).
setlocal
set "ROOT=%~dp0"
cd /d "%ROOT%"

if not exist "%ROOT%.venv\Scripts\python.exe" goto nopython

rem Open the browser a few seconds later, once the server is listening.
start "" /b cmd /c "timeout /t 3 >nul & start "" http://127.0.0.1:8765"

echo Starting Torch OS backend on http://127.0.0.1:8765
echo Keep this window open. Close it to stop the service.
echo.
"%ROOT%.venv\Scripts\python.exe" -m backend.app.run

echo.
echo Backend exited.
pause
exit /b 0

:nopython
echo .venv not found in this folder. Run setup first.
pause
exit /b 1
