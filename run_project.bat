@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title SmartStock AI -- Launcher

:: ================================================================
:: LAUNCHER
:: ================================================================
cls
echo.
echo  ====================================================
echo    SmartStock AI v2.0 -- Project Launcher
echo  ====================================================
echo.

echo  [1/4] Checking Python...
python --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo  [ERROR] Python not found.
    echo          Install from https://www.python.org/downloads/
    echo          Tick "Add Python to PATH" during install.
    pause & exit /b 1
)

echo  [2/4] Setting up virtual environment...
if not exist venv (
    echo        Creating venv...
    python -m venv venv
)
call venv\Scripts\activate.bat

echo  [3/4] Installing dependencies...
pip install -r requirements.txt --quiet

echo  [4/4] Checking AI model ^& Opening Portal...
if not exist ai_engine\final_ai_brain.pkl (
    echo        Training model now, please wait...
    python ai_engine\train_model.py >nul 2>&1
)

:: Automatically open the Landing Page
if exist frontend\index_landing.html (
    start "" "%CD%\frontend\index_landing.html"
) else if exist index_landing.html (
    start "" "%CD%\index_landing.html"
)

echo.
echo  ====================================================
echo    Server starting at : http://127.0.0.1:8000
echo    Keep this window open. Press Ctrl+C to stop.
echo  ====================================================
echo.

uvicorn main:app --reload --host 127.0.0.1 --port 8000
pause