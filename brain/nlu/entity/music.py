# brain/nlu/entity/music.py
# Извлечение и поиск музыкальных сущностей.
#
# extract_candidate — правила, вычитание триггеров. Быстро, детерминированно.
# match — fuzzy по алиасам (config/aliases.json) + жанрам (config/genre_aliases.json)
#         + настроениям (config/mood_aliases.json).
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
#   config/mood_aliases.json — {слово: tag_метатега}. «бодрое» → «Энергичное».
#   match() смотрит в mood_aliases ДО fuzzy — чтобы «бодрое» не ушло в артиста.
#   Entity(type="mood") → decision → play_mood → station_id (mood:energetic).

import os
import re

from shared import config as cfg
from shared.log import log
from brain.nlu.types import (
    Entity, EntityCandidate, EntityState, Intent,
)
from brain.nlu.entity.base import EntityExtractor


# ── Пороги ───────────────────────────────────────────────────
# Не в коде, а здесь — потом вынесем в config/nlu.json (этап 8).
# Пока константы с именами, чтобы было видно, что тюнить.
MIN_MATCH_CONFIDENCE = 0.55   # ниже — UNRESOLVED
AMBIGUITY_GAP = 0.15          # разница между топ-1 и топ-2 < gap → AMBIGUOUS


# ── Триггеры (вычитаются из текста перед кандидатом) ─────────
# Источник — shared/music_words.py (один источник правды).
# Но здесь свой набор: только то, что реально мешает извлечению.
# Не PLAY_TRIGGERS целиком — «включи» вычитается, а «группу» — тоже,
# но «поставь» и «вруби» — да. ARTIST_HINTS / TRACK_HINTS вычитаются
# отдельно, потому что они определяют ТИП сущности.
from shared.music_words import (
    PLAY_TRIGGERS, NOISE_WORDS, ARTIST_HINTS, TRACK_HINTS,
)


# ── Загрузка каталогов ───────────────────────────────────────
_aliases_cache: dict | None = None
_genres_cache: dict | None = None
_moods_cache: dict | None = None


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
    """config/mood_aliases.json — {слово: tag_метатега}.

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


# ── Fuzzy-поиск: rapidfuzz, иначе difflib ────────────────────
def _similarity(a: str, b: str) -> float:
    """0.0–1.0. rapidfuzz, если есть, иначе difflib.SequenceMatcher."""
    if not a or not b:
        return 0.0
    try:
        from rapidfuzz import fuzz
        return float(fuzz.ratio(a, b)) / 100.0
    except ImportError:
        import difflib
        return difflib.SequenceMatcher(None, a, b).ratio()


# ── Простая нормализация падежей (без pymorphy3) ─────────────
# workers/music/search.py::search_artist делает похожее. Логика:
#   «арию» → «ария», «киша» → «киш», «сплина» → «сплин».
# Не идеально, но без зависимости. С pymorphy3 — лучше.
def _normalize_case(word: str) -> str:
    """Грубая нормализация русских падежей. Возвращает несколько вариантов."""
    if not word:
        return word
    w = word.lower().strip()
    variants = [w]
    # Винительный/родительный: «арию» → «ария», «киша» → «киш»
    if w.endswith("ю"):
        variants.append(w[:-1] + "я")
    if w.endswith("у"):
        variants.append(w[:-1] + "а")
        variants.append(w[:-1])        # «кишу» → «киш»
    # Родительный: «сплина» → «сплин», «арии» → «ария»
    if w.endswith("а"):
        variants.append(w[:-1])
    if w.endswith("и"):
        variants.append(w[:-1] + "я")
        variants.append(w[:-1] + "ь")
    # Творительный: «арией» → «ария»
    if w.endswith("ей"):
        variants.append(w[:-2] + "я")
    if w.endswith("ой"):
        variants.append(w[:-2] + "а")
    # Дубликаты
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
    # Дубликаты
    seen = set()
    out = []
    for v in variants:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


# ── MusicEntityExtractor ────────────────────────────────────
class MusicEntityExtractor:
    """Извлечение и поиск музыкальных сущностей.

    Поддерживает MUSIC_PLAY и MUSIC_WAVE.
    MUSIC_STOP/PAUSE/RESUME/NEXT/PREV — не про entity (это controls).
    """

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
        """Извлечь кандидата из текста.

        «включи арию»       → "арию" (force_artist из ARTIST_HINTS не было,
                              но _is_short_query → artist; вернём "арию")
        «включи группу ария» → "ария" (вычли «группу»)
        «включи рок»        → "рок"
        «включи музыку»     → None (ABSENT — не назвал параметр)
        «вруби чего-нибудь» → None (ABSENT)
        «моя волна»         → None (ABSENT — волна без параметра)

        Не бросает. При ошибке — None (как ABSENT).
        """
        if not text:
            return None
        if intent not in self.supported_intents:
            return None

        try:
            t = text.lower().strip()
            # Убрать STOP_PREFIXES («макс», «слушаю») — уже сделал normalizer,
            # но на всякий случай. normalizer в pipeline вызывается ДО entity.
            # Здесь дублируем лёгкую очистку, чтобы экстрактор был самодостаточен.
            t = re.sub(r"^(макс|макса|максу|слушаю|готов|на связи)\s+", "", t).strip()

            # MUSIC_WAVE — параметра нет по определению (волна = жанр-настроение).
            # Но если в тексте есть «русск» — это language, не entity.
            # Entity для MUSIC_WAVE — ABSENT (нет artist/genre/track).
            if intent == Intent.MUSIC_WAVE:
                return None

            # MUSIC_PLAY — вычитаем триггеры, оставляем кандидата.
            # 1. PLAY_TRIGGERS: «включи», «поставь», «вруби», «сыграй»
            q = t
            for trigger in PLAY_TRIGGERS:
                q = re.sub(rf"\b{re.escape(trigger)}\b", " ", q)
            # 2. NOISE_WORDS: «мне», «пожалуйста», «что-нибудь», «давай»
            for noise in NOISE_WORDS:
                q = re.sub(rf"\b{re.escape(noise)}\b", " ", q)
            # 3. ARTIST_HINTS / TRACK_HINTS: «группу», «исполнителя», «песню»
            #    Они определяют ТИП, не значение. Вычитаем, тип не сохраняем
            #    здесь (тип решает match — по каталогу).
            for hint in ARTIST_HINTS + TRACK_HINTS:
                q = re.sub(rf"\b{re.escape(hint)}\b", " ", q)
            q = re.sub(r"\s+", " ", q).strip(" ,.!?-")

            if not q:
                return None

            # «включи что-нибудь» / «поставь эдакое» — q пуст после NOISE_WORDS.
            # «включи музыку» — «музыку» осталась, но это не артист.
            # Спец-случай: «музыку» / «музыка» — это ABSENT (play_wave).
            if q in ("музыку", "музыка", "музыки", "музыке"):
                return None

            # «мою волну» / «русскую волну» — это MUSIC_WAVE, не PLAY.
            # Но intent уже MUSIC_PLAY (IntentResolver ошибся?). Не наше дело —
            # вернём q, decision разберётся. Хотя лучше — None, чтобы не
            # включить артиста «Моя Волна». Возвращаем q, но decision/music
            # проверит «волна» и переключит на play_wave.
            # Пока — q как есть.
            return q

        except Exception as e:
            log(f"entity.music.extract_candidate: {e}", tag="BRAIN", level="WARN")
            return None

    # ========================================================
    # MATCH
    # ========================================================
    def match(self, candidate: str, intent: Intent) -> Entity:
        """Найти candidate в каталоге (mood + алиасы + жанры).

        Возвращает Entity: FOUND / UNRESOLVED / AMBIGUOUS.
        Не бросает.

        Порядок проверки:
          1. mood_aliases (точное + падежи) — «бодрое» → FOUND(type="mood").
             ДО алиасов/жанров, чтобы «бодрое» не ушло в артиста/жанр.
          2. aliases (точное + падежи) — «ария» → FOUND(type="artist").
          3. genres (точное + падежи) — «рок» → FOUND(type="genre").
          4. fuzzy по всем трём — если точного нет.
        """
        if not candidate:
            return Entity.absent()
        if intent not in self.supported_intents:
            return Entity.absent()

        try:
            moods = _load_moods()
            aliases = _load_aliases()
            genres = _load_genres()

            cand_lower = candidate.lower().strip()

            # 1. MOOD — точное совпадение (до артистов/жанров!).
            #    «бодрое» → «Энергичное» (tag метатега).
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

            # 4. Падежи: «арию» → «ария», «киша» → «киш», «бодрое» → «бодрое».
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

            # 5. Fuzzy по mood + алиасам + жанрам — собираем кандидатов.
            scored: list[EntityCandidate] = []

            # 5a. Mood fuzzy (высокий приоритет: type="mood").
            for mood_key, mood_tag in moods.items():
                if len(mood_key) < 3:
                    continue
                score = _similarity(cand_lower, mood_key)
                if score >= MIN_MATCH_CONFIDENCE:
                    scored.append(EntityCandidate(
                        type="mood",
                        value=mood_tag,
                        match_confidence=score,
                        source="fuzzy_mood",
                    ))

            # 5b. Artist fuzzy.
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
                        type="artist",
                        value=name,
                        match_confidence=score,
                        source="fuzzy",
                    ))

            # 5c. Genre fuzzy.
            for genre_key in genres:
                if len(genre_key) < 3:
                    continue
                score = _similarity(cand_lower, genre_key)
                if score >= MIN_MATCH_CONFIDENCE:
                    scored.append(EntityCandidate(
                        type="genre",
                        value=genre_key,
                        match_confidence=score,
                        source="fuzzy_genre",
                    ))

            if not scored:
                return Entity.unresolved(candidate, match_confidence=0.0)

            # 6. Сортировка, AMBIGUOUS только при близких кандидатах.
            scored.sort(key=lambda c: c.match_confidence, reverse=True)
            top = scored[0]
            if len(scored) >= 2:
                second = scored[1]
                gap = top.match_confidence - second.match_confidence
                # Один и тот же value с разными источниками — не AMBIGUOUS.
                same_value = top.value == second.value
                if gap < AMBIGUITY_GAP and not same_value:
                    # Ограничим 5 кандидатами — больше в вопросе не влезет.
                    return Entity.ambiguous(scored[:5])

            # Один лидер
            return Entity.found(top.type, top.value, top.match_confidence, top.source)

        except Exception as e:
            log(f"entity.music.match: {e}", tag="BRAIN", level="WARN")
            return Entity.unresolved(candidate, match_confidence=0.0)


# ── Синглтон (экстрактор без состояния, но пусть будет один) ─
_extractor: MusicEntityExtractor | None = None


def get() -> MusicEntityExtractor:
    global _extractor
    if _extractor is None:
        _extractor = MusicEntityExtractor()
    return _extractor