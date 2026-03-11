@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title SmartStock AI -- Launcher

:: ================================================================
:: MENU
:: ================================================================
cls
echo.
echo  ====================================================
echo    SmartStock AI v2.0 -- Project Launcher
echo  ====================================================
echo.
echo    Select which portal to open:
echo.
echo    [1]  SmartStock AI  --  Inventory Management (Admin)
echo    [2]  Sales Portal   --  Sales Staff Dashboard
echo    [3]  Both Portals   --  Open both at once
echo.
echo  ----------------------------------------------------

:ask
set CHOICE=
set /p CHOICE="  Enter 1, 2, or 3 and press Enter: "

if "%CHOICE%"=="1" goto stock
if "%CHOICE%"=="2" goto sales
if "%CHOICE%"=="3" goto both
echo  [!] Invalid. Enter 1, 2, or 3.
goto ask

:stock
set OPEN_STOCK=1
set OPEN_SALES=0
set PORTAL_LABEL=SmartStock AI
goto setup

:sales
set OPEN_STOCK=0
set OPEN_SALES=1
set PORTAL_LABEL=Sales Portal
goto setup

:both
set OPEN_STOCK=1
set OPEN_SALES=1
set PORTAL_LABEL=Both Portals
goto setup


:: ================================================================
:: SETUP
:: ================================================================
:setup
cls
echo.
echo  ====================================================
echo    Launching: %PORTAL_LABEL%
echo  ====================================================
echo.

echo  [0/5] Checking Python...
python --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo  [ERROR] Python not found.
    echo          Install from https://www.python.org/downloads/
    echo          Tick "Add Python to PATH" during install.
    pause & exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PY_VER=%%v
echo  [OK] Python %PY_VER%
echo.

echo  [1/5] Setting up virtual environment...
if not exist venv (
    echo        Creating venv...
    python -m venv venv
    if %ERRORLEVEL% neq 0 (
        echo  [ERROR] Could not create virtual environment.
        pause & exit /b 1
    )
    echo  [OK] Virtual environment created.
) else (
    echo  [OK] Virtual environment found. Skipping.
)
echo.
call venv\Scripts\activate.bat

echo  [2/5] Installing dependencies...
pip install -r requirements.txt --quiet
if %ERRORLEVEL% neq 0 (
    echo  [ERROR] Dependency install failed. Full output:
    pip install -r requirements.txt
    pause & exit /b 1
)
echo  [OK] All packages ready.
echo.

echo  [3/5] Checking AI model...
if exist ai_engine\final_ai_brain.pkl goto model_ok
echo        Training model now, please wait...
python ai_engine\train_model.py
if %ERRORLEVEL% neq 0 (
    echo  [WARN] Training failed. Fallback logic will be used.
) else (
    echo  [OK] AI model trained.
)
goto model_done
:model_ok
echo  [OK] AI model found. Skipping training.
:model_done
echo.

echo  [4/5] Opening portal(s)...
if "%OPEN_STOCK%"=="1" (
    if exist frontend\login.html (
        start "" "%CD%\frontend\index_landing.html"
        echo  [OK] Opened SmartStock AI
        timeout /t 1 /nobreak >nul
    ) else (
        echo  [WARN] frontend\login.html not found.
    )
)
if "%OPEN_SALES%"=="1" (
    if exist frontend\sales_portal.html (
        start "" "%CD%\frontend\sales_portal.html"
        echo  [OK] Opened Sales Portal
    ) else (
        echo  [WARN] frontend\sales_portal.html not found.
    )
)
echo.

echo  [5/5] Starting server...
echo.
echo  ====================================================
echo    URL      : http://127.0.0.1:8000
echo    API Docs : http://127.0.0.1:8000/docs
echo    Opening  : %PORTAL_LABEL%
echo  ====================================================
echo.
echo    SmartStock AI login : admin / admin123
echo    Sales Portal login  : Register on the portal
echo.
echo    Keep this window open. Press Ctrl+C to stop.
echo.

uvicorn main:app --reload --host 127.0.0.1 --port 8000

echo.
echo  [!] Server stopped. Press any key to close.
pause