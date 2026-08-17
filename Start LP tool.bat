@echo off
cd /d "%~dp0"
start "LP tool" /min cmd /c ".venv\Scripts\python.exe app.py > app.log 2>&1"
ping 127.0.0.1 -n 3 >nul
start "" http://localhost:5000
