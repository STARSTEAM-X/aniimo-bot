@echo off
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\python.exe" (
    echo Create .venv and install requirements-dev.txt first.
    exit /b 1
)
if not exist "%~dp0.tmp" mkdir "%~dp0.tmp"
set "TEMP=%~dp0.tmp"
set "TMP=%~dp0.tmp"
"%~dp0.venv\Scripts\python.exe" -m PyInstaller --noconfirm "%~dp0AnimoBot_Background.spec"
if errorlevel 1 exit /b 1
copy /y "%~dp0dist\AnimoBot_Background_Fix.exe" "%~dp0AnimoBot_Background_Fix.exe" >nul
if errorlevel 1 exit /b 1
