# brain/nlu/entity/music.py
# Извлечение и поиск музыкальных сущностей.
#
# extract_candidate — правила, вычитание триггеров. Быстро, детерминированно.
# match — fuzzy по алиасам (config/aliases.json) + жанрам (config/genre_aliases.json)
#         + настроениям (config/mood_aliases.json) + радио (config/radio_stations.json
#         + config/radio_aliases.json).
#
# ПОЧЕМУ extract_candidate НЕ ИСПОЛЬЗУЕТ classify():
#   classify() из brain/skills/music/classify.py — это «весь путь»:
#   артист/трек/жанр/волна/лайки/радио/dislike. Он принимает решения.
#   А extract_candidate — только «что пользователь назвал?».
#   Смешивать нельзя: classify() вернёт {"action": "play_wave"} для
#   «включи музыку», а нам нужно ABSENT (не назвал параметр). Разные вопросы.
#   MusicSkill.classify() остаётся для скила. Здесь — свой, узкий, экстрактор.
#
# FUZZY:
#   rapidfuzz — если установлен (pip install rapidfuzz). Иначе — difflib
#   из stdlib (SequenceMatcher), как в workers/asr/main.py::_is_echo.
#   Качество с rapidfuzz выше (лучше обрабатывает опечатки), но проект
#   не должен падать без него. Fallback — честный, не «заглушка».
#
#   pymorphy3 — если установлен. Иначе — простая эвристика по окончаниям
#   (как в workers/music/search.py::search_artist — «арию» → «ария»).
#   Не идеально, но работает без зависимости.
#
# AMBIGUOUS:
#   Только если 2+ кандидата с разницей confidence < AMBIGUITY_GAP (0.15).
#   Один явный лидер — FOUND. «Ария» и «Ария (кавер)» с разницей 0.05 —
#   AMBIGUOUS. «Ария» (0.95) и «Ариzona» (0.40) — FOUND «Ария».
#
# MOOD (новое):
#   config/mood_aliases.json — {слово: tag_метагега}. «бодрое» → «Энергичное».
#   match() смотрит в mood_aliases ДО fuzzy — чтобы «бодрое» не ушло в артиста.
#   Entity(type="mood") → decision → play_mood → station_id (mood:energetic).
#
# RADIO (2026-11-07):
#   config/radio_stations.json — {ключ: {name, url, genre}}.
#   config/radio_aliases.json — {алиас: ключ}.
#   match() смотрит в radios ДО mood/artist/genre — чтобы «радио ваня»
#   не ушло в artist fuzzy (было: «радио» → AMBIGUOUS 4 artist).
#   Логика как в workers/music/radio.py::resolve_radio, но БЕЗ импорта
#   из workers (brain не должен зависеть от воркеров):
#     1) точный ключ станции → FOUND;
#     2) точный алиас → FOUND (ключ);
#     3) подстрока ключа (sorted by len desc) → FOUND;
#     4) подстрока алиаса (sorted by len desc) → FOUND;
#     5) «радио» без станции → ABSENT (включить любую — как play_wave).
#   Entity(type="radio") → decision → play_radio → workers/music/radio.py.
#
# RADIO FIX (2026-11-07, #2): безусловная radio-проверка.
#   Раньше: if "радио" in cand_lower or cand_lower in _load_radios():
#     «ваню» → условие False (нет «радио», нет ключа «ваню») →
#     artist fuzzy → UNRESOLVED → CLARIFY.
#   Теперь: _resolve_radio(cand_lower) ВСЕГДА (до mood/artist/genre).
#     «ваню» → алиас → "радио ваня" → FOUND(type="radio").
#     «ария» → _resolve_radio("ария") → None → mood/artist/genre (как было).
#   _resolve_radio быстрый (dict lookup + sorted по len desc), ложных
#   срабатываний на артистов нет: «ария» не в radio_stations/radio_aliases.
#
# RADIO FUZZY (2026-11-07, #3): fuzzy по radio_aliases.
#   ASR может исказить станцию: «включи ваню» → «включи юаню».
#   «юаню» нет в radio_aliases → _resolve_radio("юаню") → None →
#   artist fuzzy → UNRESOLVED → CLARIFY. Раньше: только костыль (алиас
#   «юаню» в JSON). Теперь: fuzzy по radio_aliases (difflib, порог 0.8)
#   → «юаню» → «ваню» (0.9) → "радио ваня" → FOUND(type="radio").
#   Порог 0.8 — безопасно: «юра» → «ваню» 0.3 (не FOUND), «включи арию»
#   → «ария» 0.2 (не radio). Fuzzy только для радио (artist/genre/mood —
#   отдельно, ниже, в 5a/5b/5c).

import os
import re
import difflib

from shared import config as cfg
from shared.log import log
from brain.nlu.types import (
    Entity, EntityCandidate, EntityState, Intent,
)
from brain.nlu.entity.base import EntityExtractor


# ── Пороги ───────────────────────────────────────────────────
MIN_MATCH_CONFIDENCE = 0.55   # ниже — UNRESOLVED
AMBIGUITY_GAP = 0.15          # разница между топ-1 и топ-2 < gap → AMBIGUOUS
RADIO_FUZZY_THRESHOLD = 0.80  # fuzzy по radio_aliases (2026-11-07, #3)


# ── Триггеры (вычитаются из текста перед кандидатом) ─────────
from shared.music_words import (
    PLAY_TRIGGERS, NOISE_WORDS, ARTIST_HINTS, TRACK_HINTS,
)


# ── Загрузка каталогов ───────────────────────────────────────
_aliases_cache: dict | None = None
_genres_cache: dict | None = None
_moods_cache: dict | None = None
_radios_cache: dict | None = None
_radio_aliases_cache: dict | None = None


def _load_aliases() -> dict:
    """config/aliases.json — {alias: {artist_id, artist_name}}."""
    global _aliases_cache
    if _aliases_cache is not None:
        return _aliases_cache
    path = cfg.ALIASES_FILE
    if not os.path.exists(path):
        _aliases_cache = {}
        return _aliases_cache
    try:
        import json
        with open(path, "r", encoding="utf-8") as f:
            _aliases_cache = json.load(f)
    except Exception as e:
        log(f"entity.music: aliases.json: {e}", tag="BRAIN", level="WARN")
        _aliases_cache = {}
    return _aliases_cache


def _load_genres() -> dict:
    """config/genre_aliases.json — {alias: station_id}."""
    global _genres_cache
    if _genres_cache is not None:
        return _genres_cache
    path = os.path.join(cfg.BASE_DIR, "config", "genre_aliases.json")
    if not os.path.exists(path):
        _genres_cache = {}
        return _genres_cache
    try:
        import json
        with open(path, "r", encoding="utf-8") as f:
            _genres_cache = json.load(f)
    except Exception as e:
        log(f"entity.music: genre_aliases.json: {e}", tag="BRAIN", level="WARN")
        _genres_cache = {}
    return _genres_cache


def _load_moods() -> dict:
    """config/mood_aliases.json — {слово: tag_метагега}.

    «бодрое» → «Энергичное». tag потом идёт в client.metatag(tag),
    который возвращает station_id (mood:energetic).
    """
    global _moods_cache
    if _moods_cache is not None:
        return _moods_cache
    path = os.path.join(cfg.BASE_DIR, "config", "mood_aliases.json")
    if not os.path.exists(path):
        _moods_cache = {}
        return _moods_cache
    try:
        import json
        with open(path, "r", encoding="utf-8") as f:
            _moods_cache = json.load(f)
    except Exception as e:
        log(f"entity.music: mood_aliases.json: {e}", tag="BRAIN", level="WARN")
        _moods_cache = {}
    return _moods_cache


def _load_radios() -> dict:
    """config/radio_stations.json — {ключ: {name, url, genre}}."""
    global _radios_cache
    if _radios_cache is not None:
        return _radios_cache
    path = os.path.join(cfg.BASE_DIR, "config", "radio_stations.json")
    if not os.path.exists(path):
        _radios_cache = {}
        return _radios_cache
    try:
        import json
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        _radios_cache = data if isinstance(data, dict) else {}
    except Exception as e:
        log(f"entity.music: radio_stations.json: {e}", tag="BRAIN", level="WARN")
        _radios_cache = {}
    return _radios_cache


def _load_radio_aliases() -> dict:
    """config/radio_aliases.json — {алиас: ключ}."""
    global _radio_aliases_cache
    if _radio_aliases_cache is not None:
        return _radio_aliases_cache
    path = os.path.join(cfg.BASE_DIR, "config", "radio_aliases.json")
    if not os.path.exists(path):
        _radio_aliases_cache = {}
        return _radio_aliases_cache
    try:
        import json
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        _radio_aliases_cache = data if isinstance(data, dict) else {}
    except Exception as e:
        log(f"entity.music: radio_aliases.json: {e}", tag="BRAIN", level="WARN")
        _radio_aliases_cache = {}
    return _radio_aliases_cache


# ── Fuzzy-поиск: rapidfuzz, иначе difflib ────────────────────
def _similarity(a: str, b: str) -> float:
    """0.0–1.0. rapidfuzz, если есть, иначе difflib.SequenceMatcher."""
    if not a or not b:
        return 0.0
    try:
        from rapidfuzz import fuzz
        return float(fuzz.ratio(a, b)) / 100.0
    except ImportError:
        return difflib.SequenceMatcher(None, a, b).ratio()


def _normalize_case(word: str) -> str:
    """Грубая нормализация русских падежей."""
    if not word:
        return word
    w = word.lower().strip()
    variants = [w]
    if w.endswith("ю"):
        variants.append(w[:-1] + "я")
    if w.endswith("у"):
        variants.append(w[:-1] + "а")
        variants.append(w[:-1])
    if w.endswith("а"):
        variants.append(w[:-1])
    if w.endswith("и"):
        variants.append(w[:-1] + "я")
        variants.append(w[:-1] + "ь")
    if w.endswith("ей"):
        variants.append(w[:-2] + "я")
    if w.endswith("ой"):
        variants.append(w[:-2] + "а")
    seen = set()
    out = []
    for v in variants:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out[0] if len(out) == 1 else out


def _all_case_variants(word: str) -> list[str]:
    """Вернуть список вариантов (сам word + падежи)."""
    if not word:
        return []
    w = word.lower().strip()
    variants = [w]
    extra = _normalize_case(w)
    if isinstance(extra, list):
        variants.extend(extra)
    elif extra and extra != w:
        variants.append(extra)
    seen = set()
    out = []
    for v in variants:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


# ── RADIO (2026-11-07): resolve как в radio.py, но без импорта ──
def _resolve_radio(candidate: str) -> str | None:
    """Найти ключ станции по кандидату (аналог radio.py::resolve_radio)."""
    if not candidate:
        return None
    q = candidate.lower().strip()
    if not q:
        return None

    radios = _load_radios()
    aliases = _load_radio_aliases()

    # 1) точный ключ
    if q in radios:
        return q
    # 2) точный алиас
    key = aliases.get(q)
    if key and key in radios:
        return key
    # 3) подстрока ключа
    for station_key in sorted(radios.keys(), key=len, reverse=True):
        if station_key in q:
            return station_key
    # 4) подстрока алиаса
    for alias in sorted(aliases.keys(), key=len, reverse=True):
        if alias in q:
            key = aliases[alias]
            if key in radios:
                return key
    return None


# ── RADIO FUZZY (2026-11-07, #3): fuzzy по radio_aliases ──────
def _fuzzy_radio(candidate: str) -> str | None:
    """Fuzzy по radio_aliases (difflib). «юаню» → «ваню» → «радио ваня».

    Порог RADIO_FUZZY_THRESHOLD (0.8) — безопасно: «юра» → «ваню» 0.3
    (не FOUND), «включи арию» → «ария» 0.2 (не radio).
    Возвращает ключ станции или None.
    """
    if not candidate:
        return None
    q = candidate.lower().strip()
    if not q:
        return None
    radios = _load_radios()
    aliases = _load_radio_aliases()
    if not radios or not aliases:
        return None

    best_score = 0.0
    best_key = None
    for alias, key in aliases.items():
        if key not in radios:
            continue
        score = difflib.SequenceMatcher(None, q, alias.lower()).ratio()
        if score > best_score:
            best_score = score
            best_key = key
    if best_score >= RADIO_FUZZY_THRESHOLD:
        return best_key
    return None


class MusicEntityExtractor:
    """Извлечение и поиск музыкальных сущностей."""

    supported_intents: tuple[Intent, ...] = (
        Intent.MUSIC_PLAY,
        Intent.MUSIC_WAVE,
    )

    def supports(self, intent: Intent) -> bool:
        return intent in self.supported_intents

    # ========================================================
    # EXTRACT_CANDIDATE
    # ========================================================
    def extract_candidate(self, text: str, intent: Intent) -> str | None:
        """Извлечь кандидата из текста."""
        if not text:
            return None
        if intent not in self.supported_intents:
            return None

        try:
            t = text.lower().strip()
            t = re.sub(r"^(макс|макса|максу|слушаю|готов|на связи)\s+", "", t).strip()

            if intent == Intent.MUSIC_WAVE:
                return None

            q = t
            for trigger in PLAY_TRIGGERS:
                q = re.sub(rf"\b{re.escape(trigger)}\b", " ", q)
            for noise in NOISE_WORDS:
                q = re.sub(rf"\b{re.escape(noise)}\b", " ", q)
            for hint in ARTIST_HINTS + TRACK_HINTS:
                q = re.sub(rf"\b{re.escape(hint)}\b", " ", q)
            q = re.sub(r"\s+", " ", q).strip(" ,.!?-")

            if not q:
                return None

            if q in ("музыку", "музыка", "музыки", "музыке"):
                return None

            return q

        except Exception as e:
            log(f"entity.music.extract_candidate: {e}", tag="BRAIN", level="WARN")
            return None

    # ========================================================
    # MATCH
    # ========================================================
    def match(self, candidate: str, intent: Intent) -> Entity:
        """Найти candidate в каталоге (radio + mood + алиасы + жанры)."""
        if not candidate:
            return Entity.absent()
        if intent not in self.supported_intents:
            return Entity.absent()

        try:
            moods = _load_moods()
            aliases = _load_aliases()
            genres = _load_genres()

            cand_lower = candidate.lower().strip()

            # 0. RADIO (2026-11-07): до mood/artist/genre.
            #    FIX (2026-11-07, #2): безусловная radio-проверка.
            #    FIX (2026-11-07, #3): fuzzy по radio_aliases — ASR может
            #    исказить станцию: «включи ваню» → «включи юаню». «юаню»
            #    нет в radio_aliases → _resolve_radio → None → artist fuzzy
            #    → UNRESOLVED → CLARIFY. Теперь: fuzzy по radio_aliases
            #    (difflib, порог 0.8) → «юаню» → «ваню» (0.9) → «радио ваня».
            #    Порог 0.8 — безопасно: «юра» → «ваню» 0.3 (не FOUND).
            radio_key = _resolve_radio(cand_lower)
            if radio_key:
                return Entity.found("radio", radio_key, 1.0, "radio")
            # «радио» без станции (или «радио» + неизвестная станция) → ABSENT
            if cand_lower == "радио":
                return Entity(
                    type="radio",
                    value=None,
                    state=EntityState.ABSENT,
                    match_confidence=1.0,
                    source="radio_no_station",
                )
            # FIX (2026-11-07, #3): fuzzy по radio_aliases.
            fuzzy_radio_key = _fuzzy_radio(cand_lower)
            if fuzzy_radio_key:
                log(f"entity.music: radio fuzzy «{cand_lower}» → "
                    f"«{fuzzy_radio_key}»", tag="BRAIN", level="DEBUG")
                return Entity.found("radio", fuzzy_radio_key, 0.85, "radio_fuzzy")

            # 1. MOOD — точное совпадение.
            if cand_lower in moods:
                return Entity.found("mood", moods[cand_lower], 1.0, "mood_alias")

            # 2. ARTIST — точное совпадение по алиасу.
            if cand_lower in aliases:
                entry = aliases[cand_lower]
                name = entry.get("artist_name") if isinstance(entry, dict) else None
                if name:
                    return Entity.found("artist", name, 1.0, "alias")

            # 3. GENRE — точное совпадение.
            if cand_lower in genres:
                return Entity.found("genre", cand_lower, 1.0, "genre_alias")

            # 4. Падежи.
            for variant in _all_case_variants(cand_lower):
                if variant in moods:
                    return Entity.found("mood", moods[variant], 0.95, "case_mood")
                if variant in aliases:
                    entry = aliases[variant]
                    name = entry.get("artist_name") if isinstance(entry, dict) else None
                    if name:
                        return Entity.found("artist", name, 0.95, "case_alias")
                if variant in genres:
                    return Entity.found("genre", variant, 0.95, "case_genre")

            # 5. Fuzzy по mood + алиасам + жанрам.
            scored: list[EntityCandidate] = []

            for mood_key, mood_tag in moods.items():
                if len(mood_key) < 3:
                    continue
                score = _similarity(cand_lower, mood_key)
                if score >= MIN_MATCH_CONFIDENCE:
                    scored.append(EntityCandidate(
                        type="mood", value=mood_tag,
                        match_confidence=score, source="fuzzy_mood",
                    ))

            for alias_key, entry in aliases.items():
                if len(alias_key) < 3:
                    continue
                if alias_key in ("макс", "слушаю", "назад", "что"):
                    continue
                score = _similarity(cand_lower, alias_key)
                if score >= MIN_MATCH_CONFIDENCE:
                    name = entry.get("artist_name") if isinstance(entry, dict) else None
                    if not name:
                        continue
                    scored.append(EntityCandidate(
                        type="artist", value=name,
                        match_confidence=score, source="fuzzy",
                    ))

            for genre_key in genres:
                if len(genre_key) < 3:
                    continue
                score = _similarity(cand_lower, genre_key)
                if score >= MIN_MATCH_CONFIDENCE:
                    scored.append(EntityCandidate(
                        type="genre", value=genre_key,
                        match_confidence=score, source="fuzzy_genre",
                    ))

            if not scored:
                return Entity.unresolved(candidate, match_confidence=0.0)

            scored.sort(key=lambda c: c.match_confidence, reverse=True)
            top = scored[0]
            if len(scored) >= 2:
                second = scored[1]
                gap = top.match_confidence - second.match_confidence
                same_value = top.value == second.value
                if gap < AMBIGUITY_GAP and not same_value:
                    return Entity.ambiguous(scored[:5])

            return Entity.found(top.type, top.value, top.match_confidence, top.source)

        except Exception as e:
            log(f"entity.music.match: {e}", tag="BRAIN", level="WARN")
            return Entity.unresolved(candidate, match_confidence=0.0)


_extractor: MusicEntityExtractor | None = None


def get() -> MusicEntityExtractor:
    global _extractor
    if _extractor is None:
        _extractor = MusicEntityExtractor()
    return _extractor