@echo off
cd /d "%~dp0"
where py >nul 2>&1
if %errorlevel%==0 (
    py -3 start.py
    goto end
)
where python >nul 2>&1
if %errorlevel%==0 (
    python start.py
    goto end
)
echo.
echo [ERROR] Python is not installed on this computer.
echo   1. Download it from https://www.python.org/downloads/
echo   2. On the first install screen, check "Add python.exe to PATH"
echo   3. Double-click this file again
:end
echo.
pause
