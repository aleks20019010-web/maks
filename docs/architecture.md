MAX — архитектура

Домашний голосовой ассистент. Не AGI. Быстро, локально, LLM — последний fallback.
Документ фиксирует полную картину: от микрофона до TTS, с NLU как частью системы.

---

## 1. Назначение и принципы

- Домашний проект, не супер-ИИ. Задача — понимать команды, не «думать».
- Быстро и локально. LLM — только когда детерминированный код не справился.
- Не заплатки: делать правильно один раз, не возвращаясь.
- Скил не переинтерпретирует фразу. Он получает готовый `Command`.
- Semantic Router определяет намерение, но не запускает скил сам.
- Каждый этап roadmap — отдельный коммит с тестами.
- Забудешь сам — не делать то, чем не будешь пользоваться.
- Час в день — это час. Выходные — отдых.

---

## 2. Поток данных
Микрофон
↓
Vosk (ASR) — wake-word «макс», бип, ring buffer
↓
brain_inbox
↓
router.process_command
↓
┌─────────────────────────────────────────────┐
│ 1. Fast Commands (белый список) │
│ пауза, продолжить, next, prev, стоп, │
│ громкость, выход │
│ → Skill.handle(Command) напрямую │
└─────────────────────────────────────────────┘
↓ (None — не fast)
┌─────────────────────────────────────────────┐
│ 2. NLU Pipeline │
│ normalizer → context? → intent → │
│ skill_resolver → entity → decision │
└─────────────────────────────────────────────┘
↓
┌──────────┬──────────┬──────────┐
│ EXECUTE │ CLARIFY │ FALLBACK │
│ ↓ │ ↓ │ ↓ │
│ Skill │ Context │ LLM │
│ .handle │ pending │ ответ │
│ │ + TTS │ + TTS │
└──────────┴──────────┴──────────┘
↓
TTS (responses/*.json по ключам)

text

Fast Commands всегда первые. NLU — только если Fast вернул None.
LLM — только если NLU вернул FALLBACK или низкую уверенность.

---

## 3. Слои системы

| Слой | Путь | Ответственность |
|------|------|-----------------|
| ASR | `workers/asr/` | Vosk, wake-word, бип, публикация в `brain_inbox` |
| Router | `brain/router.py` | Fast → NLU → LLM, диспетчеризация |
| Fast Commands | `brain/fast_commands.py` | Белый список детерминированных команд |
| NLU | `brain/nlu/` | Normalizer → Intent → SkillResolver → Entity → Decision |
| Skills | `brain/skills/` | music, weather, system, cleanup, controls, llm, smalltalk, reminders |
| Workers | `workers/` | music (playback), asr, tts, catalog (кэш Яндекса) |
| Responses | `responses/*.json` | Фразы TTS по ключам |
| Shared | `shared/` | config, music_words, beep, pending (флаги) |
| Config | `config/` | алиасы, жанры, радио, `nlu.json` (пороги) |
| Data | `data/` | кэши: `intent_router.json`, `artists_cache.json` |
| Logs | `logs/` | `nlu.log` (DiagnosticEvent), `asr.log` |

Старый `classify()` переезжает в `fast_commands.py` и урезается до белого списка.
Не «постепенно» — в этом же этапе, иначе он продолжит перехватывать свободную речь.

---

## 4. NLU — контракты

Детали — в `brain/nlu/types.py`. Здесь — суть.

**Intent** — что сделать. `MUSIC_PLAY`, `MUSIC_WAVE`, `MUSIC_STOP`, `VOLUME_SET`,
`WEATHER_QUERY`, `SYSTEM_STATUS`, `REMINDER_ADD`, `CLEANUP_DISK`, `CLEANUP_RECYCLE`,
`SMALLTALK`, `LLM_QUESTION`, `UNKNOWN`.
Дата/город/исполнитель — не Intent, а Entity.

**Entity** — с чем/когда. Поля: `type` (artist, genre, radio, date, location),
`value`, `state` (`EntityState`), `match_confidence`, `candidates`.

**EntityState**:
- `FOUND` — параметр найден в каталоге
- `ABSENT` — пользователь не указал параметр («вруби чего-нибудь»)
- `UNRESOLVED` — параметр указан, но не найден в каталоге («включи Орион»)
- `AMBIGUOUS` — 2+ кандидата с близким confidence (разница < `AMBIGUITY_GAP`)

`UNRESOLVED ≠ ABSENT`. Отсутствие совпадения в каталоге ≠ отсутствие параметра.

**Command** — intent + entity + skill_name + action + confidence + normalized_text + original_text.

**Decision** — `command`, `action` (`DecisionAction`), `reason`, `response_key`.

**DecisionAction**: `EXECUTE`, `CLARIFY`, `FALLBACK`.

**Context** — `pending: PendingCommand | None`, `history`, `ttl_until`.
`PendingCommand` — intent, missing_entities, ttl. TTL 60с. Отмена и сброс после выполнения.

**DiagnosticEvent** — stage, intent, entity_state, decision_action, confidence, reason.
Формируется на каждом этапе NLU, пишется в `logs/nlu.log`.

**Confidence** — alias `float`. Два разных поля: `Entity.match_confidence` и
`Decision.confidence`. Не смешивать.

---

## 5. Границы и правила

**Fast vs NLU:**
- Fast — только белый список. Не `classify()` целиком.
- NLU — только если Fast вернул None.

**Entity extraction vs matching (этап 6):**
- `extract_candidate(text, intent) -> str | None` — правила (вычитание триггеров).
  `None` только при реальном ABSENT.
- `match(candidate, intent) -> Entity` — fuzzy (rapidfuzz) + pymorphy3.
- «Вруби чего-нибудь» → `extract_candidate` = `None` → ABSENT → `play_wave`.
- «Включи Орион» → `extract_candidate` = `"орион"` → match не нашёл → UNRESOLVED → CLARIFY.
  Не `play_wave`.

**Context:**
- Применяется **до** `IntentResolver`. Если pending активен и TTL не истёк —
  заполнение entity, не новый intent. `IntentResolver` не вызывается.
- `EntityResolver` про контекст не знает. Контекст — на уровне pipeline.
- TTL 60с. Отмена по «отмена»/«не надо»/«забудь». Сброс после выполнения.

**Пороги:**
- В `config/nlu.json`, не в коде: `intent.high`, `intent.medium`, `intent.low`,
  `entity.min_match`, `entity.ambiguity_gap`.
- Калибровка — `scripts/calibrate_intent.py` на реальных фразах из `logs/nlu.log`.
  Не «0.5 наугад».

**Регистрация скилов:**
- Декоратор `@register_skill(name, intents, entity, decision)` в `skills/registry.py`.
- Явный импорт в `skills/__init__.py`. Не автодискавери — `git grep register_skill`
  должен показывать все скилы.

**Совместимость с A5:**
- `WeatherSkill.parse_intent()` остаётся. Адаптер в `skills/weather/adapter.py`:
  `from_nlu(Command) -> WeatherQuery`.
- NLU не ломает A5. A5 не знает про NLU. Миграция — позже.

**Скил не переинтерпретирует:**
- Получает `Command`, исполняет. Если данных не хватает — возвращает
  `Decision(action=CLARIFY)`, pipeline сам решает.

---

## 6. Roadmap — 12 этапов

Этап = отдельный коммит с тестами. Не «всё в конце дня».

| № | Этап | Результат |
|---|------|-----------|
| 1 | `types.py` | Intent, Entity, EntityState, Candidate, Command, Decision, DecisionAction, PendingCommand, Context, DiagnosticEvent |
| 2 | `normalizer.py` | lowercase, пунктуация, STOP_PREFIXES, пробелы |
| 3 | `intent.py` | IndexedRouter (FastEmbed), UNKNOWN, `(Intent, confidence, level)`, `scripts/calibrate_intent.py` |
| 4 | `skill_resolver.py` + `registry.py` | Декоратор, явный импорт в `skills/__init__.py` |
| 5 | `entity/base.py` | `extract_candidate` + `match` — два метода |
| 6 | `entity/music.py` + `MusicCatalog` | Extraction — правила, matching — rapidfuzz + pymorphy3. AMBIGUOUS только при близких кандидатах |
| 7 | `decision/music.py` | По EntityState: FOUND→EXECUTE(play_artist), ABSENT→EXECUTE(play_wave), UNRESOLVED→CLARIFY, AMBIGUOUS→CLARIFY |
| 8 | `pipeline.py` + `logging.py` | `process(text, context=None)`, DiagnosticEvent в `logs/nlu.log` |
| 9 | Интеграция в `router.py` | `classify()` → `fast_commands.py` белый список. Fast → NLU → LLM |
| 10 | WeatherSkill | Адаптер, проверка универсальности архитектуры |
| 11 | `context.py` | PendingCommand, TTL 60с, отмена, сброс после выполнения |
| 12 | Остальные скилы | По триггерам, постепенный перенос |

---

## 7. Бэклог (не roadmap)

Фиксируем, но не делаем, пока нет триггера.

| # | Что | Триггер |
|---|-----|---------|
| B2 | VOLUME_SET «Громкость на 50» — «на» лишнее | болит |
| B3 | Hush (weya-ai) — шумоподавление ASR | ASR слышит ТВ чаще, чем тебя |
| B5 | `[DUCK] not playing` (State.Opening) — pause не ставится | живой тест ловит «стоп» сразу после «дальше» |
| B6 | PLAY_ARTIST_STARTED TTS лишний | после стабилизации |
| B7 | Алиасы без HTTP (resolve_alias дёргает Яндекс) | 30 мин, когда удобно |
| B8 | Локальные треки сразу (без get_artist_tracks_page) | сложно, не сейчас |
| C1–C11 | scripts, calendar, shuffle, TTS retry, smalltalk→self_state, Discovery, AEC | по триггерам из списка |

---

## 8. Правила работы

1. Не заплатки — делать правильно и 1 раз.
2. Не супер-ИИ — домашний проект. Решать кодом, быстро и локально. LLM — fallback.
3. Забудешь сам — не делать то, чем не будешь пользоваться.
4. Полные файлы, не патчи.
5. JSON — только редактор или Python, не PowerShell (BOM, пути).
6. Тесты в тот же день, не «потом».
7. Час в день — это час. Выходные — отдых.
8. Не заморачиваться — если болит, скажешь.
9. Semantic Router — после стабилизации ASR.
10. Артисты — отдельный слой (fuzzy по алиасам, semantic на топ-50 если нужно).

---

*Документ живой. Правки — по мере прохождения этапов, не «потом».*