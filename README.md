# Макс — голосовой дворецкий

Голосовой ассистент на русском. Музыка, погода, статус системы, напоминания, ритуалы, очистка диска.

## Требования

- Windows 10/11
- Python 3.10+
- VLC (для музыки)
- Vosk-модель vosk-model-small-ru-0.22 в models/
- Токен Яндекса в config/yandex_key.txt

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

5. Создать config/secrets.json с токенами:
   {
     "yandex_token": "...",
     "jellyfin_key": "..."
   }

6. Запустить:
   start.bat

## Структура

- brain/ — ядро (router, skills, composer, appraisal, reminders)
- personality_core/ — личность (engine, prosody, style, self_state, mind_stream)
- memory/ — память (engine, storage, aggregates)
- workers/ — asr, tts, music
- shared/ — config, protocol, ipc, log, metrics, yandex, state
- responses/ — JSON фразы
- config/ — workers.json, identity.json, music_words.json
- docs/ — architecture.md, llm_design.md
- tools/ — warmup_tts.py, clean_slots.py, remove_sir.py, clean_cache.py
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

## Лицензия

MIT