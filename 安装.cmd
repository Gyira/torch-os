@echo off
title Torch OS setup
rem Torch OS installer entry: double-click this file.
rem It opens the graphical installer (scripts\install_gui.py), which is the only install path.
rem Keep this file pure ASCII: cmd.exe reads a .cmd in the OEM code page, so Chinese text in
rem here would turn to mojibake on any locale. All Chinese messages come from the Python side (UTF-8).
rem %~dp0 is the folder of this file, so it works no matter where it was launched from.
setlocal
set "ROOT=%~dp0"

rem Re-run case: the venv exists, use its windowed interpreter.
if exist "%ROOT%.venv\Scripts\pythonw.exe" goto venv_python

rem Preferred: the py launcher with 3.12 (installed to the Windows dir even without PATH).
where py.exe >nul 2>nul
if errorlevel 1 goto try_python
where pyw.exe >nul 2>nul
if errorlevel 1 goto try_python
py -3.12 -c "import tkinter" >nul 2>nul
if errorlevel 1 goto try_python
start "" pyw -3.12 "%ROOT%scripts\install_gui.py"
exit /b 0

:venv_python
start "" "%ROOT%.venv\Scripts\pythonw.exe" "%ROOT%scripts\install_gui.py"
exit /b 0

:try_python
set "PYEXE="
for /f "delims=" %%I in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE goto no_python
echo %PYEXE% | find /i "WindowsApps" >nul
if not errorlevel 1 goto no_python
for %%I in ("%PYEXE%") do set "PYW=%%~dpIpythonw.exe"
if not exist "%PYW%" goto no_python
start "" "%PYW%" "%ROOT%scripts\install_gui.py"
exit /b 0

:no_python
echo.
echo The graphical installer needs Python 3.12, and none was found.
echo Install it (no admin rights needed), then double-click this file again:
echo     winget install --id Python.Python.3.12 --source winget --scope user
echo.
echo Opening the Python download page...
start "" "https://www.python.org/downloads/"
pause
exit /b 1
