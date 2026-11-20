# Макс — локальный голосовой ассистент

Голосовой дворецкий и медиацентр для Windows с гибридным распознаванием речи (Vosk + GigaAM-v3 + AEC), NLU-пайплайном, эмоциональным ядром личности и десктопным интерфейсом на Qt 6 (PySide6).

## Ключевые возможности

* **Гибридный ASR и эхоподавление:** аппаратно-программное подавление эха (`echoff`), мгновенный детектор wake-word и быстрых команд (`Vosk`), верификация и распознавание свободной речи (`GigaAM-v3 ONNX`), умное приглушение музыки (`audio ducking`) на время речи.
* **NLU-пайплайн и навыки:** нормализация текста, классификация интентов (`SetFit ONNX` + правила), извлечение сущностей (артисты, жанры, настроения, радио, даты/таймеры), многошаговые диалоги (`pending`).
* **Ядро личности (`PersonalityCore`):** динамическое внутреннее состояние (`mood`, `energy`, `valence`, `arousal`), поток сессии (`mind_stream`), сборка ответов из слотов (`opening + core + closing`), просодия и словарь ударений для TTS.
* **Музыка и аудио:** Яндекс.Музыка (Моя волна, артисты, жанры, лайки, кэширование треков), локальная медиатека с индексацией (`local_index.json`), интернет-радио, 7 пресетов эквалайзера VLC, система привычек и оценки пропусков (`appraisal`).
* **Графический интерфейс (Qt 6):** тёмная космическая тема со стеклянным эффектом, живой аватар с реакцией на микрофон, TTS, ритм музыки и эмоции (включая режим сна), зеркальный анализатор спектра, управление плеером, пресеты EQ, компактный чат и системный трей.
* **Системные и бытовые навыки:** погода (OpenWeatherMap), напоминания (по таймеру и по дате/времени), статус системы, безопасная очистка диска C, диалоговый LLM-скилл (AITUNNEL / OpenAI-compatible API).

---

## Требования

* **ОС:** Windows 10 / 11
* **Python:** 3.10 – 3.12
* **VLC Media Player** (используется для воспроизведения музыки, радио и TTS)
* **Модели распознавания речи (в `models/`):**
  * `vosk-model-small-ru-0.22` (для wake-word и hot-words)
  * `gigaam-v3-rnnt` (загружается автоматически через `onnx-asr` при первом запуске или прогреве)
* **Ключи и токены (в `config/`):**
  * `config/token.txt` — OAuth-токен Яндекс.Музыки
  * `config/yandex_key.txt` + `config/yandex_folder.txt` — API-ключ и `folder_id` Яндекс.Облака (SpeechKit TTS)
  * `config/weather_key.txt` — API-ключ OpenWeatherMap (погода)
  * `config/llm_key.txt` — токен AITUNNEL (для LLM-ответов, опционально)

---

## Установка и запуск

1. **Клонировать репозиторий и установить зависимости:**
   ```bash
   git clone [https://github.com/aleks20019010-web/maks.git](https://github.com/aleks20019010-web/maks.git)
   cd maks
   pip install -r requirements.txt
Установить VLC (если не установлен):

Bash
winget install VideoLAN.VLC
Скачать модель Vosk (vosk-model-small-ru-0.22) с alphacephei.com/vosk/models и распаковать в папку models/.

Настроить внешние пути в config/paths.json:

JSON
{
  "music_dir": "D:\\Music",
  "vlc_path": "C:\\Program Files\\VideoLAN\\VLC\\vlc.exe"
}
Создать файлы токенов в папке config/:

config/token.txt — токен Яндекс.Музыки (одна строка без пробелов)

config/yandex_key.txt — API-ключ Яндекс.Облака (TTS)

config/yandex_folder.txt — folder_id Яндекс.Облака (TTS)

config/weather_key.txt — ключ OpenWeatherMap (погода)

config/llm_key.txt — токен AITUNNEL (опционально)

Запуск и остановка:

DOS
.\start.bat
При запуске стартуют супервизор и воркеры (asr, tts, music, brain, ui).

Окно интерфейса сворачивается в системный трей при закрытии; полная остановка — через .\stop.bat или Ctrl+C в консоли супервизора.

Структура проекта
Plaintext
maks/
├── brain/                  # Мозг ассистента: router, NLU-пайплайн, навыки (skills),
│   │                       # appraisal (оценка пропусков), reminders, pending, liked_tracks
│   └── nlu/                # normalizer, intent (SetFit ONNX), entity (music/time), decision, pipeline
├── personality_core/       # Ядро личности: engine, self_state, mind_stream, composer,
│   │                       # prosody, style, phrase, policy, character, identity, rules/
├── memory/                 # Долговременная память: история прослушиваний, привычки, веса артистов
├── workers/                # Изолированные воркеры:
│   ├── asr/                # echoff AEC, Ring/Segmenter VAD, Vosk, GigaAM-v3, ducking
│   ├── tts/                # Синтез речи (Yandex SpeechKit), кэш фраз, телеметрия уровня голоса
│   ├── music/              # VLC-плеер, Яндекс.Музыка, локальный индекс, радио, эквалайзер
│   └── ui/                 # Десктоп-интерфейс на PySide6 (PlayerPanel, ChatPanel, Avatar, Visualizer)
├── shared/                 # Конфигурация, IPC-очереди, протокол сообщений, логирование, ui_bridge
├── responses/              # База фраз и шаблонов слотов (composer.json, music, weather, llm, ...)
├── config/                 # Конфиги воркеров, алиасы артистов/жанров/радио, character/identity
├── tools/                  # Вспомогательные скрипты (очистка диска, индексация, прогрев кэша)
├── tests/                  # Набор юнит-тестов pytest (259 тестов)
├── start.bat / stop.bat    # Скрипты запуска и остановки ассистента
└── warmup_tts.py           # Прогрев кэша синтеза речи
Примеры голосовых команд
Музыка и радио:

«Макс, включи музыку» / «включи мою волну» / «включи русскую музыку»

«Макс, включи Арию» / «включи рок» / «включи что-нибудь бодрое»

«Макс, включи радио Максимум» / «включи мои лайки»

«Дальше» / «Назад» / «Пауза» / «Продолжи» / «Стоп» (работают без wake-word)

«Громче» / «Тише» / «Громкость 50»

«Макс, что играет» / «мне нравится» / «мне не нравится»

Напоминания и время:

«Макс, напомни через 15 минут проверить духовку»

«Макс, напомни 29 октября в 10:00 записаться к врачу»

«Макс, какие напоминания» / «удали напоминание про врача»

Погода, система и диалог:

«Макс, какая погода» / «погода на завтра»

«Макс, статус системы» / «почисти диск C»

«Макс, расскажи, почему небо голубое» (передача свободного вопроса в LLM)

Тестирование
Запуск всех юнит-тестов:

Bash
python -m pytest tests/ -v
Покрытие (259 тестов):

NLU и классификация: test_normalizer, test_intent, test_intent_music, test_entity_music, test_decision_music, test_decision_system, test_decision_weather, test_pipeline, test_skill_resolver, test_music_words, test_music_classify, test_weather_intent, test_datetime_parser, test_time_bucket

Личность, память и навыки: test_context, test_phrase, test_appraisal, test_artist_weight, test_habits, test_relationship, test_reminders, test_controls, test_cleanup_recycle, test_llm_parser, test_llm_skill, test_llm_whitelist

ASR и музыкальный воркер: test_wake, test_hot_words, test_asr_duck, test_modes_duck, test_music_player_duck, test_play_index, test_playback_no_release_duck

Лицензия
MIT