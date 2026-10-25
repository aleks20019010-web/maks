@echo off
echo Останавливаю все процессы Джарвиса...
taskkill /F /IM python.exe /FI "WINDOWTITLE eq Jarvis Supervisor*" 2>nul
echo Готово.
pause