@echo off
cd /d "%~dp0"
if exist "dist\v0.1.1\PhoneTrace\PhoneTrace.exe" (
    start "" "dist\v0.1.1\PhoneTrace\PhoneTrace.exe" --data-dir "%~dp0dist\PhoneTrace\records"
    exit /b
)
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
