@echo off
REM ---------------------------------------------------------------
REM  pdfbookmarks - graphical launcher
REM
REM  Finds a usable Python 3 automatically and opens the window,
REM  preferring pythonw.exe so no console is left behind.
REM ---------------------------------------------------------------
setlocal
set "HERE=%~dp0"

for /f "usebackq delims=" %%L in (`powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%tools\find_python.ps1" gui`) do @%%L

if not defined PYEXE_OK (
    echo [pdfbookmarks] No usable Python 3 installation was found.
    echo [pdfbookmarks] Install Python 3, or point PDFBOOKMARKS_PYTHON at python.exe.
    pause
    exit /b 1
)

set "PYRUN=%PYEXE%"
set "PYW=%PYEXE:python.exe=pythonw.exe%"
if exist "%PYW%" set "PYRUN=%PYW%"

if defined PYHOME (
    set "PYTHONHOME=%PYHOME%"
    set "PYTHONPATH=%PYHOME%\Lib"
)
set "PYTHONIOENCODING=utf-8"

start "" "%PYRUN%" -m pdfbookmarks.gui
exit /b 0
