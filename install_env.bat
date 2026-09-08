@echo off
REM ===================================================================
REM  DataScope - environment setup for Windows
REM
REM  Installs ONLY what is missing, one package at a time, so an
REM  already-working PySide6 is never touched or upgraded.
REM
REM  Usage:
REM     install_env.bat            use the current Python
REM     install_env.bat venv       create/use a venv at C:\dsenv
REM                                (recommended: short ASCII path, avoids
REM                                 the Windows 260-character path limit)
REM ===================================================================
setlocal enabledelayedexpansion
set PROJDIR=%~dp0
set VENVDIR=C:\dsenv
set PY=python
set PIPOPT=--no-warn-script-location --disable-pip-version-check

echo.
echo ================= DataScope environment setup =================
echo.

REM ---------- [1/6] Python -------------------------------------------
echo [1/6] Python
where python >nul 2>&1
if errorlevel 1 (
    echo   ERROR: "python" is not on PATH.
    echo   Install Python 3.11-3.13 from https://www.python.org/downloads/
    echo   and tick "Add python.exe to PATH" during setup.
    goto :fail
)
for /f "delims=" %%v in ('python -c "import sys;print(sys.version.split()[0])"') do set PYVER=%%v
for /f "delims=" %%p in ('python -c "import sys;print(sys.executable)"') do set PYEXE=%%p
echo   version : !PYVER!
echo   exe     : !PYEXE!

echo !PYEXE! | find /i "WindowsApps" >nul
if not errorlevel 1 (
    echo.
    echo   WARNING: this is the Microsoft Store build of Python.
    echo   Its site-packages folder is redirected and read-only, which makes
    echo   large packages such as PySide6 fail to install.
    echo   Strongly recommended: install Python from python.org, or run
    echo      install_env.bat venv
    echo   to build a virtual environment on a short path instead.
    echo.
)

REM ---------- [2/6] long path support --------------------------------
echo [2/6] Windows long path support
set LONGPATH=unknown
for /f "tokens=3" %%a in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\FileSystem" /v LongPathsEnabled 2^>nul ^| find /i "LongPathsEnabled"') do set LONGPATH=%%a
if /i "!LONGPATH!"=="0x1" (
    echo   enabled - good
) else (
    echo   NOT enabled ^(value: !LONGPATH!^)
    echo   PySide6 ships files with very long names; combined with a long
    echo   user folder name this breaks the installer.
    echo   Fix once, in an ADMIN PowerShell, then sign out and back in:
    echo.
    echo     Set-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' ^
    echo       -Name LongPathsEnabled -Value 1 -Type DWord
    echo.
    echo   Or use the short-path virtual environment: install_env.bat venv
    echo.
)

REM ---------- [3/6] optional virtual environment ---------------------
echo [3/6] Virtual environment
if /i "%~1"=="venv" (
    if not exist "%VENVDIR%\Scripts\python.exe" (
        echo   creating %VENVDIR% ...
        python -m venv "%VENVDIR%" || goto :fail
    ) else (
        echo   reusing %VENVDIR%
    )
    set PY="%VENVDIR%\Scripts\python.exe"
    !PY! -m pip install --upgrade pip %PIPOPT% >nul 2>&1
    echo   using !PY!
) else (
    echo   skipped ^(pass "venv" as an argument to use %VENVDIR%^)
)

REM ---------- [4/6] install only what is missing ---------------------
echo.
echo [4/6] Checking packages ^(installing only what is missing^)
call :need pandas       "pandas>=2.0"
call :need numpy        "numpy>=1.24"
call :need openpyxl     "openpyxl>=3.1"
call :need matplotlib   "matplotlib>=3.7"
call :need PySide6      "PySide6"
call :need charset_normalizer "charset-normalizer"

REM ---------- [5/6] verify ------------------------------------------
echo.
echo [5/6] Verifying
%PY% -c "import pandas,numpy,openpyxl,matplotlib;print('   core OK  pandas',pandas.__version__,'| numpy',numpy.__version__,'| openpyxl',openpyxl.__version__)" || goto :corefail
%PY% -c "import PySide6;print('   GUI  OK  PySide6',PySide6.__version__)" 2>nul
if errorlevel 1 (
    echo   GUI  -- PySide6 not usable. The graphical app will not start,
    echo           but the command line mode works with the core packages:
    echo.
    echo             %PY% "%PROJDIR%main.py" --cli "D:\data" --dict "D:\data\CSV項目名称.xlsx" --out "D:\out"
    echo.
    echo   To repair PySide6: enable long paths ^(step 2^), then
    echo             %PY% -m pip install --force-reinstall PySide6
    goto :end
)

REM ---------- [6/6] done -------------------------------------------
echo.
echo [6/6] Ready.
echo   Start the app:   %PY% "%PROJDIR%main.py"
goto :end

REM ------------------------------------------------------------------
:need
%PY% -c "import %~1" >nul 2>&1
if errorlevel 1 (
    echo   installing %~2 ...
    %PY% -m pip install %PIPOPT% %~2
    if errorlevel 1 (
        echo   *** %~1 failed to install - see the message above ***
        set FAILED=!FAILED! %~1
    )
) else (
    echo   %~1 already present - left untouched
)
exit /b 0

:corefail
echo.
echo   *** The core packages are still missing: DataScope cannot run. ***
echo   Most likely cause: Microsoft Store Python or disabled long paths.
echo   Try:  install_env.bat venv
goto :fail

:fail
echo.
echo   *** SETUP FAILED ***
:end
echo.
echo Press any key to close...
pause >nul
endlocal
