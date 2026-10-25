# Макс — голосовой дворецкий

Голосовой ассистент на русском. Музыка, погода, статус системы, напоминания, ритуалы, очистка диска.

## Требования

- Windows 10/11
- Python 3.10+
- VLC
- Vosk-модель vosk-model-small-ru-0.22 в models/
- Токен Яндекса в config/yandex_key.txt

## Установка

1. git clone https://github.com/<логин>/maks.git
2. cd maks
3. pip install -r requirements.txt
4. winget install VideoLAN.VLC
5. Скачать Vosk-модель в models/
6. Создать config/secrets.json с токенами
7. Запустить start.bat

## Структура

- brain/ — ядро (router, skills, composer)
- personality_core/ — личность (engine, prosody, style, self_state)
- memory/ — память
- workers/ — asr, tts, music
- shared/ — config, protocol, ipc, log
- responses/ — JSON фразы
- config/ — настройки
- docs/ — документация
- tools/ — утилиты

## Лицензия

MIT