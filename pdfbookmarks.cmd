@echo off
REM ---------------------------------------------------------------
REM  pdfbookmarks - command line launcher
REM
REM  Finds a usable Python 3 automatically, then runs the CLI.
REM  Usage:  pdfbookmarks.cmd input.pdf outline.txt [options]
REM ---------------------------------------------------------------
setlocal
set "HERE=%~dp0"

for /f "usebackq delims=" %%L in (`powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%tools\find_python.ps1"`) do @%%L

if not defined PYEXE_OK (
    echo [pdfbookmarks] No usable Python 3 installation was found.
    echo [pdfbookmarks] Install Python 3, or point PDFBOOKMARKS_PYTHON at python.exe.
    exit /b 1
)

if defined PYHOME (
    set "PYTHONHOME=%PYHOME%"
    set "PYTHONPATH=%PYHOME%\Lib"
)
set "PYTHONIOENCODING=utf-8"
chcp 65001 >nul 2>nul

"%PYEXE%" -m pdfbookmarks %*
exit /b %ERRORLEVEL%
