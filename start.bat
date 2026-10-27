@echo off
REM ============================================================
REM  CloudCare local dev launcher (Windows)
REM  Opens two terminal windows: API (:8000) + frontend (:5500)
REM  then opens the browser. Close the windows to stop.
REM ============================================================

cd /d "%~dp0cloudcare-api"

if not exist ".venv\Scripts\python.exe" (
    echo [!] .venv not found or broken. Recreate it with:
    echo     py -3.12 -m venv .venv
    echo     .venv\Scripts\python -m pip install -r requirements-dev.txt
    pause
    exit /b 1
)

echo Starting API  -^>  http://127.0.0.1:8000  (migrations run first)...
start "CloudCare API" cmd /k ".venv\Scripts\activate && alembic upgrade head && uvicorn app.main:app --reload --proxy-headers --forwarded-allow-ips=*"

echo Starting web  -^>  http://127.0.0.1:5500 ...
start "CloudCare Web" cmd /k ".venv\Scripts\python.exe -m http.server 5500 --directory ..\cloudcare"

timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:5500/index.html"

echo.
echo Done. Sign in with a demo account, e.g.
echo   Admin  : ritu.malhotra@cloudcare.demo
echo   Doctor : aakash.verma@cloudcare.demo
echo   Password for all demo accounts: Demo!Pass123
echo.
echo Keep both terminal windows open while developing.
