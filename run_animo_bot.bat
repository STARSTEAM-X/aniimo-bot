@echo off
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" "%~dp0animo_bot.py"
) else (
    py -3 "%~dp0animo_bot.py"
)
if errorlevel 1 pause
