# workers/music/aliases.py
# Алиасы артистов: load / save / remember / resolve + char-3-grams (fuzzy).
# Хранится в config/aliases.json.
#
# БЭКАП (новое): save_aliases перед перезаписью делает .bak копию.
#   Причина: aliases.json в .gitignore (данные, не код), и его легко
#   потерять (git clean, случайная перезапись, сбой). .bak — страховка:
#   если файл обнулится, копия останется, и её легко восстановить:
#   copy config/aliases.json.bak config/aliases.json

import os
import json
import shutil

from shared import config as cfg
from shared.audio_cache import normalize
from workers.music import state
from workers.music._shared import STOP_WORDS


# ────────────────────────────────────────────────────────────
# CHAR-N-GRAMS (fuzzy-поиск по алиасам)
# ────────────────────────────────────────────────────────────
def _char_ngrams(s, n=3):
    """Множество n-грамм символов (с пробелами по краям)."""
    s = " " + s.lower().strip() + " "
    if len(s) < n:
        return {s}
    return set(s[i:i + n] for i in range(len(s) - n + 1))


def _similarity(a, b, n=3):
    """Jaccard-сходство по char-n-grams. 0.0 – 1.0."""
    if not a or not b:
        return 0.0
    ga, gb = _char_ngrams(a, n), _char_ngrams(b, n)
    if not ga or not gb:
        return 0.0
    inter = len(ga & gb)
    union = len(ga | gb)
    return inter / union if union else 0.0


def find_similar_alias(query, threshold=0.4):
    """Найти похожий ключ алиаса по char-3-grams."""
    q = normalize(query)
    if not q or len(q) < 3:
        return None

    aliases = load_aliases()
    if not aliases:
        return None

    best_key = None
    best_score = 0.0
    for key in aliases.keys():
        if key in STOP_WORDS:
            continue
        if len(key) < 3:
            continue
        score = _similarity(q, key)
        if score > best_score:
            best_key = key
            best_score = score

    if best_score >= threshold and best_key is not None:
        print(f"   [fuzzy] «{q}» ~ «{best_key}» ({best_score:.2f})")
        return best_key
    return None


# ────────────────────────────────────────────────────────────
# ОСНОВНОЕ
# ────────────────────────────────────────────────────────────
def load_aliases(force_refresh=False):
    with state._aliases_lock:
        if state._aliases_cache is not None and not force_refresh:
            return state._aliases_cache
        if not os.path.exists(cfg.ALIASES_FILE):
            state._aliases_cache = {}
            return state._aliases_cache
        try:
            with open(cfg.ALIASES_FILE, 'r', encoding='utf-8') as f:
                state._aliases_cache = json.load(f)
        except Exception:
            state._aliases_cache = {}
        return state._aliases_cache


def save_aliases(aliases):
    """Сохранить алиасы. Перед перезаписью — .bak копия (страховка)."""
    try:
        # Бэкап перед перезаписью (на случай потери)
        if os.path.exists(cfg.ALIASES_FILE):
            try:
                shutil.copy2(str(cfg.ALIASES_FILE), str(cfg.ALIASES_FILE) + ".bak")
            except Exception as e:
                print(f"⚠ Ошибка бэкапа алиасов: {e}")
        with open(cfg.ALIASES_FILE, 'w', encoding='utf-8') as f:
            json.dump(aliases, f, ensure_ascii=False, indent=2)
        with state._aliases_lock:
            state._aliases_cache = aliases
    except Exception as e:
        print(f"⚠ Ошибка сохранения алиасов: {e}")


def remember_alias(user_query, artist_id, artist_name):
    q = normalize(user_query)
    if not q or not artist_id:
        return
    if len(q.split()) > 2:
        return
    if q in STOP_WORDS:
        return
    aliases = dict(load_aliases())
    entry = {"artist_id": artist_id, "artist_name": artist_name}
    if aliases.get(q) == entry:
        return
    aliases[q] = entry
    save_aliases(aliases)
    print(f"   💡 запомнил: «{q}» → {artist_name}")


def resolve_alias(query):
    """Точный поиск алиаса. Если не нашли — None (см. find_similar_alias)."""
    aliases = load_aliases()
    q = normalize(query)
    if q not in aliases:
        return None
    entry = aliases[q]
    if isinstance(entry, dict) and "artist_id" in entry:
        if state.client is None:
            return None
        try:
            artists = state.client.artists([entry["artist_id"]])
            if artists:
                print(f"   алиас: «{q}» → {artists[0].name}")
                return artists[0]
        except Exception as e:
            print(f"   ошибка загрузки алиаса: {e}")
    return None