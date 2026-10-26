# Макс — голосовой дворецкий

Голосовой ассистент на русском. Музыка, погода, статус системы, напоминания, ритуалы, очистка диска, LLM-ответы (диалог).

## Требования

- Windows 10/11
- Python 3.10+
- VLC (для музыки)
- Vosk-модель vosk-model-small-ru-0.22 в models/
- Токен Яндекс.Музыки в config/token.txt
- Токен AITUNNEL (LLM) в config/llm_key.txt

## Установка

1. Клонировать репозиторий:
   git clone https://github.com/aleks20019010-web/maks.git
   cd maks

2. Установить зависимости:
   pip install -r requirements.txt

3. Установить VLC (если нет):
   winget install VideoLAN.VLC

4. Скачать Vosk-модель vosk-model-small-ru-0.22 в models/:
   https://alphacephei.com/vosk/models

5. Создать config/token.txt — токен Яндекс.Музыки (одна строка).

6. Создать config/llm_key.txt — токен AITUNNEL (одна строка).

7. (Опционально) config/secrets.json — если используешь Jellyfin:
   {"jellyfin_key": "..."}

8. Запустить:
   .\start.bat

## Структура

- brain/ — ядро (router, skills, composer, appraisal, reminders, pending)
- personality_core/ — личность (engine, prosody, style, self_state, mind_stream, phrase, policy, character, identity)
- memory/ — память (engine, storage, aggregates)
- workers/ — asr, tts, music
- shared/ — config, protocol, ipc, log, metrics, yandex, state
- responses/ — JSON фразы (composer, openings, music, weather, system, smalltalk, llm, …)
- config/ — workers.json, identity.json, music_words.json
- docs/ — architecture.md, итоги_*.md, план_*.md
- tools/ — warmup_tts.py, clean_slots.py, remove_sir.py, clean_cache.py, prepare_github.py
- tests/ — тесты

## Голосовые команды

- «Макс, включи музыку» / «включи Арию» / «включи рок»
- «Макс, пауза» / «продолжи» / «стоп» / «дальше» / «назад»
- «Макс, громче» / «тише» / «громкость 50»
- «Макс, что играет» / «что за песня»
- «Макс, какая погода»
- «Макс, статус» / «как дела»
- «Макс, напомни ...» / «когда ...»
- «Макс, почисти диск C»
- «Макс, привет» / «спасибо» / «кто ты» / «как дела»

## 🎵 Музыка (Яндекс.Музыка)

Макс использует **неофициальную** Python-библиотеку [yandex-music](https://github.com/MarshalX/yandex-music-api) (автор MarshalX) для доступа к API Яндекс.Музыки.

**Важно:** это неофициальный API. Яндекс может изменить его в любой момент, и библиотека может перестать работать.

### Настройка

1. Установи библиотеку:
   pip install yandex-music

2. Получи OAuth-токен Яндекс.Музыки. Проще всего через [Device Flow](https://github.com/MarshalX/yandex-music-token).

3. Положи токен в файл config/token.txt (одна строка, без пробелов).

### Как это работает

- При старте rain/main.py вызывает shared/yandex.py::init().
- Клиент yandex_music.Client(token).init() авторизуется и получает доступ к аккаунту.
- Поиск артиста/трека → получение download_info → скачивание mp3.
- Скачанные файлы кэшируются локально (повторно не качаются).

### Ограничения

- Токен привязан к аккаунту. Если сменить аккаунт — нужен новый токен.
- Без подписки Яндекс.Плюс качество может быть ниже.
- Скачивание музыки через сторонний клиент может нарушать условия сервиса Яндекса (используй на свой риск).
- При сбое Яндекс.API (таймаут, 5xx) — трек может не скачаться.

## 🧠 LLM (диалог)

Макс использует **AITUNNEL** (агрегатор OpenAI-совместимых моделей) для ответов на вопросы, которые не понял router.

- Модель: deepseek-v4-flash-0731
- Токен: config/llm_key.txt
- Таймаут: 15 секунд
- Провайдер: сортировка по latency (provider: {"sort": "latency"})
- Потоки: llm_worker + 	ts_worker (brain не блокируется)
- Отмена: «стоп» / «хватит» в диалоге → cancel_all()

## Лицензия

MIT
