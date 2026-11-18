# brain/nlu/entity/music.py
# Извлечение и поиск музыкальных сущностей.
#
# Порядок поиска в match():
#   0. local_liked («мою музыку» / «мои лайки» / «мне нравится»)
#   1. radio (точные ключи/алиасы + fuzzy по radio_aliases)
#   2. mood_aliases (точное + падежи)
#   3. local_index.json (точные + падежи по artist / phon_artist / title)
#   4. aliases.json (точные + падежи)
#   5. genre_aliases.json (точные + падежи)
#   6. fuzzy по mood + local_artists + aliases + genres (порог 0.80 для артистов)
#   7. fallback в play_search (локальный поиск → Яндекс.Музыка)

import json
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
MIN_MATCH_CONFIDENCE = 0.72    # для mood/genre fuzzy
ARTIST_FUZZY_THRESHOLD = 0.80  # для артистов (защита от «нэнси» → «дэнс» 0.67)
AMBIGUITY_GAP = 0.15           # разница между топ-1 и топ-2 < gap → AMBIGUOUS
RADIO_FUZZY_THRESHOLD = 0.80   # fuzzy по radio_aliases


# ── Триггеры (вычитаются из текста перед кандидатом) ─────────
from shared.music_words import (
    PLAY_TRIGGERS, NOISE_WORDS, ARTIST_HINTS, TRACK_HINTS,
)


# ── Загрузка каталогов (с отслеживанием mtime) ───────────────
_aliases_cache: dict = {}
_aliases_mtime: float = -1.0

_local_artists_cache: dict[str, str] = {}   # lower_key -> canonical artist name
_local_tracks_cache: dict[str, str] = {}    # lower_key -> "Artist Title"
_local_index_mtime: float = -1.0

_genres_cache: dict | None = None
_moods_cache: dict | None = None
_radios_cache: dict | None = None
_radio_aliases_cache: dict | None = None


def _load_aliases() -> dict:
    """config/aliases.json — {alias: {artist_id, artist_name}} с автообновлением."""
    global _aliases_cache, _aliases_mtime
    path = cfg.ALIASES_FILE
    if not os.path.exists(path):
        _aliases_cache = {}
        _aliases_mtime = -1.0
        return _aliases_cache
    try:
        mtime = os.path.getmtime(path)
        if mtime == _aliases_mtime:
            return _aliases_cache
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _aliases_cache = data if isinstance(data, dict) else {}
        _aliases_mtime = mtime
    except Exception as e:
        log(f"entity.music: aliases.json: {e}", tag="BRAIN", level="WARN")
    return _aliases_cache


def _load_local_index() -> tuple[dict[str, str], dict[str, str]]:
    """data/local_index.json — кэш локальных артистов и треков с автообновлением."""
    global _local_artists_cache, _local_tracks_cache, _local_index_mtime
    path = getattr(cfg, "LOCAL_INDEX_FILE", os.path.join(cfg.DATA_DIR, "local_index.json"))
    if not os.path.exists(path):
        _local_artists_cache = {}
        _local_tracks_cache = {}
        _local_index_mtime = -1.0
        return _local_artists_cache, _local_tracks_cache

    try:
        mtime = os.path.getmtime(path)
        if mtime == _local_index_mtime:
            return _local_artists_cache, _local_tracks_cache

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        tracks = data.get("tracks", []) if isinstance(data, dict) else (
            data if isinstance(data, list) else []
        )

        artists_map: dict[str, str] = {}
        tracks_map: dict[str, str] = {}

        for item in tracks:
            if not isinstance(item, dict):
                continue
            artist = (item.get("artist") or "").strip()
            phon_artist = (item.get("phon_artist") or "").strip().lower()
            title = (item.get("title") or "").strip()
            phon_title = (item.get("phon_title") or "").strip().lower()

            if artist:
                al = artist.lower()
                if al not in artists_map:
                    artists_map[al] = artist
                if phon_artist and phon_artist not in artists_map:
                    artists_map[phon_artist] = artist

            if title:
                tl = title.lower()
                full_q = f"{artist} {title}".strip() if artist else title
                if len(tl) >= 3 and tl not in tracks_map:
                    tracks_map[tl] = full_q
                if len(phon_title) >= 3 and phon_title not in tracks_map:
                    tracks_map[phon_title] = full_q

        _local_artists_cache = artists_map
        _local_tracks_cache = tracks_map
        _local_index_mtime = mtime
        log(f"entity.music: local_index обновлён (артистов: {len(artists_map)}, "
            f"треков: {len(tracks_map)})", tag="BRAIN", level="DEBUG")
    except Exception as e:
        log(f"entity.music: local_index.json: {e}", tag="BRAIN", level="WARN")

    return _local_artists_cache, _local_tracks_cache


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
        with open(path, "r", encoding="utf-8") as f:
            _genres_cache = json.load(f)
    except Exception as e:
        log(f"entity.music: genre_aliases.json: {e}", tag="BRAIN", level="WARN")
        _genres_cache = {}
    return _genres_cache


def _load_moods() -> dict:
    """config/mood_aliases.json — {слово: tag_метатега}."""
    global _moods_cache
    if _moods_cache is not None:
        return _moods_cache
    path = os.path.join(cfg.BASE_DIR, "config", "mood_aliases.json")
    if not os.path.exists(path):
        _moods_cache = {}
        return _moods_cache
    try:
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


def _normalize_case(word: str) -> str | list[str]:
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


# ── RADIO ────────────────────────────────────────────────────
def _resolve_radio(candidate: str) -> str | None:
    if not candidate:
        return None
    q = candidate.lower().strip()
    if not q:
        return None

    radios = _load_radios()
    aliases = _load_radio_aliases()

    if q in radios:
        return q
    key = aliases.get(q)
    if key and key in radios:
        return key
    for station_key in sorted(radios.keys(), key=len, reverse=True):
        if station_key in q:
            return station_key
    for alias in sorted(aliases.keys(), key=len, reverse=True):
        if alias in q:
            key = aliases[alias]
            if key in radios:
                return key
    return None


def _fuzzy_radio(candidate: str) -> str | None:
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
        if not candidate:
            return Entity.absent()
        if intent not in self.supported_intents:
            return Entity.absent()

        try:
            moods = _load_moods()
            aliases = _load_aliases()
            genres = _load_genres()
            local_artists, local_tracks = _load_local_index()

            cand_lower = candidate.lower().strip()

            # 0. «мою музыку» / «мои лайки» / «мне нравится»
            if any(phrase in cand_lower for phrase in ("мою музыку", "мои лайки", "мне нравится")):
                return Entity.found("local_liked", "local_liked", 1.0, "phrase")

            # 1. RADIO
            radio_key = _resolve_radio(cand_lower)
            if radio_key:
                return Entity.found("radio", radio_key, 1.0, "radio")
            if cand_lower == "радио":
                return Entity(
                    type="radio",
                    value=None,
                    state=EntityState.ABSENT,
                    match_confidence=1.0,
                    source="radio_no_station",
                )
            fuzzy_radio_key = _fuzzy_radio(cand_lower)
            if fuzzy_radio_key:
                log(f"entity.music: radio fuzzy «{cand_lower}» → "
                    f"«{fuzzy_radio_key}»", tag="BRAIN", level="DEBUG")
                return Entity.found("radio", fuzzy_radio_key, 0.85, "radio_fuzzy")

            # 2. MOOD — точное совпадение
            if cand_lower in moods:
                return Entity.found("mood", moods[cand_lower], 1.0, "mood_alias")

            # 3. LOCAL_INDEX (артисты) — точное совпадение
            if cand_lower in local_artists:
                return Entity.found("artist", local_artists[cand_lower], 1.0, "local_index")

            # 4. ALIASES — точное совпадение
            if cand_lower in aliases:
                entry = aliases[cand_lower]
                name = entry.get("artist_name") if isinstance(entry, dict) else None
                if name:
                    return Entity.found("artist", name, 1.0, "alias")

            # 5. LOCAL_INDEX (названия треков) — точное совпадение
            if cand_lower in local_tracks:
                return Entity.found("search", local_tracks[cand_lower], 0.98, "local_track")

            # 6. GENRE — точное совпадение
            if cand_lower in genres:
                return Entity.found("genre", cand_lower, 1.0, "genre_alias")

            # 7. Падежи (mood / local_artists / aliases / genre)
            for variant in _all_case_variants(cand_lower):
                if variant in moods:
                    return Entity.found("mood", moods[variant], 0.95, "case_mood")
                if variant in local_artists:
                    return Entity.found("artist", local_artists[variant], 0.95, "case_local_index")
                if variant in aliases:
                    entry = aliases[variant]
                    name = entry.get("artist_name") if isinstance(entry, dict) else None
                    if name:
                        return Entity.found("artist", name, 0.95, "case_alias")
                if variant in genres:
                    return Entity.found("genre", variant, 0.95, "case_genre")

            # 8. Fuzzy по mood + local_artists + aliases + genres
            scored: list[EntityCandidate] = []
            seen_artists: set[str] = set()

            for mood_key, mood_tag in moods.items():
                if len(mood_key) < 3:
                    continue
                score = _similarity(cand_lower, mood_key)
                if score >= MIN_MATCH_CONFIDENCE:
                    scored.append(EntityCandidate(
                        type="mood", value=mood_tag,
                        match_confidence=score, source="fuzzy_mood",
                    ))

            for loc_key, canonical_name in local_artists.items():
                if len(loc_key) < 3:
                    continue
                score = _similarity(cand_lower, loc_key)
                if score >= ARTIST_FUZZY_THRESHOLD:
                    scored.append(EntityCandidate(
                        type="artist", value=canonical_name,
                        match_confidence=score, source="fuzzy_local",
                    ))
                    seen_artists.add(canonical_name.lower())

            for alias_key, entry in aliases.items():
                if len(alias_key) < 3:
                    continue
                if alias_key in ("макс", "слушаю", "назад", "что"):
                    continue
                score = _similarity(cand_lower, alias_key)
                if score >= ARTIST_FUZZY_THRESHOLD:
                    name = entry.get("artist_name") if isinstance(entry, dict) else None
                    if not name or name.lower() in seen_artists:
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

            scored.sort(key=lambda c: c.match_confidence, reverse=True)

            # Если fuzzy нашёл артиста, но в запросе есть лишние слова (название трека)
            if scored:
                top = scored[0]
                if top.type == "artist":
                    artist_words = len(top.value.split())
                    cand_words = len(candidate.split())
                    if cand_words > artist_words:
                        return Entity.found(
                            "search", candidate, 0.9,
                            "play_search_artist_track",
                        )

            if not scored:
                # Если локально и в алиасах не нашли — отправляем в play_search
                # (music проверит локально и пойдёт искать в Яндекс.Музыку).
                if len(cand_lower) >= 2:
                    return Entity.found("search", candidate, 0.85, "play_search")
                return Entity.unresolved(candidate, match_confidence=0.0)

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