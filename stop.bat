@echo off
setlocal

cd /d "%~dp0"

echo Останавливаю Макса...

rem 0. Удалить exit-флаг brain, если остался от прошлой «выход».
rem    Иначе следующий start.bat унаследует флаг, и supervisor
rem    при первом падении brain решит, что это «штатный выход».
if exist "data\brain_exit.flag" del /f /q "data\brain_exit.flag" 2>nul

rem 1. Убить supervisor и ВСЁ его дерево по PID из lock.
rem    /T — убить дерево (дети: brain, asr, tts, music). Без /T они остаются
rem    сиротами: supervisor мёртв, а воркеры живут и жрут RAM. Копятся.
if exist "data\jarvis.lock" (
    for /f "tokens=2 delims=:, " %%a in ('type "data\jarvis.lock" ^| findstr /i "\"pid\""') do (
        taskkill /F /T /PID %%a 2>nul
    )
)

rem 2. Добить всех python с заголовком "Maks Supervisor".
taskkill /F /IM python.exe /FI "WINDOWTITLE eq Maks Supervisor*" 2>nul

rem 3. Дать Windows 2 секунды отпустить файл lock.
timeout /t 2 /nobreak >nul

rem 4. Удалить lock, если остался.
if exist "data\jarvis.lock" del /f /q "data\jarvis.lock" 2>nul

echo Готово.
pause