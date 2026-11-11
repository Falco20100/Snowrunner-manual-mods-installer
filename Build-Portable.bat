@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Install Python 3.9 or newer with Tkinter from python.org first.
  pause
  exit /b 1
)
py -3 -m pip install pyinstaller==6.11.1
if errorlevel 1 goto failed
py -3 -m PyInstaller --noconfirm --clean --onefile --windowed --name SnowRunnerManualInstaller app.py
if errorlevel 1 goto failed
echo.
echo Portable EXE created: dist\SnowRunnerManualInstaller.exe
echo Copy this file anywhere on Windows. Do not place it in the game PAK folder.
pause
exit /b 0
:failed
echo Build failed. Check the errors above.
pause
exit /b 1
