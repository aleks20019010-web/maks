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
# Раскрытие плейсхолдеров:
#   {genre}          → все значения из config/genre_aliases.json
#   {name}           → мою волну, твою музыку, мою музыку, твою волну
#   {greeting}       → 7 вариантов приветствия
#   {identity_about} → манифест Макса
#   {identity_can}   → что умеет
#   {artist}         → топ-150 артистов из data/memory.db (weight > 0)
#   {track}          → топ-500 треков из data/memory.db (weight > 0)
#   {title}          → топ-500 треков из data/memory.db (weight > 0)
#   {device}, {query}, {temp}, {sky}, {wind}, {advice} — динамичны, НЕ прогреваются
#
# Защита:
#   - фразы, где {artist} + {track}/{title} вместе, ПРОПУСКАЮТСЯ
#   - composer.json ИСКЛЮЧЁН (правила, не фразы)
#   - треки с названиями < 3 символов или без букв — ПРОПУСКАЮТСЯ

import os
import sys
import glob
import json
import time
import argparse
import re
import sqlite3
from itertools import product

BASE_DIR = r'D:\Maks'
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from shared import config as cfg
from workers.tts.engine import YandexEngine
from workers.tts import cache as tts_cache

CACHE_DIR = cfg.CACHE_DIR
RESPONSES_DIR = os.path.join(BASE_DIR, 'responses')
FAILED_FILE = os.path.join(CACHE_DIR, 'failed.json')
LOCK_FILE = os.path.join(cfg.DATA_DIR, 'warmup_tts.lock')

os.makedirs(CACHE_DIR, exist_ok=True)

PAUSE_BETWEEN_PHRASES = 0.5

TOP_ARTISTS_LIMIT = 100
TOP_TRACKS_LIMIT = 50

PLACEHOLDER_RE = re.compile(r'\{[^}]+\}')

# Файлы, которые НЕ трогаем (composer.json — правила, не фразы)
SKIP_FILES = {"composer.json"}

# Минимальная длина названия трека и требование хотя бы одной буквы
MIN_TRACK_LEN = 3
LETTER_RE = re.compile(r'[а-яА-Яa-zA-Z]')

GENRE_NAMES = {}
try:
    _genre_aliases_path = os.path.join(BASE_DIR, 'config', 'genre_aliases.json')
    if os.path.exists(_genre_aliases_path):
        with open(_genre_aliases_path, 'r', encoding='utf-8') as f:
            _aliases = json.load(f)
        _seen = set()
        for _alias, _station in _aliases.items():
            if _station not in _seen:
                GENRE_NAMES[_station] = _alias
                _seen.add(_station)
except Exception as _e:
    print(f"   ⚠ genre_names: {_e}")

PLAYLIST_NAMES = ["мою волну", "твою музыку", "мою музыку", "твою волну"]

GREETING_VALUES = [
    "Доброе утро", "Добрый день", "Добрый вечер", "Доброй ночи",
    "Доброго утра", "Доброго дня", "Доброго вечера",
]


# ────────────────────────────────────────────────────────────
# LOCK (свой, не shared.lock)
# ────────────────────────────────────────────────────────────
def _acquire_lock():
    """Вернуть (True, pid), если lock взят. (False, pid) — уже запущен."""
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


# ────────────────────────────────────────────────────────────
# ДАННЫЕ
# ────────────────────────────────────────────────────────────
def _identity_values():
    try:
        from personality_core import identity as _identity
        ident = _identity.get(BASE_DIR)
        about = ident.about()
        can = ident.can()
        return {
            "identity_about": lambda: [about],
            "identity_can": lambda: [can],
        }
    except Exception as e:
        print(f"   ⚠ identity_values: {e}")
        return {}


def _top_artists(limit=TOP_ARTISTS_LIMIT):
    try:
        db_path = cfg.MEMORY_DB
        if not os.path.exists(db_path):
            print(f"   ⚠ top_artists: БД не найдена {db_path}")
            return []
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("""
            SELECT artist FROM artist_stats
            WHERE weight > 0
            ORDER BY weight DESC
            LIMIT ?
        """, (limit,))
        rows = cur.fetchall()
        conn.close()
        return [r[0] for r in rows if r[0]]
    except Exception as e:
        print(f"   ⚠ top_artists: {e}")
        return []


def _top_tracks(limit=TOP_TRACKS_LIMIT):
    """Топ треков с фильтром: длина >= MIN_TRACK_LEN, есть хотя бы одна буква."""
    try:
        db_path = cfg.MEMORY_DB
        if not os.path.exists(db_path):
            print(f"   ⚠ top_tracks: БД не найдена {db_path}")
            return []
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("""
            SELECT title FROM track_stats
            WHERE weight > 0
            ORDER BY weight DESC
            LIMIT ?
        """, (limit * 2,))  # берём с запасом, отфильтруем
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


TOP_ARTISTS = _top_artists(limit=TOP_ARTISTS_LIMIT)
TOP_TRACKS = _top_tracks(limit=TOP_TRACKS_LIMIT)


PLACEHOLDER_EXPANDERS = {
    "genre": lambda: list(GENRE_NAMES.values()),
    "name": lambda: PLAYLIST_NAMES,
    "greeting": lambda: GREETING_VALUES,
    "artist": lambda: TOP_ARTISTS,
    "track": lambda: TOP_TRACKS,
    "title": lambda: TOP_TRACKS,
    **_identity_values(),
}


def is_real_phrase(s):
    s = s.strip()
    if not s or len(s) < 2:
        return False
    if PLACEHOLDER_RE.search(s):
        return False
    if re.fullmatch(r'[a-zA-Z_]+', s):
        return False
    if re.fullmatch(r'[\d\W]+', s):
        return False
    return True


def collect_phrases(node, out_set):
    if isinstance(node, str):
        if node.strip() and len(node.strip()) >= 2:
            out_set.add((node.strip(), None, None, None))
    elif isinstance(node, list):
        for item in node:
            collect_phrases(item, out_set)
    elif isinstance(node, dict):
        if 'text' in node:
            text = node.get('text', '')
            rate = node.get('rate')
            pitch = node.get('pitch')
            emotion = node.get('emotion')
            if text and len(text.strip()) >= 2:
                out_set.add((text.strip(), rate, pitch, emotion))
            return
        for v in node.values():
            collect_phrases(v, out_set)


def load_all_phrases():
    phrases = set()
    all_files = sorted(glob.glob(os.path.join(RESPONSES_DIR, '*.json')))
    files = [f for f in all_files if os.path.basename(f) not in SKIP_FILES]

    if not files:
        print(f"❌ Не нашёл JSON в {RESPONSES_DIR}")
        return []

    skipped = [os.path.basename(f) for f in all_files if os.path.basename(f) in SKIP_FILES]
    if skipped:
        print(f"   ⏭ Пропущено файлов: {', '.join(skipped)}")

    for path in files:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            before = len(phrases)
            collect_phrases(data, phrases)
            print(f"   📄 {os.path.basename(path)}: +{len(phrases)-before} фраз")
        except Exception as e:
            print(f"   ⚠ {os.path.basename(path)}: {e}")
    return sorted(phrases, key=lambda x: x[0])


def expand_phrases(phrases):
    expanded = set()
    skipped_unknown = 0
    skipped_multi = 0

    for (text, rate, pitch, emotion) in phrases:
        found = re.findall(r'\{(\w+)\}', text)

        if not found:
            if is_real_phrase(text):
                expanded.add((text, rate, pitch, emotion))
            continue

        has_artist = "artist" in found
        has_track = "track" in found or "title" in found
        if has_artist and has_track:
            skipped_multi += 1
            continue

        if any(p not in PLACEHOLDER_EXPANDERS for p in found):
            skipped_unknown += 1
            continue

        values_lists = [PLACEHOLDER_EXPANDERS[p]() for p in found]

        for combo in product(*values_lists):
            new_text = text
            for ph, val in zip(found, combo):
                new_text = new_text.replace('{' + ph + '}', val)
            if is_real_phrase(new_text):
                expanded.add((new_text, rate, pitch, emotion))

    if skipped_unknown:
        print(f"   ⏭ Пропущено (бесконечные плейсхолдеры): {skipped_unknown}")
    if skipped_multi:
        print(f"   ⏭ Пропущено (artist + track/title вместе): {skipped_multi}")

    return sorted(expanded, key=lambda x: x[0])


def main():
    ok, pid = _acquire_lock()
    if not ok:
        print(f"❌ Прогрев уже запущен (PID {pid}). Выход.")
        sys.exit(1)
    print(f"🔒 lock взят (PID {os.getpid()})")

    parser = argparse.ArgumentParser()
    parser.add_argument('--clear', action='store_true', help='очистить кэш')
    parser.add_argument('--force', action='store_true', help='перегенерировать всё')
    parser.add_argument('--dry', action='store_true', help='показать список, без синтеза')
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

    print(f"📚 GENRE_NAMES: {len(GENRE_NAMES)} жанров")
    if GENRE_NAMES:
        _sample = list(GENRE_NAMES.values())[:5]
        print(f"   Первые 5: {_sample}")

    print(f"🎤 TOP_ARTISTS: {len(TOP_ARTISTS)} (лимит {TOP_ARTISTS_LIMIT})")
    if TOP_ARTISTS:
        print(f"   Первые 5: {TOP_ARTISTS[:5]}")

    print(f"🎵 TOP_TRACKS: {len(TOP_TRACKS)} (лимит {TOP_TRACKS_LIMIT}, фильтр: ≥{MIN_TRACK_LEN} символов, есть буквы)")
    if TOP_TRACKS:
        print(f"   Первые 5: {TOP_TRACKS[:5]}")
    print()

    print("Читаю фразы...")
    phrases = load_all_phrases()
    if not phrases:
        _release_lock()
        return

    print(f"\nРаскрываю известные плейсхолдеры...")
    if not GENRE_NAMES:
        print("   ⚠ GENRE_NAMES не загружен — {genre} не будет раскрыт")
    before = len(phrases)
    phrases = expand_phrases(phrases)
    print(f"   Было: {before}, стало: {len(phrases)} (+{len(phrases) - before})\n")

    if not phrases:
        print("❌ После раскрытия фраз нет")
        _release_lock()
        return

    if args.dry:
        for i, (text, rate, pitch, emotion) in enumerate(phrases, 1):
            extras = []
            if rate:
                extras.append(f"rate={rate}")
            if pitch:
                extras.append(f"pitch={pitch}")
            if emotion:
                extras.append(f"emotion={emotion}")
            ex = f"  [{', '.join(extras)}]" if extras else ""
            print(f"[{i}/{len(phrases)}] {text[:70]}{ex}")
        print("\n(dry-run, синтез не выполнялся)")
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
    print(f"Готово за {dt:.1f} сек")
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