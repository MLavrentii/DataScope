@echo off
REM ===================================================================
REM  DataScope - run the analysis WITHOUT the graphical interface.
REM
REM  Easiest use: drag a folder (or a few CSV files) onto this file.
REM  No arguments: analyses the folder this script sits in.
REM
REM  Needs only pandas / numpy / openpyxl / matplotlib - NOT PySide6,
REM  so it works even when the Qt install is broken.
REM
REM  Output goes to  <input folder>\DataScope_out
REM ===================================================================
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
set PROJ=%~dp0
set PY=python
if exist "C:\dsenv\Scripts\python.exe" set PY="C:\dsenv\Scripts\python.exe"

echo.
echo ===================== DataScope (command line) ====================
echo.

REM ---------- where is the data? ------------------------------------
if "%~1"=="" (
    set "TARGET=%PROJ:~0,-1%"
    echo   no folder given - using the script folder
) else (
    set "TARGET=%~1"
)
echo   input : !TARGET!

REM ---------- output folder ----------------------------------------
if exist "!TARGET!\*" (
    set "OUT=!TARGET!\DataScope_out"
) else (
    for %%f in ("!TARGET!") do set "OUT=%%~dpfDataScope_out"
)
echo   output: !OUT!

REM ---------- profile ----------------------------------------------
set "PROF=%PROJ%profiles\aomori_pcs.json"
if not exist "!PROF!" set "PROF="
if defined PROF (
    echo   profile: aomori_pcs.json
) else (
    echo   profile: none - everything auto-detected
)

REM ---------- packages ---------------------------------------------
%PY% -c "import pandas,numpy,openpyxl,matplotlib" >nul 2>&1
if errorlevel 1 (
    echo.
    echo   Missing core packages. Install just these ^(no PySide6 needed^):
    echo       %PY% -m pip install pandas openpyxl matplotlib
    echo   or run install_env.bat
    goto :fail
)

REM ---------- run ---------------------------------------------------
echo.
if defined PROF (
    %PY% "%PROJ%main.py" --cli "!TARGET!" --profile "!PROF!" --out "!OUT!" --png %2 %3 %4 %5
) else (
    %PY% "%PROJ%main.py" --cli "!TARGET!" --out "!OUT!" --png %2 %3 %4 %5
)
if errorlevel 1 goto :fail

echo.
echo   Done. Opening the output folder...
if exist "!OUT!" start "" "!OUT!"
goto :end

:fail
echo.
echo   *** RUN FAILED - see the message above. ***
:end
echo.
echo Press any key to close...
pause >nul
endlocal
