@echo off
cd /d "%~dp0"
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*app.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
ping 127.0.0.1 -n 2 >nul
start "LP tool" /min cmd /c ".venv\Scripts\python.exe app.py > app.log 2>&1"
ping 127.0.0.1 -n 3 >nul
start "" http://localhost:5001
