@echo off
chcp 65001 >nul
REM Windows：安裝依賴並啟動。示範模式：start.bat demo
cd /d "%~dp0backend"
if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
if not exist .env copy .env.example .env
if "%1"=="demo" (
  set DEMO_MODE=true
  if not exist data\twstock_demo.db python -m app.cli demo
)
echo 開啟 http://127.0.0.1:8000
python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000
