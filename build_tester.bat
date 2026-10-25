@echo off
REM ==========================================================
REM  FP-Tester - build the standalone executable
REM
REM  ASCII-only, CRLF, no "chcp", no "^" line continuations.
REM  cmd.exe reads .bat files with the OEM codepage, so UTF-8
REM  text here would be corrupted and continuations would be
REM  split mid-word. All real work happens in build_tester.py.
REM ==========================================================

setlocal
cd /d "%~dp0"

set PY=
where py >nul 2>nul && set PY=py
if "%PY%"=="" (
    where python >nul 2>nul && set PY=python
)

if "%PY%"=="" (
    echo.
    echo  ERROR: Python was not found.
    echo.
    echo  Install Python 3.10 or newer from https://www.python.org/downloads/
    echo  and make sure "Add python.exe to PATH" is checked during setup.
    echo.
    pause
    exit /b 1
)

"%PY%" build_tester.py
set RC=%ERRORLEVEL%

echo.
pause
exit /b %RC%
