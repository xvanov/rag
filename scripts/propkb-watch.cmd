@echo off
REM propkb email watcher -- one polling pass. Scheduled via Task Scheduler.
REM cd into the repo so `python -m propkb` resolves and .env loads.
cd /d C:\repos\docrag
".venv\Scripts\python.exe" -m propkb.watch --once --days 3 >> "%TEMP%\propkb-watch.log" 2>&1
