@echo off
REM ===================================================================
REM  DataScope - start the graphical application.
REM  Uses C:\dsenv if install_env.bat created it, otherwise the Python
REM  on PATH.  If PySide6 is unusable it says so and points at
REM  run_analysis.bat, which needs no Qt.
REM ===================================================================
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
set PROJ=%~dp0
set PY=python
if exist "C:\dsenv\Scripts\python.exe" set PY="C:\dsenv\Scripts\python.exe"

%PY% -c "import PySide6, shiboken6" >nul 2>&1
if errorlevel 1 (
    echo.
    echo   PySide6 / shiboken6 is not usable in this Python, so the window
    echo   cannot open. Two options:
    echo.
    echo     1^) analyse without the GUI:  drag your data folder onto
    echo        run_analysis.bat
    echo     2^) repair Qt:                install_env.bat venv
    echo        ^(see README section 3 about Windows long paths^)
    echo.
    echo Press any key to close...
    pause >nul
    exit /b 1
)

start "" %PY% "%PROJ%main.py"
endlocal
