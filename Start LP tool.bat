@echo off
cd /d "%~dp0"
rem Kills only the exact process from the last run, tracked by PID in
rem .app.pid (app.py writes its own pid there on startup) - not a
rem CommandLine match on "*app.py*", which would kill any unrelated Python
rem script on the machine that happens to share that common filename.
if exist .app.pid (
  for /f "usebackq delims=" %%p in (".app.pid") do (
    taskkill /PID %%p /F >nul 2>&1
  )
  del /f /q .app.pid >nul 2>&1
)
ping 127.0.0.1 -n 2 >nul
start "LP tool" /min cmd /c ".venv\Scripts\python.exe app.py > app.log 2>&1"
ping 127.0.0.1 -n 3 >nul
start "" http://localhost:5001
