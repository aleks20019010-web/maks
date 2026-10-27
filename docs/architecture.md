# Макс — архитектура

**Версия:** 2026-10-27 (день 27)
**Имя:** Макс (русский дворецкий)
**Wake word:** «макс»

---

## 1. Что это

Макс — голосовой ассистент-дворецкий. Мультипроцессная архитектура:
- **brain** — ядро (router, навыки, личность, память).
- **asr** — распознавание речи (Vosk).
- **tts** — синтез речи (Yandex).
- **music** — музыка (VLC, Яндекс.Музыка).
- **supervisor** — супервизор (запуск, перезапуск, lock).

Общение — через **файловую шину** (`shared/ipc.py`), формат JSON (`shared/protocol.py`).

---

## 2. Дерево
D:\Maks
├── start.bat / stop.bat
├── supervisor.py ✅ 4 воркера, lock, перезапуск, /T
├── warmup_tts.py ✅ кэш TTS, BASE_DIR от file
│
├── brain/ ✅ ядро
│ ├── main.py ✅ цикл, tick 30с, ритуалы, привычки
│ ├── router.py ✅ команда → навык
│ ├── errors.py ✅ обработка ошибок
│ ├── pending.py ✅ ожидание уточнения
│ ├── relationship.py ✅ привычки, вес артиста
│ ├── reaction.py ✅ очередь appraisal
│ ├── reminders.py ✅ F1 напоминания (delay/date)
│ ├── datetime_parser.py ✅ парсинг дат
│ ├── composer.py ✅ сборка из слотов (opening/core/closing)
│ ├── appraisal/ ✅ G: 3 скипа → реакция → penalize
│ │ ├── events.py / history.py / policy.py / service.py
│ └── skills/
│ ├── base.py / _helpers.py ✅
│ ├── smalltalk.py ✅
│ ├── controls.py ✅
│ ├── music/ ✅ (skill, classify, _shared — обёртка)
│ ├── weather/ ✅ (skill, handlers, classify, sources)
│ ├── system/ ✅ (skill, cpu, ram, disks, max_sender, cleanup)
│ └── llm/ ✅ (skill, _parser, _prompt, _history, _whitelist)
│
├── workers/ ✅
│ ├── asr/ ✅ Vosk, hot words 42, wake по границам слов
│ ├── tts/ ✅ Yandex, кэш, rate/pitch/emotion, p.release()
│ └── music/ ✅ VLC, очередь, prefetch, play_index attempts ≤ 3
│
├── shared/ ✅
│ ├── config.py ✅ BASE_DIR от file, MEMORY_DB = data/memory.db
│ ├── music_words.py ✅ ОДИН источник: STOP_WORDS, PLAY_TRIGGERS, ...
│ ├── log.py / protocol.py / ipc.py
│ ├── metrics.py / yandex.py
│ ├── audio_cache.py / lock.py / state.py
│ └── sources.py ✅ CommandSource(str, Enum)
│
├── personality_core/ ✅ ядро личности
│ ├── engine.py ✅ оркестратор + prosody + composer
│ ├── types.py ✅ Event, Context, JarvisResponse
│ ├── context.py ✅ ContextBuilder
│ ├── policy.py ✅ PolicyEngine
│ ├── phrase.py ✅ PhrasePicker (night/drowsy/style/slot)
│ ├── response.py ✅ ResponseBuilder (fallback)
│ ├── self_state.py ✅ energy/mood/valence/arousal
│ ├── mind_stream.py ✅ поток сознания
│ ├── character.py ✅ style/humor/sarcasm/formality/temperature
│ ├── identity.py ✅ Макс (дворецкий)
│ ├── prosody.py ✅ rate/pitch/emotion + apply_prosody
│ ├── style.py ✅ night/tired/formal/friendly/normal
│ └── rules/ ✅ ~9 JSON
│
├── memory/ ✅
│ ├── engine.py ✅ одна БД
│ ├── storage.py / events.py / aggregates.py
│ └── preferences.py / reader.py / writer.py
│
├── responses/ ✅
│ ├── wake.json ✅ 59 фраз
│ ├── smalltalk.json ✅ 80 фраз
│ ├── errors.json ✅ 83 фразы
│ ├── music.json ✅ 287 фраз
│ ├── music_extra.json ✅ 37 фраз
│ ├── weather.json ✅ 131 фраза
│ ├── radio.json ✅ 12 фраз
│ ├── system.json ✅ 57 фраз
│ ├── appraisal.json ✅ 18 фраз
│ ├── jellyfin.json ✅ 69 фраз
│ ├── relationship.json ✅ 18 фраз
│ ├── rituals.json ✅ 56 фраз
│ ├── calendar.json ✅ 13 фраз
│ ├── llm.json ✅ 12 фраз
│ ├── scripts.json ✅ 10 фраз
│ ├── openings.json ✅ 10×12 ключей
│ └── composer.json ✅ 135 правил
│
├── config/ ✅
│ ├── workers.json / identity.json / music_words.json
│
├── models/ ✅ Vosk
├── data/ ✅
│ ├── memory.db ✅ одна БД
│ ├── relationship.json
│ ├── tts_cache/ ✅ 23 902 mp3
│ └── self_state.json ✅
│
├── tests/ ✅ 95 passed
├── tools/ ✅
├── docs/ ✅
│ ├── architecture.md ✅ (этот файл)
│ ├── итоги_2026-10-27.md ✅
│ └── ...
└── _archive/ ✅ (архив)

text

---

## 3. Потоки

### 3.1. Команда (голос)
ASR → brain_inbox → router.process_command()
→ навык (music / weather / system / controls / smalltalk)
→ _helpers.respond_and_say(intent)
→ engine.respond() → _process()
→ self_state.on_command()
→ mind_stream.on_command()
→ policy.evaluate() → phrase_set
→ composer.compose() → JarvisResponse (opening + core + closing)
→ prosody (rate/pitch/emotion) — если у фразы нет из JSON
→ protocol.say(text, rate, pitch, emotion)
→ tts_inbox → TTS worker → Yandex → VLC

text

### 3.2. Событие
music → brain_inbox → _handle_event()
→ music_started: state.set_current + relationship.record_play_habit

on_music_started (PLAY_ARTIST_STARTED / PLAY_TRACK_STARTED)
→ now_playing: state.set_current + respond_and_say("NOW_PLAYING")
→ track_finished / music_stopped: state.clear_current
→ wake: _maybe_ritual + respond_and_say("WAKE")

text

### 3.3. Tick (30 сек)
brain.main: now - last_tick >= 30
→ _tick()
→ self_state.on_tick() — energy падает днём, растёт ночью
→ mind_stream.on_tick(mood)
→ раз в 10 минут — лог energy/mood

text

### 3.4. Appraisal (G)
controls: «дальше» → next → appraisal.publish(SKIP)
→ reaction worker → service.handle()
→ history: 3 скипа одного артиста → reaction=True
→ penalize_artist(artist, -5.0)
→ respond_and_say("APPRAISAL_SKIP_3X", artist=...)

text

### 3.5. Напоминания (F1)
reminders worker (30 сек) → data/reminders.json
→ due_at <= now → respond_and_say("REMINDER_TRIGGER", ...)
→ mark done

text

### 3.6. Ритуалы (F1)
wake → _maybe_ritual()
→ bucket = morning/day/evening/night (см. time_bucket, 5 <= h < 12 — утро)
→ if not was_asked_today(f"ritual{bucket}")
→ respond_and_say(RITUAL_MORNING/EVENING/NIGHT)
→ mark_asked_today

text

---

## 4. Что где (таблица)

| Вопрос | Файл |
|---|---|
| Как выбирается фраза? | `personality_core/phrase.py` |
| Как меняется тон? | `personality_core/prosody.py` + `engine._process()` блок 4b |
| Как меняется настроение? | `personality_core/self_state.py` |
| Как работает память? | `memory/engine.py` (одна БД) |
| Как работает appraisal? | `brain/appraisal/` + `brain/reaction.py` |
| Как работают привычки? | `brain/relationship.py` + `memory/aggregates.py` |
| Как работают напоминания? | `brain/reminders.py` + `datetime_parser.py` |
| Как работают ритуалы? | `brain/main.py` (`_maybe_ritual`) |
| Как работает музыка? | `workers/music/` |
| Как работает TTS? | `workers/tts/` |
| Как работает ASR? | `workers/asr/` |
| Как работает LLM? | `brain/skills/llm/` |
| Как общаются воркеры? | `shared/ipc.py` + `shared/protocol.py` |
| Где STOP_WORDS? | `shared/music_words.py` (один источник) |
| Где `_time_bucket`? | `brain/main.py` + `tests/test_time_bucket.py` (границы) |

---

## 5. Ключевые цифры (2026-10-27)

| Метрика | Значение |
|---|---|
| День проекта | 27 |
| Воркеров | 4/4 |
| Тесты | **95 passed** (было 89) |
| Время тестов | **1.91s** |
| HOT WORDS | 42 |
| Скилов | 6 (system, controls, smalltalk, weather, music, cleanup) |
| TTS-кэш | 23 902 mp3 |
| Composer правил | 135 |
| Комбинаций фраз | ~21 600 |
| Appraisal: порог | 3 SKIP |
| Appraisal: кулдаун | 600 сек |
| Напоминания | delay / date, 2 слота |
| Wake word | «макс» (по границам слов, не «мак») |
| AEC | выключен |
| Prefetch timeout | 10 сек |
| play_index attempts | ≤ 3 |
| Prosody | rate/pitch/emotion по mood/energy |
| Style | night/tired/formal/friendly/normal |
| `shared/music_words.py` | 1 источник, 153/156 |
| `p.release()` | `workers/tts/player.py::play_tts_file` |
| `_session.py` | удалён (мёртвый код) |

---

## 6. Что закрыто

- **G (appraisal)** — 3 скипа → реакция → penalize. Кулдаун 600 сек. ✅
- **System** — CPU, RAM, диски, MAX, ACK → 3 STATUS. ✅
- **I (relationship)** — I.2 (вес вниз), I.3 (привычки), I.5 («Учту»), I.6, I.7 (тесты). ✅
- **F1 (ритуалы)** — утро/вечер/ночь, первый wake. ✅
- **F1 (напоминания)** — delay/date, 2 слота. ✅
- **TTS-кэш** — 23 902 mp3. ✅
- **Переименование** Джарвис → Макс. ✅
- **Prefetch-фикс** — timeout 10 сек, не блокирует. ✅
- **Prosody для обычных фраз** — rate/pitch/emotion по mood/energy. ✅
- **Style** — night/tired/formal/friendly/normal. ✅
- **Composer** — сборка из слотов (opening + core + closing). ✅
- **Openings** — 10 вариантов на ключ. ✅
- **LLM** — AITUNNEL, 2 потока (llm_worker + tts_worker), generation_id. ✅
- **`shared/music_words.py`** — один источник правды. ✅
- **`play_index` attempts** — ≤ 3. ✅
- **`p.release()`** — TTS VLC-плееры не копятся. ✅
- **`_session.py`** — удалён (мёртвый код). ✅

---

## 7. Что в планах

- **Discovery** (день 29–30) — `tracks_since_discovery`, `discovery_threshold` (10–20),
  `get_discovery_track` (`play_count = 0 AND last_played IS NULL AND blacklisted = 0`),
  `DISCOVERY_NEW` / `DISCOVERY_RETRY` / `DISCOVERY_REJECTED` (фразы есть, кода нет),
  `test_discovery.py` — 7 тестов.
- **O (Calendar)** — 2–3 часа.
- **`test_music_classify.py`** — 3 теста (5 минут).

---

## 8. Осознанные компромиссы (не баги)

- **SystemSkill: `time.sleep(2)` + `cpu_percent(interval=3)`** — brain блокируется
  на 5–13 сек на «статус». Причина: ACK должен прозвучать до сбора данных,
  а `cpu_percent(interval=3)` даёт реальный процент (без interval — 0% на простое).
  Если станет мешать — `interval=1` + `sleep(1)` (2 сек вместо 5).
- **`play_index`: `PREFETCH_WAIT_SEC=10` + `download_track=30`** — music worker
  блокируется до 40 сек на холодную. Причина: не пропускать первый трек
  («включи Арию» → играет Ария, а не второй трек). В обычной работе prefetch
  успевает за 1–3 сек, блокировки нет. `attempts ≤ 3` ограничивает **сверху**:
  3 битых подряд → сдаться (не 6 × 30 сек = 3 минуты).
- **`self_state.save()` на каждую команду** — I/O на SSD незаметно.
  На HDD — можно `save()` раз в 10 команд.
- **`aliases.json` может накопить мусор** («л лампу» → «Лампочка» — ASR услышал,
  Яндекс вернул). Это **не** баг `remember_alias` — он не может отличить
  «пользователь так сказал» от «ASR ошибся». Один раз почистить — ок,
  бороться в коде — перебор.

---

## 9. Что не делать

- Не добавлять presence, sound_detected, реакции на long_silence.
- Не добавлять initiative.py.
- Не переписывать PhrasePicker.
- Не трогать `responses/music.json`.
- Не включать AEC на ноуте — ждать колонок/компа.
- Не делать volume-duck — только pause.
- Не возвращать reflection / explanation — appraisal закрывает G.
- Не возвращать promises.py — reminders покрывает.

---

**Конец.**