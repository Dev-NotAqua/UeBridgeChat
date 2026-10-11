@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title UE Chat Bridge
cd /d "%~dp0"

if not exist "%~dp0logs" mkdir "%~dp0logs" >nul 2>nul
set "LOGFILE=%~dp0logs\start.log"
>>"%LOGFILE%" 2>nul echo(===== %DATE% %TIME%  start.bat launched =====
for /f "tokens=*" %%v in ('ver') do >>"%LOGFILE%" 2>nul echo(%%v

echo.
echo   === UE Chat Bridge ===
echo.

REM Refuse to run from inside a ZIP preview: Explorer extracts start.bat alone
REM to %TEMP%, so bridge\bridge.py is missing and the launch fails confusingly.
if not exist "%~dp0bridge\bridge.py" (
    echo   ERROR: bridge\bridge.py not found next to start.bat.
    echo.
    echo   If you opened start.bat from inside the downloaded ZIP, first EXTRACT
    echo   the whole ZIP ^(right-click, "Extract All..."^), then run start.bat
    echo   from the extracted folder.
    echo.
    >>"%LOGFILE%" 2>nul echo(FATAL: bridge bridge.py missing - extracted from inside ZIP.^)
    pause
    exit /b 1
)

REM --- 1. Find Python ---------------------------------------------------------
echo   [1/2] Looking for Python...
set "PY="

REM Prefer the py launcher - it never resolves to the Microsoft Store stub.
where py >nul 2>nul && set "PY=py -3"
call :validate_py && goto :found

set "PY=python"
call :validate_py && goto :found

REM Last resort: scan the standard install folders directly (covers installs
REM WITHOUT "Add to PATH" and without the py launcher). Newest version first.
for %%R in (
    "%LOCALAPPDATA%\Programs\Python"
    "%ProgramFiles%"
    "%ProgramFiles(x86)%"
) do (
    if exist "%%~R" (
        for /f "delims=" %%D in ('dir /b /ad /o-n "%%~R\Python3*" 2^>nul') do (
            if exist "%%~R\%%D\python.exe" (
                set PY="%%~R\%%D\python.exe"
                call :validate_py && goto :found
            )
        )
    )
)

set "PY="
>>"%LOGFILE%" 2>nul echo(Python not found on PATH or in standard install folders.)
goto :need_install

:found
for /f "tokens=*" %%v in ('call %PY% --version 2^>^&1') do (
    echo         Found: %PY%  ^(%%v^)
    >>"%LOGFILE%" 2>nul echo(Python found OK.^)
)
goto :run_bridge

:need_install
where winget >nul 2>nul
if errorlevel 1 (
    echo   ERROR: Python is not installed and winget is not available,
    echo   so it cannot be installed automatically.
    echo.
    echo   Install Python manually: https://www.python.org/downloads/
    echo   IMPORTANT: tick "Add python.exe to PATH", then run start.bat again.
    echo.
    >>"%LOGFILE%" 2>nul echo(FATAL: no Python and no winget on this machine.^)
    pause
    exit /b 1
)
echo         Not found. Installing via winget...
echo.
winget install --id Python.Python.3.12 --source winget --accept-package-agreements --accept-source-agreements
if errorlevel 1 call :log "winget install returned an error - see console output above."
echo.
echo   Checking again...
set "PY=py -3"
call :validate_py && goto :ready
set "PY=python"
call :validate_py && goto :ready
for %%R in (
    "%LOCALAPPDATA%\Programs\Python"
    "%ProgramFiles%"
    "%ProgramFiles(x86)%"
) do (
    if exist "%%~R" (
        for /f "delims=" %%D in ('dir /b /ad /o-n "%%~R\Python3*" 2^>nul') do (
            if exist "%%~R\%%D\python.exe" (
                set PY="%%~R\%%D\python.exe"
                call :validate_py && goto :ready
            )
        )
    )
)
echo.
echo   ERROR: Python not found after install.
echo   Install manually: https://www.python.org/downloads/
echo   Tick "Add python.exe to PATH" then run this again.
echo.
>>"%LOGFILE%" 2>nul echo(FATAL: no usable Python found even after winget install.^)
pause
exit /b 1
:ready
echo         Python ready!
>>"%LOGFILE%" 2>nul echo(Python ready after winget install: %PY%)

:run_bridge
REM --- 2. Run the bridge ------------------------------------------------------
echo.
echo   [2/2] Starting bridge...
echo         No extra libraries needed - the bridge uses Python's standard
echo         library only.
echo.
>>"%LOGFILE%" 2>nul echo(Launching bridge\bridge.py with %PY%)
%PY% "%~dp0bridge\bridge.py"
set "BRIDGE_EXIT=%errorlevel%"
>>"%LOGFILE%" 2>nul echo(bridge.py exited with code %BRIDGE_EXIT%)

echo.
if not "%BRIDGE_EXIT%"=="0" (
    echo   Bridge stopped with ERROR code %BRIDGE_EXIT% - scroll up for the
    echo   error message and include THIS WHOLE WINDOW in any bug report.
    echo   Log file: logs\start.log
) else (
    echo   Bridge stopped normally.
)
echo   Press any key to close.
pause >nul
exit /b 0

:validate_py
%PY% --version >nul 2>nul || exit /b 1
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
exit /b %errorlevel%

REM --- Subroutine: append one line to start.log. Messages are passed quoted
REM (call :log "message") so parentheses in the text can't break the caller's
REM parenthesized blocks - the bug that silently killed this script before.
:log
>>"%LOGFILE%" 2>nul echo(%~1
exit /b 0
