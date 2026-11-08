# workers/music/download.py
# Скачивание треков, prefetch, постановка в очередь.
#
# download_track: subprocess.run с таймаутом 30 сек.
# При 300 Мбит/с трек 5-10 МБ качается за 1-3 сек.
# 30 сек — десятикратный запас. Не успел — Яндекс глючит, ждать нет смысла.
#
# ── FIX (этап 0.4): download_track не видит новый файл ────────────
#   Симптом: «включи арию» → play_artist → start_artist_only →
#   get_artist_tracks_page → track → play_index → find_local_by_track
#   → None → download_track → subprocess → after - before — ПУСТО
#   (JSON-индекс старый, all_audio_files(force_refresh=True) читает
#   его же) → None → attempts=1 → следующий → ... → 3 → False.
#   Яндекс-треки не играют. Регрессия FIX 2026-11-08 (JSON-индекс).
#
#   Причина: download_track делал:
#     before = set(all_audio_files())
#     subprocess.run(...)
#     after = set(all_audio_files(force_refresh=True))
#     new_files = after - before  # ← ПУСТО, JSON не пересобран
#   all_audio_files(force_refresh=True) → _load_index(force_refresh=True)
#   → _load_index_sync (JSON, старый) — _build_index НЕ вызывается.
#
#   Решение (этап 0.4):
#     1) local.invalidate_index() — сброс _index_cache.
#     2) subprocess.run(...).
#     3) _find_new_file(track) — точечный glob по MUSIC_DIR/**/*{ext}
#        (1–2 сек, БЕЗ mutagen) → фильтр по title в имени →
#        getsize >= MIN_FILE_SIZE.
#     4) local.add_path_to_index(path) — дописать ОДНУ запись в JSON
#        (0.001 сек), не пересобирая.
#
#   Почему не _build_index (8 сек): сканирует ВСЮ библиотеку + mutagen
#   × 3000. Для одного нового файла — избыточно. 5 prefetch = 40 сек.
#   _find_new_file — 1–2 сек на трек (glob без mutagen).
#
#   Fallback: если _find_new_file не нашёл (downloader упал, но файл
#   уже был) — find_local_by_track(track). Если и он None, и
#   result.returncode != 0 — поиск по title в all_audio_files(force_refresh=True)
#   (старый путь, но только в fallback).
#
# ── FIX (этап 0.5): print → log(tag="MUSIC") ────────────────────
#   Раньше все сообщения download.py шли в stderr (print), в jarvis.log
#   их не было. Отладка «почему трек не скачался» — только по консоли.

import os
import glob
import subprocess
import threading

from shared import config as cfg
from shared.log import log
from shared.audio_cache import (
    all_audio_files, invalidate_audio_cache,
    normalize, find_local_by_track,
)
from workers.music import state
from workers.music.local import (
    LocalTrack, invalidate_index, add_path_to_index,
)


DOWNLOAD_TIMEOUT_SEC = 30   # 30 сек на трек. 300 Мбит = 5-10 МБ за 1-3 сек.


def _find_new_file(track):
    """FIX (этап 0.4): точечный поиск нового файла по title (glob, 1–2 сек).

    Без полной пересборки индекса (8 сек). Только glob (без mutagen),
    фильтр по normalize(title) в normalize(имя файла).
    """
    try:
        title_norm = normalize(track.title)
    except Exception:
        return None
    if not title_norm:
        return None

    for ext in ('*.mp3', '*.flac', '*.m4a', '*.aac', '*.ogg'):
        pattern = os.path.join(cfg.MUSIC_DIR, '**', ext)
        for path in glob.glob(pattern, recursive=True):
            if '_tts' in path or 'Jarvis' in path:
                continue
            fname = os.path.splitext(os.path.basename(path))[0]
            if title_norm not in normalize(fname):
                continue
            try:
                if os.path.getsize(path) >= cfg.MIN_FILE_SIZE:
                    return path
            except OSError:
                pass
    return None


def download_track(track):
    existing = find_local_by_track(track)
    if existing:
        return existing

    token = state.TOKEN
    if not token:
        log("download_track: нет токена Яндекса", tag="MUSIC", level="WARN")
        return None

    try:
        album_id = track.albums[0].id
        track_url = f"https://music.yandex.ru/album/{album_id}/track/{track.id}"
    except (IndexError, AttributeError):
        track_url = f"https://music.yandex.ru/track/{track.id}"

    music_path = os.path.abspath(cfg.MUSIC_DIR)

    env = os.environ.copy()
    env['PYTHONUTF8'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    env['LANG'] = 'ru_RU.UTF-8'
    env['LC_ALL'] = 'ru_RU.UTF-8'

    # FIX (этап 0.4): сбросить _index_cache до скачивания. После —
    # _find_new_file (glob) найдёт новый файл, add_path_to_index допишет.
    invalidate_index()

    result = None
    try:
        result = subprocess.run(
            ['yandex-music-downloader', '--token', token, '--url', track_url,
             '--dir', music_path, '--quality', cfg.QUALITY,
             '--path-pattern', cfg.PATH_PATTERN],
            capture_output=True, text=True, timeout=DOWNLOAD_TIMEOUT_SEC,
            encoding='utf-8', errors='replace',
            env=env,
        )
    except FileNotFoundError:
        log("yandex-music-downloader не найден", tag="MUSIC", level="WARN")
        return None
    except subprocess.TimeoutExpired:
        log(f"Таймаут скачивания ({DOWNLOAD_TIMEOUT_SEC}с)",
            tag="MUSIC", level="WARN")
        return None
    finally:
        invalidate_audio_cache()

    # FIX (этап 0.4): точечный поиск нового файла (1–2 сек), не
    # all_audio_files(force_refresh=True) (читает старый JSON).
    new_path = _find_new_file(track)
    if new_path:
        try:
            size = os.path.getsize(new_path)
        except OSError:
            size = 0
        if size < cfg.MIN_FILE_SIZE:
            try:
                os.remove(new_path)
            except Exception:
                pass
            invalidate_audio_cache()
            log(f"скачанный файл слишком мал ({size} < {cfg.MIN_FILE_SIZE})",
                tag="MUSIC", level="WARN")
            return None
        add_path_to_index(new_path)
        log(f"скачано: {os.path.basename(new_path)}",
            tag="MUSIC", level="DEBUG")
        return new_path

    # Fallback: downloader вернул ошибку «уже существует» — файл был,
    # но не в индексе (или find_local_by_track не нашёл).
    if result and result.returncode != 0:
        stderr = (result.stderr or "").lower()
        if "183" in stderr or "уже существует" in stderr or "already exists" in stderr:
            track_title_norm = normalize(track.title)
            for file_path in all_audio_files(force_refresh=True):
                fname = os.path.splitext(os.path.basename(file_path))[0]
                if track_title_norm and track_title_norm in normalize(fname):
                    add_path_to_index(file_path)
                    return file_path
        existing = find_local_by_track(track)
        if existing:
            return existing

    log(f"download_track: не удалось получить файл для «{track.title}»",
        tag="MUSIC", level="WARN")
    return None


def prefetch_worker(indices):
    for idx in indices:
        with state.playlist_lock:
            if idx >= len(state.playlist) or idx in state.prefetched:
                continue
            track = state.playlist[idx]

        if isinstance(track, LocalTrack):
            with state.playlist_lock:
                state.prefetched[idx] = track.path
            log(f"[prefetch] Локально: {track.title}",
                tag="MUSIC", level="DEBUG")
            continue

        title = f"{track.title} — {', '.join(a.name for a in track.artists)}"
        local_file = find_local_by_track(track)
        if local_file:
            with state.playlist_lock:
                state.prefetched[idx] = local_file
            log(f"[prefetch] Локально: {title}", tag="MUSIC", level="DEBUG")
            continue
        log(f"[prefetch] Качаю: {title}", tag="MUSIC", level="DEBUG")
        path = download_track(track)
        with state.playlist_lock:
            state.prefetched[idx] = path
        if path:
            log(f"[prefetch] Готово: {title}", tag="MUSIC", level="DEBUG")
        else:
            log(f"[prefetch] ✗ Не удалось: {title}",
                tag="MUSIC", level="WARN")


def start_prefetch(start_index, count=cfg.PREFETCH_COUNT):
    with state.playlist_lock:
        total = len(state.playlist)
        indices = list(range(start_index, min(start_index + count, total)))
        indices = [i for i in indices if i not in state.prefetched]
    if not indices:
        return
    threading.Thread(target=prefetch_worker, args=(indices,), daemon=True).start()


def _append_and_prefetch(track):
    with state.queued_lock:
        state.queued_track_ids.add(track.id)
    with state.playlist_lock:
        state.playlist.append(track)
        new_idx = len(state.playlist) - 1
    start_prefetch(new_idx, count=1)
    return new_idx