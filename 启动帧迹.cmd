@echo off
cd /d "%~dp0"
if exist "dist\PhoneTrace\PhoneTrace.exe" (
    start "" "dist\PhoneTrace\PhoneTrace.exe"
    exit /b
)
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "main.py"
    exit /b
)
echo Please build PhoneTrace first. See README.md.
pause
