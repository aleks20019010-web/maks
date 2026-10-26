@echo off
setlocal

cd /d "%~dp0"
title Maks Supervisor

echo ========================================
echo  MAKS - start (supervisor)
echo  Stop: Ctrl+C or close window
echo ========================================
echo.

python supervisor.py

pause
