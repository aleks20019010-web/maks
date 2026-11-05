# warmup_tts.py
# Прогрев TTS-кэша Макса. Работает с YandexEngine (workers/tts/engine.py).
#
# Запуск:  python warmup_tts.py
# Флаги:
#   --clear   очистить кэш перед прогревом
#   --force   перегенерировать всё, даже если уже в кэше
#   --dry     только показать, что будет синтезироваться (без сети)
#
# Lock-файл: второй экземпляр не запускается (data/warmup_tts.lock).
#
# Стратегия (конечные слоты composer + конечная динамика):
#   - opening/closing: все 13 (не режем — конечны)
#   - {artist}  → топ-3 из БД (хвост — Yandex в реальном времени)
#   - {track}/{title} → 0 (Yandex всегда — треки бесконечны)
#   - {genre}   → топ-3 жанра
#   - {name}    → 4 плейлиста (PLAY_PLAYLIST) / 6 станций (PLAY_RADIO, конечны)
#   - {mood}    → 5 настроений
#   - {value}   → sample {10,20,...,100}
#   - weather   → ПРОПУСК (бесконечная динамика: temp/sky/wind/advice)
#   - {device}, {query}, {answer}, {due}, {when}, {count}, {items}, {condition}
#               → ПРОПУСК (динамика)
#
# Склейка opening+core+closing — через _join_parts из brain.composer.
# rate/pitch/emotion берутся от core.
#
# FIX (warmup v5):
#   - _infer_slot по ПРЕФИКСУ ключа (OPENING_* → opening, CLOSING_* → closing)
#   - {name} разделён: PLAY_RADIO → RADIO_NAMES (все), PLAY_PLAYLIST → PLAYLIST_NAMES
#   - {mood}, {value} — не динамические (конечные)
#   - {artist} → 3, {genre} → 3, {track}/{title} → 0 (Yandex)
#   - NOW_PLAYING, DISCOVERY_*: {artist}+{title} → 0 (трек 0, Yandex)
#   - str(val) — value может быть int
#   - DEBUG-вывод слотов

import os
import sys
import glob
import json
import time
import argparse
import re
import sqlite3
from itertools import product
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from shared import config as cfg
from workers.tts.engine import YandexEngine
from workers.tts import cache as tts_cache

try:
    from brain.composer import _join_parts
except Exception as e:
    print(f"⚠ не удалось импортировать _join_parts: {e}")
    def _join_parts(parts):
        return " ".join(p.strip() for p in parts if p and p.strip())

CACHE_DIR = cfg.CACHE_DIR
RESPONSES_DIR = os.path.join(BASE_DIR, 'responses')
FAILED_FILE = os.path.join(CACHE_DIR, 'failed.json')
LOCK_FILE = os.path.join(cfg.DATA_DIR, 'warmup_tts.lock')

os.makedirs(CACHE_DIR, exist_ok=True)

PAUSE_BETWEEN_PHRASES = 0.5

# ── Стратегия ─────────────────────────────────────────────
TOP_OPENING_CLOSING = 13     # все (конечны)
TOP_ARTISTS_LIMIT = 3        # 3 из топа (хвост — Yandex)
TOP_TRACKS_LIMIT = 0         # 0 — треки бесконечны (Yandex)
TOP_GENRES_LIMIT = 3         # 3 жанра
VOLUME_SAMPLE = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]

SLOT_RE = re.compile(r'\{(\w+)\}')

SKIP_FILES = {"composer.json"}

DYNAMIC_PLACEHOLDERS = {
    "temp", "sky", "wind", "advice", "device", "query",
    "answer", "due", "when", "count", "items", "condition",
}

ALLOW_ARTIST_TRACK_KEYS = {
    "NOW_PLAYING", "DISCOVERY_NEW", "DISCOVERY_RETRY",
    "DISCOVERY_REJECTED", "BLACKLIST_RETRY_REMOVED",
    "BLACKLIST_RETRY_CONFIRMED",
}

MIN_TRACK_LEN = 3
LETTER_RE = re.compile(r'[а-яА-Яa-zA-Z]')

# ── Данные ───────────────────────────────────────────────
GENRE_NAMES = {}
try:
    _p = os.path.join(BASE_DIR, 'config', 'genre_aliases.json')
    if os.path.exists(_p):
        with open(_p, 'r', encoding='utf-8') as f:
            _aliases = json.load(f)
        _seen = set()
        for _alias, _station in _aliases.items():
            if _station not in _seen:
                GENRE_NAMES[_station] = _alias
                _seen.add(_station)
except Exception as _e:
    print(f"   ⚠ genre_names: {_e}")

PLAYLIST_NAMES = ["мою волну", "твою музыку", "мою музыку", "твою волну"]
MOOD_NAMES = ["спокойное", "бодрое", "весёлое", "грустное", "агрессивное"]

GREETING_VALUES = [
    "Доброе утро", "Добрый день", "Добрый вечер", "Доброй ночи",
    "Доброго утра", "Доброго дня", "Доброго вечера",
]


def _identity_values():
    try:
        from personality_core import identity as _identity
        ident = _identity.get(BASE_DIR)
        return {
            "identity_about": lambda: [ident.about()],
            "identity_can": lambda: [ident.can()],
        }
    except Exception as e:
        print(f"   ⚠ identity_values: {e}")
        return {}


def _top_artists(limit):
    if limit <= 0:
        return []
    try:
        db_path = cfg.MEMORY_DB
        if not os.path.exists(db_path):
            return []
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT artist FROM artist_stats WHERE weight > 0 "
                    "ORDER BY weight DESC LIMIT ?", (limit,))
        rows = cur.fetchall()
        conn.close()
        return [r[0] for r in rows if r[0]]
    except Exception as e:
        print(f"   ⚠ top_artists: {e}")
        return []


def _top_tracks(limit):
    if limit <= 0:
        return []
    try:
        db_path = cfg.MEMORY_DB
        if not os.path.exists(db_path):
            return []
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT title FROM track_stats WHERE weight > 0 "
                    "ORDER BY weight DESC LIMIT ?", (limit * 2,))
        rows = cur.fetchall()
        conn.close()
        result = []
        for r in rows:
            title = (r[0] or "").strip()
            if len(title) < MIN_TRACK_LEN:
                continue
            if not LETTER_RE.search(title):
                continue
            result.append(title)
            if len(result) >= limit:
                break
        return result
    except Exception as e:
        print(f"   ⚠ top_tracks: {e}")
        return []


def _load_radio_names():
    try:
        p = os.path.join(BASE_DIR, 'config', 'radio_stations.json')
        if os.path.exists(p):
            with open(p, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                return list(data.keys())
            if isinstance(data, list):
                return [x.get("name") for x in data if x.get("name")]
    except Exception as e:
        print(f"   ⚠ radio_names: {e}")
    return []


TOP_ARTISTS = _top_artists(TOP_ARTISTS_LIMIT)
TOP_TRACKS = _top_tracks(TOP_TRACKS_LIMIT)
RADIO_NAMES = _load_radio_names()
GENRE_SAMPLE = list(GENRE_NAMES.values())[:TOP_GENRES_LIMIT]

PLACEHOLDER_EXPANDERS = {
    "genre": lambda: GENRE_SAMPLE,
    "name": lambda: PLAYLIST_NAMES,
    "greeting": lambda: GREETING_VALUES,
    "artist": lambda: TOP_ARTISTS,
    "track": lambda: TOP_TRACKS,
    "title": lambda: TOP_TRACKS,
    "radio": lambda: RADIO_NAMES,
    "station": lambda: RADIO_NAMES,
    "mood": lambda: MOOD_NAMES,
    "value": lambda: VOLUME_SAMPLE,
    **_identity_values(),
}


def _expanders_for(phrase_set: str) -> dict:
    ex = dict(PLACEHOLDER_EXPANDERS)
    if phrase_set == "PLAY_RADIO":
        ex["name"] = lambda: RADIO_NAMES
    elif phrase_set == "PLAY_PLAYLIST":
        ex["name"] = lambda: PLAYLIST_NAMES
    return ex


# ── Slot inference (по префиксу) ─────────────────────────
def _infer_slot(key: str, item: dict) -> str:
    if key.startswith("OPENING_"):
        return "opening"
    if key.startswith("CLOSING_"):
        return "closing"
    return item.get("slot", "core")


# ── Lock ─────────────────────────────────────────────────
def _acquire_lock():
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, 'r', encoding='utf-8') as f:
                pid = int(f.read().strip())
            import psutil
            if psutil.pid_exists(pid):
                return False, pid
        except Exception:
            pass
    with open(LOCK_FILE, 'w', encoding='utf-8') as f:
        f.write(str(os.getpid()))
    return True, os.getpid()


def _release_lock():
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except Exception:
        pass


# ── Загрузка responses с slot ────────────────────────────
def load_responses_with_slots():
    result = {}
    all_files = sorted(glob.glob(os.path.join(RESPONSES_DIR, '*.json')))
    files = [f for f in all_files if os.path.basename(f) not in SKIP_FILES]

    for path in files:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict):
                continue
            for key, items in data.items():
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    text = (item.get("text") or "").strip()
                    if not text or len(text) < 2:
                        continue
                    slot = _infer_slot(key, item)
                    result.setdefault(key, {}).setdefault(slot, []).append(item)
        except Exception as e:
            print(f"   ⚠ {os.path.basename(path)}: {e}")

    for key in ("OPENING_MUSIC", "CLOSING_MUSIC",
                "PLAY_ARTIST", "PLAY_GENRE", "PLAY_TRACK",
                "PLAY_PLAYLIST", "PLAY_RADIO"):
        d = result.get(key, {})
        print(f"   DEBUG {key}: opening={len(d.get('opening', []))}, "
              f"core={len(d.get('core', []))}, "
              f"closing={len(d.get('closing', []))}")

    return result


def load_composer_rules():
    path = os.path.join(RESPONSES_DIR, 'composer.json')
    try:
        with open(path, 'r', encoding='utf-8-sig') as f:
            return json.load(f)
    except Exception as e:
        print(f"   ⚠ composer.json: {e}")
        return {}


def _top_n_weight(items, n):
    if n <= 0:
        return []
    if len(items) <= n:
        return items
    def _w(x):
        try:
            return float(x.get("weight", 1.0))
        except (TypeError, ValueError):
            return 1.0
    return sorted(items, key=_w, reverse=True)[:n]


def _expand_item(item, phrase_set=""):
    text = item.get("text", "")
    found = SLOT_RE.findall(text)
    if not found:
        return [item]

    if any(p in DYNAMIC_PLACEHOLDERS for p in found):
        return []

    allow_at = phrase_set in ALLOW_ARTIST_TRACK_KEYS
    if ("artist" in found and ("track" in found or "title" in found)) and not allow_at:
        return []

    expanders = _expanders_for(phrase_set)
    if any(p not in expanders for p in found):
        return []

    values_lists = [expanders[p]() for p in found]
    # Пустой пул (например, {track} при TOP_TRACKS_LIMIT=0) — пропуск
    if any(not v for v in values_lists):
        return []

    out = []
    for combo in product(*values_lists):
        new_text = text
        for ph, val in zip(found, combo):
            new_text = new_text.replace('{' + ph + '}', str(val))
        new_item = dict(item)
        new_item["text"] = new_text
        out.append(new_item)
    return out


def collect_phrases_for_composer(responses, composer_rules):
    out = set()
    skipped_weather = 0
    skipped_dynamic = 0
    skipped_pool = 0

    for phrase_set, rule in composer_rules.items():
        if not isinstance(rule, dict):
            continue
        if phrase_set.startswith("WEATHER_"):
            skipped_weather += 1
            continue

        slots = [(s, rule[s]) for s in ("opening", "core", "closing") if rule.get(s)]
        if not slots:
            continue

        pools = []
        for slot, ps in slots:
            items = responses.get(ps, {}).get(slot, [])
            if not items:
                pools.append((slot, []))
                continue
            if slot in ("opening", "closing") and TOP_OPENING_CLOSING > 0:
                items = _top_n_weight(items, TOP_OPENING_CLOSING)
            pools.append((slot, items))

        if any(not items for _, items in pools):
            skipped_pool += 1
            continue

        combo_lists = [items for _, items in pools]
        for combo in product(*combo_lists):
            expanded_combo = []
            ok = True
            for item in combo:
                expanded = _expand_item(item, phrase_set)
                if not expanded:
                    if any(p in DYNAMIC_PLACEHOLDERS
                           for p in SLOT_RE.findall(item.get("text", ""))):
                        skipped_dynamic += 1
                    ok = False
                    break
                expanded_combo.append(expanded)
            if not ok:
                continue

            for chosen in product(*expanded_combo):
                texts = [c.get("text", "") for c in chosen if c.get("text")]
                if not texts:
                    continue
                full_text = _join_parts(texts)
                if not full_text or len(full_text) < 2:
                    continue
                core = None
                for (slot, _), c in zip(pools, chosen):
                    if slot == "core":
                        core = c
                        break
                core = core or chosen[0]
                out.add((full_text, core.get("rate"),
                         core.get("pitch"), core.get("emotion")))

    if skipped_weather:
        print(f"   ⏭ Пропущено weather-ключей: {skipped_weather}")
    if skipped_pool:
        print(f"   ⏭ Пропущено (пул opening/core/closing): {skipped_pool}")
    if skipped_dynamic:
        print(f"   ⏭ Пропущено склеек с динамикой: {skipped_dynamic}")
    return out


def collect_core_only(responses, composer_rules):
    out = set()
    skipped_dynamic = 0

    for key, slot_dict in responses.items():
        if key.startswith("WEATHER_"):
            continue

        rule = composer_rules.get(key)
        if isinstance(rule, dict) and (rule.get("opening") or rule.get("closing")):
            continue

        items = slot_dict.get("core", [])
        if not items:
            for slot_items in slot_dict.values():
                items.extend(slot_items)

        for item in items:
            expanded = _expand_item(item, key)
            if not expanded:
                if any(p in DYNAMIC_PLACEHOLDERS
                       for p in SLOT_RE.findall(item.get("text", ""))):
                    skipped_dynamic += 1
                continue
            for e in expanded:
                text = e.get("text", "")
                if not text or len(text) < 2:
                    continue
                out.add((text, e.get("rate"), e.get("pitch"), e.get("emotion")))

    if skipped_dynamic:
        print(f"   ⏭ Пропущено core-фраз с динамикой: {skipped_dynamic}")
    return out


def main():
    ok, pid = _acquire_lock()
    if not ok:
        print(f"❌ Прогрев уже запущен (PID {pid}). Выход.")
        sys.exit(1)
    print(f"🔒 lock взят (PID {os.getpid()})")

    parser = argparse.ArgumentParser()
    parser.add_argument('--clear', action='store_true')
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--dry', action='store_true')
    args = parser.parse_args()

    if args.clear:
        removed = 0
        for f in glob.glob(os.path.join(CACHE_DIR, '*.mp3')):
            try:
                os.remove(f)
                removed += 1
            except Exception:
                pass
        if os.path.exists(FAILED_FILE):
            try:
                os.remove(FAILED_FILE)
            except Exception:
                pass
        print(f"🧹 Очищено: {removed} файлов\n")

    engine = YandexEngine()
    print(f"📁 Кэш: {CACHE_DIR}")
    print(f"🎙 Движок: {engine.name}")
    print(f"🎙 Отпечаток: {engine.cache_fingerprint()}\n")

    print(f"📚 GENRE_SAMPLE: {len(GENRE_SAMPLE)}/{len(GENRE_NAMES)} (лимит {TOP_GENRES_LIMIT})")
    print(f"🎤 TOP_ARTISTS: {len(TOP_ARTISTS)} (лимит {TOP_ARTISTS_LIMIT})")
    if TOP_ARTISTS:
        print(f"   {TOP_ARTISTS}")
    print(f"🎵 TOP_TRACKS: {len(TOP_TRACKS)} (лимит {TOP_TRACKS_LIMIT} — Yandex)")
    print(f"📻 RADIO_NAMES: {len(RADIO_NAMES)}")
    if RADIO_NAMES:
        print(f"   {RADIO_NAMES}")
    print(f"🎭 MOOD_NAMES: {len(MOOD_NAMES)}")
    print(f"🎚 VOLUME_SAMPLE: {VOLUME_SAMPLE}")
    print(f"🎬 OPENING/CLOSING: {TOP_OPENING_CLOSING} (все)\n")

    print("Читаю responses с slot...")
    responses = load_responses_with_slots()
    print(f"   Ключей: {len(responses)}, "
          f"слотов: {sum(len(v) for v in responses.values())}")

    print("Читаю composer.json...")
    composer_rules = load_composer_rules()
    print(f"   Правил: {len(composer_rules)}\n")

    print("Собираю фразы (composer + core-only)...")
    phrases_a = collect_phrases_for_composer(responses, composer_rules)
    print(f"   Composer (склейка): {len(phrases_a)}")
    phrases_b = collect_core_only(responses, composer_rules)
    print(f"   Core-only: {len(phrases_b)}")
    phrases = sorted(phrases_a | phrases_b, key=lambda x: x[0])
    print(f"   Всего: {len(phrases)}\n")

    if not phrases:
        print("❌ Фраз нет")
        _release_lock()
        return

    if args.dry:
        for i, (text, rate, pitch, emotion) in enumerate(phrases, 1):
            extras = []
            if rate: extras.append(f"rate={rate}")
            if pitch: extras.append(f"pitch={pitch}")
            if emotion: extras.append(f"emotion={emotion}")
            ex = f"  [{', '.join(extras)}]" if extras else ""
            print(f"[{i}/{len(phrases)}] {text[:70]}{ex}")
        print(f"\n(dry-run, {len(phrases)} фраз, ~{len(phrases)*3.6/3600:.1f} часов)")
        _release_lock()
        return

    fresh = 0
    cached = 0
    failed = 0
    t0 = time.time()

    for i, (text, rate, pitch, emotion) in enumerate(phrases, 1):
        out_path = os.path.join(CACHE_DIR, f"_warmup_{i}.mp3")
        if not args.force and tts_cache.cache_hit(engine, text, out_path, rate, pitch, emotion):
            cached += 1
            if i % 100 == 0 or cached <= 3:
                print(f"[{i}/{len(phrases)}] ✓ из кэша: {text[:60]}")
            continue

        ok = False
        for attempt in range(3):
            try:
                ok = engine.synth(text, out_path, rate=rate, pitch=pitch, emotion=emotion)
                if ok and os.path.exists(out_path) and os.path.getsize(out_path) > 1024:
                    break
                ok = False
            except Exception as e:
                print(f"      ↻ попытка {attempt+2}/3: {e}")
            time.sleep(1.0)

        if ok:
            tts_cache.cache_save(engine, text, out_path, rate, pitch, emotion)
            fresh += 1
            size_kb = os.path.getsize(out_path) // 1024
            print(f"[{i}/{len(phrases)}] 🔊 {size_kb} КБ: {text[:60]}")
            if PAUSE_BETWEEN_PHRASES > 0:
                time.sleep(PAUSE_BETWEEN_PHRASES)
        else:
            failed += 1
            print(f"[{i}/{len(phrases)}] ✗ ОШИБКА: {text[:60]}")

        try:
            if os.path.exists(out_path):
                os.remove(out_path)
        except Exception:
            pass

    dt = time.time() - t0
    print(f"\n{'='*50}")
    print(f"Готово за {dt/3600:.1f} ч ({dt:.0f} сек)")
    print(f"  Новых:      {fresh}")
    print(f"  Из кэша:    {cached}")
    print(f"  Ошибок:     {failed}")
    print(f"  Всего:      {len(phrases)}")
    print(f"{'='*50}")

    _release_lock()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n⏹ Остановлено по Ctrl+C")
        _release_lock()
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        _release_lock()
        sys.exit(1)