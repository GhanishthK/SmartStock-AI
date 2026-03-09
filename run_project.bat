@echo off
echo.
echo [1/4] Creating Virtual Environment...
python -m venv venv
call venv\Scripts\activate

echo.
echo [2/4] Installing Libraries...
pip install -r requirements.txt

echo.
echo [3/4] Training the AI Brain...
python ai_engine/train_model.py

echo.
echo [4/4] Starting the SmartStock AI Backend...
echo --------------------------------------------------
echo Dashboard will be available at: http://127.0.0.1:8000/docs
echo --------------------------------------------------
uvicorn main:app --reload
pause