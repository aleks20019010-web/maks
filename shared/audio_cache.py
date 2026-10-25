"""
shared/audio_cache.py — кэш списка аудиофайлов и поиск по локальной библиотеке.

Кэш — локальный для модуля. Не зависит от state.
Brain и workers/music используют этот модуль — оба.

Поиск: basename + родительские папки (папка артиста тоже участвует).
"""

import os
import re
import glob
import threading

from shared import config as cfg


_cache = None
_cache_lock = threading.Lock()


def all_audio_files(force_refresh=False):
    global _cache
    with _cache_lock:
        if _cache is not None and not force_refresh:
            return _cache
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