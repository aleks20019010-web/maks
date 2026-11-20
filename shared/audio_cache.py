"""
shared/audio_cache.py — кэш списка аудиофайлов и поиск по локальной библиотеке.

Кэш — локальный для модуля. Не зависит от state.
Brain и workers/music используют этот модуль — оба.

Поиск: basename + родительские папки (папка артиста тоже участвует).

FIX (2026-11-08): all_audio_files → из local_index.json (0.05 сек) вместо
  glob (8 сек на 3000 файлов). local.py строит JSON при первом запуске
  (8 сек) или обновляет в фоне (8 сек, не блокирует). Повторный запуск —
  JSON (0.05 сек). Первый «включи музыку» — 0.05 сек (не 8).

── FIX (2026-11-20): не кешировать пустой индекс навсегда ──────
  Раньше: files = [...]; _cache = files. Если _load_index вернул
  пустой tracks (битый JSON, MUSIC_DIR ещё пустой, индекс строится
  в фоне), _cache = [] запоминался навсегда. Все последующие вызовы
  all_audio_files() без force_refresh возвращали [] — «локальных
  треков нет» до перезапуска процесса.

  Теперь: _cache обновляется только если files непустой. Пустой
  индекс → возвращаем [], но НЕ кешируем; следующий вызов снова
  попробует _load_index. Стоимость — один лишний вызов
  _load_index() на каждый запрос, пока индекс пуст. Как только
  в индексе появятся треки — закешируется.

  trade-off: если индекс реально пуст (нет музыки вообще), каждый
  поиск будет дёргать _load_index. Но _load_index кеширует сам
  (см. local.py), так что это дешёвая операция — чтение JSON
  или возврат in-memory словаря. Дешевле, чем «музыка не играет
  до перезапуска».
"""

import os
import re
import glob
import threading

from shared import config as cfg


_cache = None
_cache_lock = threading.Lock()


def all_audio_files(force_refresh=False):
    """Список аудиофайлов.

    FIX (2026-11-08): из local_index.json (0.05 сек) вместо glob (8 сек).
    Fallback: glob (если local.py не импортируется — циклический импорт?).

    FIX (2026-11-20): пустой индекс не кешируем. Иначе один пустой
    ответ (индекс ещё строится / битый / MUSIC_DIR пуст) — и до
    перезапуска процесса «локальных треков нет».
    """
    global _cache
    with _cache_lock:
        if _cache is not None and not force_refresh:
            return _cache

        # FIX (2026-11-08): из индекса (0.05 сек)
        try:
            from workers.music.local import _load_index
            idx = _load_index(force_refresh=force_refresh)
            files = [t["path"] for t in idx.get("tracks", [])]
            if files:
                _cache = files
            return files
        except Exception as e:
            print(f"⚠ audio_cache: индекс недоступен ({e}) — fallback glob")

        # Fallback: glob (если local.py не импортируется)
        files = []
        for ext in ('*.mp3', '*.flac', '*.m4a', '*.aac', '*.ogg'):
            files += glob.glob(os.path.join(cfg.MUSIC_DIR, '**', ext), recursive=True)
        files = [f for f in files if '_tts' not in f and 'Jarvis' not in f]
        _cache = files
        return files


def invalidate_audio_cache():
    global _cache
    with _cache_lock:
        _cache = None


def normalize(text):
    text = text.lower()
    text = text.replace('_', ' ')
    text = re.sub(r'[^\w\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _strip_track_number(name):
    """Убирает префикс номера трека: '01 - Лесник' → 'Лесник'."""
    return re.sub(r'^\d+\s*-\s*', '', name).strip()


def _build_haystack(file_path):
    """Возвращает нормализованную строку:
       basename (без номера трека) + все родительские папки до MUSIC_DIR.
    """
    filename = os.path.splitext(os.path.basename(file_path))[0]
    filename = _strip_track_number(filename)
    norm_name = normalize(filename)

    try:
        rel = os.path.relpath(file_path, cfg.MUSIC_DIR)
    except ValueError:
        rel = os.path.basename(file_path)
    rel_no_ext = os.path.splitext(rel)[0]
    rel_words = normalize(rel_no_ext)

    return f"{norm_name} {rel_words}".strip()


def find_local_by_track(track):
    """Найти локальный файл по объекту трека (артист + название)."""
    try:
        artists_names = [a.name for a in track.artists]
        artists_full = ', '.join(artists_names)
    except Exception:
        return None

    wanted_full = normalize(f"{artists_full} - {track.title}")
    wanted_title = normalize(track.title)
    artists_norm = [normalize(a) for a in artists_names if a]

    for file_path in all_audio_files():
        haystack = _build_haystack(file_path)

        if wanted_full and wanted_full in haystack:
            return file_path

        if wanted_title and wanted_title in haystack:
            for a_norm in artists_norm:
                if a_norm and a_norm in haystack:
                    return file_path

        basename = os.path.splitext(os.path.basename(file_path))[0]
        basename = _strip_track_number(basename)
        if wanted_title == normalize(basename):
            return file_path

        if wanted_title and len(wanted_title) > 4 and wanted_title in haystack:
            return file_path

    return None


def find_local(query):
    """Найти локальный файл по строке запроса."""
    wanted = normalize(query)
    if not wanted:
        return None
    query_words = set(wanted.split())
    if not query_words:
        return None

    files = all_audio_files()

    # Проход 1: точное совпадение basename
    for file_path in files:
        basename = os.path.splitext(os.path.basename(file_path))[0]
        basename = _strip_track_number(basename)
        if normalize(basename) == wanted:
            return file_path

    # Проход 2: все слова запроса в haystack (basename + папки)
    for file_path in files:
        haystack = _build_haystack(file_path)
        haystack_words = set(haystack.split())
        if query_words.issubset(haystack_words):
            return file_path

    return None