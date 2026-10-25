markdown
# Макс — архитектура

**Версия:** 2026-10-23 (день 25)
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
├── start.bat / stop.bat / status.bat
├── supervisor.py ✅ 4 воркера, lock, перезапуск
├── warmup_tts.py ✅ кэш TTS, планировщик 8:00
│
├── brain/ ✅ ядро
│ ├── main.py ✅ цикл, tick 30с, ритуалы, привычки
│ ├── router.py ✅ команда → навык
│ ├── errors.py ✅ обработка ошибок
│ ├── pending.py ✅ ожидание уточнения
│ ├── relationship.py ✅ привычки, вес артиста, «Учту»
│ ├── reaction.py ✅ очередь appraisal
│ ├── reminders.py ✅ F1 напоминания (delay/date)
│ ├── datetime_parser.py ✅ парсинг дат
│ ├── appraisal/ ✅ G: 3 скипа → реакция → penalize
│ │ ├── events.py / history.py / policy.py / service.py
│ └── skills/
│ ├── base.py / _helpers.py ✅
│ ├── smalltalk.py ✅
│ ├── controls.py ✅
│ ├── music/ ✅
│ ├── weather/ ✅
│ └── system/ ✅
│
├── workers/ ✅
│ ├── asr/ ✅ Vosk, hot words 36
│ ├── tts/ ✅ Yandex, кэш, rate/pitch/emotion
│ └── music/ ✅ VLC, очередь, prefetch (timeout 10с)
│
├── shared/ ✅
│ ├── config.py ✅ MEMORY_DB = data/memory.db
│ ├── log.py / protocol.py / ipc.py
│ ├── metrics.py / yandex.py
│ ├── audio_cache.py / lock.py / state.py
│
├── personality_core/ ✅ ядро личности
│ ├── engine.py ✅ оркестратор + prosody для обычных фраз
│ ├── types.py ✅ Event, Context, JarvisResponse
│ ├── context.py ✅ ContextBuilder
│ ├── policy.py ✅ PolicyEngine
│ ├── phrase.py ✅ PhrasePicker (night/drowsy)
│ ├── response.py ✅ ResponseBuilder (rate/pitch/emotion)
│ ├── self_state.py ✅ energy/mood/valence/arousal
│ ├── mind_stream.py ✅ поток сознания (play/skip/finish/cmd/tick)
│ ├── character.py ✅ style/humor/sarcasm/formality/temperature
│ ├── identity.py ✅ Макс (дворецкий)
│ ├── prosody.py ✅ rate/pitch/emotion + apply_prosody
│ ├── style.py ⚠️ заглушка (для LLM, K+)
│ └── rules/ ✅ ~9 JSON
│
├── memory/ ✅
│ ├── engine.py ✅ одна БД
│ ├── storage.py / events.py / aggregates.py
│ ├── preferences.py / reader.py / writer.py
│
├── responses/ ✅
│ ├── wake.json ✅ 59 фраз
│ ├── smalltalk.json ✅ 80 фраз
│ ├── errors.json ✅ 83 фразы
│ ├── music.json ✅ 295 фраз
│ ├── music_extra.json ✅ 37 фраз
│ ├── weather.json ✅ 154 фразы
│ ├── radio.json ✅ 12 фраз
│ ├── system.json ✅ 57 фраз
│ ├── appraisal.json ✅ 18 фраз
│ ├── jellyfin.json ✅ 69 фраз
│ ├── relationship.json ✅ 18 фраз
│ ├── rituals.json ✅ 15 фраз
│ ├── calendar.json ✅ 13 фраз
│ └── llm.json ✅ 12 фраз
│
├── config/ ✅
│ ├── workers.json / identity.json / music_words.json
│
├── models/ ✅ Vosk
├── data/ ✅
│ ├── memory.db ✅ одна БД
│ ├── relationship.json
│ ├── tts_cache/ ✅ 121 974 mp3
│ └── self_state.json ✅
│
├── tests/ ⚠️ 21 тест, 13/13 новых
├── tools/ ✅
├── docs/ ✅
└── _archive/ ✅

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
→ response_builder.build() → JarvisResponse
→ prosody (rate/pitch/emotion) — если у фразы нет из JSON
→ protocol.say(text, rate, pitch, emotion)
→ tts_inbox → TTS worker → Yandex → VLC

text

### 3.2. Событие
music → brain_inbox → _handle_event()
→ music_started: state.set_current + relationship.record_play_habit
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
wake → maybe_ritual()
→ bucket = morning/day/evening/night
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
| Как общаются воркеры? | `shared/ipc.py` + `shared/protocol.py` |

---

## 5. Ключевые цифры (2026-10-23)

| Метрика | Значение |
|---|---|
| День проекта | 25 |
| Воркеров | 4/4 |
| HOT WORDS | 36 |
| Скилов | 5 (system, controls, smalltalk, weather, music) |
| TTS-кэш | 121 974 фраз |
| Appraisal: порог | 3 SKIP |
| Appraisal: кулдаун | 600 сек |
| Привычка | `habit:day` → `{"Ария": 6}` |
| Вес КИНО | 216 → 211 |
| Напоминания | `delay` / `date`, 2 слота |
| Тесты | 13/13 |
| Wake word | «макс» |
| AEC | выключен |
| Prefetch timeout | 10 сек (playback) / 30 сек (download) |
| Prosody | rate/pitch/emotion по mood/energy |

---

## 6. Что закрыто

- **G (appraisal)** — 3 скипа → реакция → penalize. Кулдаун 600 сек. ✅
- **System** — CPU, RAM, диски, MAX, ACK → 3 STATUS. ✅
- **I (relationship)** — I.2 (вес вниз), I.3 (привычки), I.5 («Учту»), I.6, I.7 (тесты). I.4 убрано. ✅
- **F1 (ритуалы)** — утро/вечер/ночь, первый wake. ✅
- **F1 (напоминания)** — delay/date, 2 слота. ✅
- **TTS-кэш** — 121 974 фраз. ✅
- **Переименование** Джарвис → Макс. ✅
- **Prefetch-фикс** — timeout 10 сек, не блокирует. ✅
- **Prosody для обычных фраз** — rate/pitch/emotion по mood/energy. ✅

---

## 7. Что в планах

- **M (Jellyfin, scripts)** — 3–4 часа.
- **O (Calendar)** — 2–3 часа.
- **LLM (K+)** — 4–6 часов. Дизайн — в `docs/llm_design.md`.
- **`mood`-фильтр в `phrase.py`** — 30 минут (опционально).
- **`style.py` / `prosody.py` полные** — для LLM (K+).
- **`composer.py`** — после LLM.
- **`memory/habits.py`** — потом.
- **`mind_stream` расширить** — потом.

---

## 8. Что не делать

- Не добавлять presence, sound_detected, реакции на long_silence.
- Не добавлять initiative.py.
- Не делать temperature «ручкой живости».
- Не переписывать PhrasePicker.
- Не добавлять LLM-tools сейчас (только место).
- Не трогать system, G, I, F1, reminders, пока стабильно не проработают 1–2 дня.
- Не трогать `responses/music.json` (раздут, appraisal — отдельно).
- Не включать AEC на ноуте — ждать колонок/компа.
- Не делать volume-duck — только pause.
- Не возвращать `reflection` / `explanation` — appraisal закрывает G.
- Не возвращать `promises.py` — reminders покрывает.

---

**Конец.**