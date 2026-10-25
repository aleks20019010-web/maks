@echo off
cd /d D:\Maks
title Maks Supervisor
echo ========================================
echo  MAKS - start (supervisor)
echo  Stop: Ctrl+C or close window
echo ========================================
echo.
python supervisor.py
echo.
echo ========================================
echo  Supervisor finished.
echo ========================================
pause