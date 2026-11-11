@echo off
setlocal
cd /d "%~dp0"
where pyw >nul 2>nul
if %errorlevel% equ 0 (
  start "" pyw -3 "%~dp0app.py"
  exit /b
)
where pythonw >nul 2>nul
if %errorlevel% equ 0 (
  start "" pythonw "%~dp0app.py"
  exit /b
)
echo Python 3.9 or newer with Tkinter is required to run this source version.
echo Install Python from https://www.python.org/downloads/windows/ or build the portable EXE using Build-Portable.bat.
pause
