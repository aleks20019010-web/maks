# Макс — голосовой дворецкий

Голосовой ассистент на русском. Музыка (Яндекс.Музыка), погода (OpenWeatherMap), статус системы, напоминания, ритуалы, очистка диска, LLM-ответы (AITUNNEL).

## Требования

- Windows 10/11
- Python 3.10+
- VLC (для музыки и TTS)
- Vosk-модель `vosk-model-small-ru-0.22` в `models/`
- Токен Яндекс.Музыки в `config/token.txt` (для музыки)
- Токен Яндекс.Облака в `config/yandex_key.txt` + `config/yandex_folder.txt` (для TTS)
- Токен AITUNNEL в `config/llm_key.txt` (для LLM, опционально)
- Ключ OpenWeatherMap в `config/weather_key.txt` (для погоды)

## Установка

1. Клонировать репозиторий:
git clone https://github.com/aleks20019010-web/maks.git
cd maks

text

2. Установить зависимости:
pip install -r requirements.txt

text

3. Установить VLC (если нет):
winget install VideoLAN.VLC

text

4. Скачать Vosk-модель `vosk-model-small-ru-0.22` в `models/`:
https://alphacephei.com/vosk/models

5. Создать `config/paths.json` — внешние пути (музыка, VLC):

```json
{
  "music_dir": "D:\\Music",
  "vlc_path": "C:\\Program Files\\VideoLAN\\VLC\\vlc.exe"
}
Создать токены:

config/token.txt — токен Яндекс.Музыки (одна строка)

config/yandex_key.txt — API-ключ Яндекс.Облака (TTS)

config/yandex_folder.txt — folder_id Яндекс.Облака (TTS)

config/llm_key.txt — токен AITUNNEL (LLM, опционально)

config/weather_key.txt — ключ OpenWeatherMap (погода)

(Опционально) config/secrets.json — если используешь Jellyfin:

json
{"jellyfin_key": "..."}
Запустить:

text
.\start.bat
Остановить: .\stop.bat или Ctrl+C в окне supervisor.

Структура
brain/ — ядро (router, skills, composer, appraisal, reminders, pending)

personality_core/ — личность (engine, prosody, style, self_state, mind_stream, phrase, policy, character, identity)

memory/ — память (engine, storage, aggregates, reader, writer)

workers/ — asr (Vosk), tts (Yandex), music (VLC + Яндекс.Музыка)

shared/ — config, protocol, ipc, log, metrics, yandex, state, sources

responses/ — JSON фразы (composer, openings, music, weather, system, smalltalk, llm, …)

config/ — настройки (workers.json, identity.json, music_words.json, aliases.json, genre_aliases.json, genre_tags.json, radio_stations.json, radio_aliases.json, weather.json, personality.json)

docs/ — architecture.md, llm_design.md, итоги_.md, план_.md

tools/ — cleanup_c.ps1, prepare_github.py, test_*.py (скрипты-проверки LLM)

tests/ — pytest-тесты (appraisal, phrase, habits, artist_weight)

warmup_tts.py — в корне (прогрев TTS-кэша).

Голосовые команды
«Макс, включи музыку» / «включи Арию» / «включи рок» / «включи лампу»

«Макс, пауза» / «продолжи» / «стоп» / «дальше» / «назад»

«Макс, громче» / «тише» / «громкость 50»

«Макс, что играет» / «что за песня»

«Макс, какая погода»

«Макс, статус» / «как дела»

«Макс, напомни ...» / «когда ...»

«Макс, почисти диск C»

«Макс, привет» / «спасибо» / «кто ты» / «как дела»

🎵 Музыка (Яндекс.Музыка)
Макс использует неофициальную Python-библиотеку yandex-music (автор MarshalX) для доступа к API Яндекс.Музыки.

Важно: это неофициальный API. Яндекс может изменить его в любой момент, и библиотека может перестать работать.

Настройка
Установи библиотеку: pip install yandex-music (уже в requirements)

Получи OAuth-токен Яндекс.Музыки. Проще всего через Device Flow.

Положи токен в config/token.txt (одна строка, без пробелов).

Как это работает
При старте workers/music/main.py вызывает shared/yandex.py::init().

Клиент yandex_music.Client(token).init() авторизуется и получает доступ к аккаунту.

Поиск артиста/трека → получение download_info → скачивание mp3 через yandex-music-downloader.

Скачанные файлы кэшируются локально (повторно не качаются).

Ограничения
Токен привязан к аккаунту. Если сменить аккаунт — нужен новый токен.

Без подписки Яндекс.Плюс качество может быть ниже.

Скачивание музыки через сторонний клиент может нарушать условия сервиса Яндекса (используй на свой риск).

При сбое Яндекс.API (таймаут, 5xx) — трек может не скачаться.

🧠 LLM (диалог)
Макс использует AITUNNEL (агрегатор OpenAI-совместимых моделей) для ответов на вопросы, которые не понял router.

Модель: deepseek-v4-flash-0731

Токен: config/llm_key.txt

Таймаут: 15 секунд

Провайдер: сортировка по latency (provider: {"sort": "latency"})

Потоки: llm_worker + tts_worker (brain не блокируется)

Отмена: «стоп» / «хватит» в диалоге → cancel_all() + закрытие сессии (история очищается)

Если токена нет — LLM-скил не работает, router говорит «Не понял».

🧹 Очистка диска
«Макс, почисти диск C» → dry-run → «Могу освободить: безопасно — X ГБ; корзина — Y ГБ. Очистить безопасное?» → «да» → чистка.

По умолчанию чистит только безопасное:

%TEMP%, %LOCALAPPDATA%\Temp, C:\Windows\Temp

Windows Update Download, CBS/DISM логи

Jellyfin transcodes

Не чистит (только с флагами .ps1):

Корзина (-IncludeRecycleBin)

Windows.old — откат Windows (-IncludeWindowsOld, опасно)

Prefetch — не трогаем вообще (ускоряет запуск)

Тесты
text
pytest tests/ -v
20 тестов: appraisal (4), artist_weight (5), habits (4), phrase (7).

Лицензия
MIT
