@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "TASK_PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if exist "%TASK_PY%" goto found
where py >nul 2>&1
if not errorlevel 1 (
  py -3 bootstrap.py
  if errorlevel 1 pause
  exit /b
)
set "TASK_PY=python"
:found
"%TASK_PY%" bootstrap.py
if errorlevel 1 pause
