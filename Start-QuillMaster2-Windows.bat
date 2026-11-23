@echo off
title Quill Master 2
cd /d "%~dp0"

set "PY=python"
where py >nul 2>nul && set "PY=py -3"

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo  First run: setting up Quill Master 2. This takes a minute or two...
  echo.
  %PY% -m venv .venv
  if errorlevel 1 goto nopython
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto fail
)

".venv\Scripts\python.exe" run.py %*
pause
exit /b 0

:nopython
echo.
echo  Python 3 was not found on this computer.
echo  Install it from https://www.python.org/downloads/
echo  On the first installer screen, tick "Add python.exe to PATH".
echo  Then double-click this file again.
pause
exit /b 1

:fail
echo.
echo  Setup could not download what it needs. Check your internet
echo  connection, delete the .venv folder in this folder, and try again.
pause
exit /b 1
