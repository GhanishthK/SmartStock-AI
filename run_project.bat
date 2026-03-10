@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title SmartStock AI -- Launcher

echo.
echo ====================================================
echo    SmartStock AI v2.0 -- Project Launcher
echo    AI-Driven Inventory Management System
echo ====================================================
echo.
echo Running from: %CD%
echo.


:: ----------------------------------------------------------------
:: STEP 0 -- Check Python
:: ----------------------------------------------------------------
echo [0/5] Checking Python...

python --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] Python not found in PATH.
    echo  Install Python 3.9+ from https://www.python.org/downloads/
    echo  IMPORTANT: Check "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PY_VER=%%v
echo [OK] Python %PY_VER% found.
echo.


:: ----------------------------------------------------------------
:: STEP 1 -- Virtual environment
:: ----------------------------------------------------------------
echo [1/5] Setting up virtual environment...

if not exist venv (
    echo       Creating venv...
    python -m venv venv
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo [OK] Virtual environment created.
) else (
    echo [OK] Virtual environment found. Skipping.
)
echo.


:: ----------------------------------------------------------------
:: STEP 2 -- Activate and install packages
:: ----------------------------------------------------------------
echo [2/5] Installing dependencies...

if not exist venv\Scripts\activate.bat (
    echo [ERROR] venv\Scripts\activate.bat not found.
    echo         Delete the venv folder and run this file again.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat

pip install -r requirements.txt --quiet
if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] Package installation failed. Full output:
    echo.
    pip install -r requirements.txt
    pause
    exit /b 1
)
echo [OK] All packages installed.
echo.


:: ----------------------------------------------------------------
:: STEP 3 -- Train AI model
:: NOTE: No parentheses allowed inside if blocks in batch files.
::       That was causing the "unexpected at this time" crash.
:: ----------------------------------------------------------------
echo [3/5] Checking AI model...

if exist ai_engine\final_ai_brain.pkl goto model_exists

echo       Model not found. Training now, please wait...
python ai_engine\train_model.py
if %ERRORLEVEL% neq 0 (
    echo [WARN] Training failed. Fallback logic will be used.
) else (
    echo [OK] AI model trained and saved.
)
goto model_done

:model_exists
echo [OK] Model already trained. Skipping.

:model_done
echo.


:: ----------------------------------------------------------------
:: STEP 4 -- Open browser
:: ----------------------------------------------------------------
echo [4/5] Opening browser...

if exist frontend\login.html (
    start "" "%CD%\frontend\login.html"
    echo [OK] Browser opened.
) else (
    echo [WARN] frontend\login.html not found. Open it manually.
)
echo.


:: ----------------------------------------------------------------
:: STEP 5 -- Start server
:: ----------------------------------------------------------------
echo [5/5] Starting server...
echo.
echo ====================================================
echo   URL   : http://127.0.0.1:8000
echo   Docs  : http://127.0.0.1:8000/docs
echo   Login : admin / admin123
echo ====================================================
echo.
echo   Keep this window open. Press Ctrl+C to stop.
echo.

uvicorn main:app --reload --host 127.0.0.1 --port 8000

echo.
echo [!] Server stopped. Press any key to close.
pause