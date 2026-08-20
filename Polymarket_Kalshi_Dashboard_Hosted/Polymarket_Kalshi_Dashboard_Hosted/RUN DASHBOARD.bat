@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================
echo   Polymarket + Kalshi Dashboard
echo ============================================
echo.

where python >nul 2>nul
if %errorlevel% neq 0 (
    where py >nul 2>nul
    if %errorlevel% neq 0 (
        echo ERROR: Python was not found on this computer.
        echo.
        echo Install Python 3.10+ from https://python.org/downloads/ and check
        echo "Add python.exe to PATH" during setup, then run this again.
        echo.
        pause
        exit /b 1
    ) else (
        set PYCMD=py
    )
) else (
    set PYCMD=python
)

echo Checking dependencies...
%PYCMD% -m pip show flask >nul 2>nul
set NEED_INSTALL=0
if %errorlevel% neq 0 set NEED_INSTALL=1
%PYCMD% -m pip show matplotlib >nul 2>nul
if %errorlevel% neq 0 set NEED_INSTALL=1
%PYCMD% -m pip show requests >nul 2>nul
if %errorlevel% neq 0 set NEED_INSTALL=1

if %NEED_INSTALL% equ 1 (
    echo Installing required packages, this only happens once...
    %PYCMD% -m pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo.
        echo ERROR: Failed to install dependencies. Check your internet connection.
        echo.
        pause
        exit /b 1
    )
)

echo.
echo Starting the dashboard - your browser should open automatically.
echo Leave this window open while you use the dashboard; close it to stop.
echo.
%PYCMD% app.py
pause
