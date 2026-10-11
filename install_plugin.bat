@echo off
setlocal
title UE Chat Bridge - install plugin
cd /d "%~dp0"

set "PROJECT=%~1"
if "%PROJECT%"=="" (
    echo Drag your .uproject file onto this window and press Enter:
    set /p PROJECT=
)
if "%PROJECT%"=="" (
    echo No project given - nothing to do.
    pause
    exit /b 1
)

REM Find a Python, same order as start.bat (py launcher first).
set "PY=py -3"
%PY% --version >nul 2>nul || set "PY=python"
%PY% --version >nul 2>nul
if errorlevel 1 (
    echo Python 3.9 or newer is required: https://www.python.org/downloads/
    echo Tick "Add python.exe to PATH" in the installer, then run this file again.
    pause
    exit /b 1
)

%PY% bridge\bridge.py --install "%PROJECT%"
set "EC=%errorlevel%"
echo.
if not "%EC%"=="0" (
    echo Install FAILED with code %EC% - read the message above.
    echo Common cause: the path has no .uproject file in it.
) else (
    echo Install OK - restart the Unreal editor to pick it up.
)
pause
exit /b %EC%
