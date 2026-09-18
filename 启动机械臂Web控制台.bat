@echo off
REM ============================================================
REM  Robot Arm Web Console - one click launcher
REM  Starts `python -m webui --open` (service + browser page).
REM  Keep this console window open while using the web page.
REM ============================================================
setlocal
title Robot Arm Web Console
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"

if not defined PY (
  echo [ERROR] Python was not found in PATH.
  echo         Install Python 3.10+ or add it to PATH, then retry.
  echo.
  pause
  exit /b 1
)

echo Using interpreter: %PY%
%PY% -c "import serial" >nul 2>nul
if errorlevel 1 (
  echo [SETUP] pyserial is missing, installing ...
  %PY% -m pip install pyserial
)

echo.
echo Starting Robot Arm Web Console ...
echo.
%PY% -m webui --open %*
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
  echo.
  echo [ERROR] The service exited with code %RC%.
  echo.
  pause
)
exit /b %RC%
