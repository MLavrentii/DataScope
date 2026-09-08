@echo off
REM ===================================================================
REM  DataScope - Windows EXE build script (PyInstaller)
REM  Produces:  dist\DataScope_release_v2.7.0\DataScope_release_v2.7.0.exe
REM  One-folder build: starts faster and antivirus flags it less often
REM  than one-file.  Set ONEFILE=1 below for a single .exe instead.
REM ===================================================================
setlocal enabledelayedexpansion
set APPNAME=DataScope
set VERSION=2.7.0
set OUTNAME=%APPNAME%_release_v%VERSION%
set ONEFILE=0

echo.
echo [1/5] Checking Python...
where python >nul 2>&1
if errorlevel 1 (
    echo   ERROR: python was not found in PATH.
    goto :fail
)
python -c "import sys; print('   using', sys.version)" || goto :fail

echo.
echo [2/5] Checking required packages...
REM Never bulk-install from requirements.txt here: that upgrades an existing
REM PySide6 and, on a machine without long-path support, breaks the working
REM one half way through.  Report exactly what is absent instead.
set MISSING=
for %%m in (pandas numpy openpyxl matplotlib shiboken6 PySide6) do (
    python -c "import %%m" >nul 2>&1
    if errorlevel 1 (
        set MISSING=!MISSING! %%m
        echo   missing: %%m
    ) else (
        echo   ok     : %%m
    )
)
if not "!MISSING!"=="" (
    echo.
    echo   Cannot build: !MISSING!
    echo   install_env.bat installs only what is absent and explains the
    echo   Windows long-path / Microsoft-Store-Python pitfalls.
    echo.
    choice /c YN /m "   Run install_env.bat now"
    if errorlevel 2 goto :fail
    call "%~dp0install_env.bat"
    echo.
    echo   Setup finished - start build_exe.bat again to build the EXE.
    goto :end
)
python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo   Installing PyInstaller ...
    python -m pip install --no-warn-script-location pyinstaller || goto :fail
)

echo.
echo [3/5] Cleaning previous build...
if exist build rmdir /s /q build
if exist "dist\%OUTNAME%" rmdir /s /q "dist\%OUTNAME%"
if exist "%OUTNAME%.spec" del /q "%OUTNAME%.spec"

echo.
echo [4/5] Building...
set COMMON=--noconfirm --clean --windowed --name "%OUTNAME%" ^
 --add-data "profiles;profiles" ^
 --collect-submodules openpyxl ^
 --hidden-import openpyxl.cell._writer ^
 --exclude-module PySide6.QtWebEngineCore ^
 --exclude-module PySide6.Qt3DCore ^
 --exclude-module PySide6.QtMultimedia ^
 --exclude-module tkinter ^
 --exclude-module PyQt5

if exist app.ico set COMMON=%COMMON% --icon app.ico

if "%ONEFILE%"=="1" (
    python -m PyInstaller %COMMON% --onefile main.py || goto :fail
) else (
    python -m PyInstaller %COMMON% main.py || goto :fail
)

echo.
echo [5/5] Done.
echo   Output: dist\%OUTNAME%\
echo.
echo   Notes:
echo     * Test the EXE on a clean Windows PC without Python installed.
echo     * Settings are stored per user in %%APPDATA%%\DataScope - never
echo       beside the EXE, so it also works from Program Files.
echo     * Some antivirus products flag fresh PyInstaller binaries.
echo       Signing the EXE, or using the one-folder build, reduces this.
goto :end

:fail
echo.
echo   *** BUILD FAILED - see the message above. ***
echo.
:end
echo Press any key to close...
pause >nul
endlocal
