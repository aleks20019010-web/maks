# workers/music/download.py
# Скачивание треков, prefetch, постановка в очередь.
#
# download_track: subprocess.run с таймаутом 30 сек.
# При 300 Мбит/с трек 5-10 МБ качается за 1-3 сек.
# 30 сек — десятикратный запас. Не успел — Яндекс глючит, ждать нет смысла.

import os
import subprocess
import threading

from shared import config as cfg
from shared.audio_cache import (
    all_audio_files, invalidate_audio_cache,
    normalize, find_local_by_track,
)
from workers.music import state
from workers.music.local import LocalTrack


DOWNLOAD_TIMEOUT_SEC = 30   # 30 сек на трек. 300 Мбит = 5-10 МБ за 1-3 сек.


def download_track(track):
    existing = find_local_by_track(track)
    if existing:
        return existing

    token = state.TOKEN
    if not token:
        print("  ✗ download_track: нет токена Яндекса")
        return None

    try:
        album_id = track.albums[0].id
        track_url = f"https://music.yandex.ru/album/{album_id}/track/{track.id}"
    except (IndexError, AttributeError):
        track_url = f"https://music.yandex.ru/track/{track.id}"

    music_path = os.path.abspath(cfg.MUSIC_DIR)
    before = set(all_audio_files())

    env = os.environ.copy()
    env['PYTHONUTF8'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    env['LANG'] = 'ru_RU.UTF-8'
    env['LC_ALL'] = 'ru_RU.UTF-8'

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
        print("  ✗ yandex-music-downloader не найден")
        return None
    except subprocess.TimeoutExpired:
        print(f"  Таймаут скачивания ({DOWNLOAD_TIMEOUT_SEC}с)")
        return None
    finally:
        invalidate_audio_cache()

    after = set(all_audio_files(force_refresh=True))
    new_files = after - before
    if new_files:
        file_path = list(new_files)[0]
        try:
            size = os.path.getsize(file_path)
        except OSError:
            return None
        if size < cfg.MIN_FILE_SIZE:
            try:
                os.remove(file_path)
            except Exception:
                pass
            invalidate_audio_cache()
            return None
        return file_path

    if result and result.returncode != 0:
        stderr = (result.stderr or "").lower()
        if "183" in stderr or "уже существует" in stderr or "already exists" in stderr:
            track_title_norm = normalize(track.title)
            for file_path in all_audio_files(force_refresh=True):
                fname = os.path.splitext(os.path.basename(file_path))[0]
                if track_title_norm and track_title_norm in normalize(fname):
                    return file_path
        existing = find_local_by_track(track)
        if existing:
            return existing
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
            print(f"  [prefetch] Локально: {track.title}")
            continue

        title = f"{track.title} — {', '.join(a.name for a in track.artists)}"
        local_file = find_local_by_track(track)
        if local_file:
            with state.playlist_lock:
                state.prefetched[idx] = local_file
            print(f"  [prefetch] Локально: {title}")
            continue
        print(f"  [prefetch] Качаю: {title}")
        path = download_track(track)
        with state.playlist_lock:
            state.prefetched[idx] = path
        if path:
            print(f"  [prefetch] Готово: {title}")
        else:
            print(f"  [prefetch] ✗ Не удалось: {title}")


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