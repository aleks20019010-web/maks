@echo off
setlocal

cd /d "%~dp0"

echo Останавливаю Макса...

rem 1. Убить supervisor по PID из lock
if exist "data\jarvis.lock" (
    for /f "tokens=2 delims=:, " %%a in ('type "data\jarvis.lock" ^| findstr /i "\"pid\""') do (
        taskkill /F /PID %%a 2>nul
    )
)

rem 2. Добить всех python с заголовком "Maks Supervisor" (на случай, если lock битый)
taskkill /F /IM python.exe /FI "WINDOWTITLE eq Maks Supervisor*" 2>nul

rem 3. Удалить lock, если остался
if exist "data\jarvis.lock" del /f /q "data\jarvis.lock" 2>nul

echo Готово.
pause