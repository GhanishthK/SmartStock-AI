@echo off
echo ====================================================
echo Starting SmartStock AI Setup...
echo ====================================================

:: 1. Create Virtual Environment if it doesn't exist
if not exist venv (
    echo Creating Virtual Environment...
    python -m venv venv
)

:: 2. Activate and Install Dependencies
echo Installing/Updating Packages...
call venv\Scripts\activate
pip install -r requirements.txt --quiet

:: 3. Train the AI Model (Ensures it works on the new system)
echo Training AI Engine...
python ai_engine/train_model.py

:: 4. Launch the Frontend Dashboard in the Browser
echo Opening Dashboard...
start "" "frontend/login.html"

:: 5. Start the Backend API Server
echo Starting FastAPI Server...
echo ----------------------------------------------------
echo SYSTEM IS LIVE. DO NOT CLOSE THIS WINDOW.
echo ----------------------------------------------------
uvicorn main:app --reload