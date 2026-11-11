# workers/music/download.py
# Скачивание треков, prefetch, постановка в очередь.
#
# download_track: subprocess.run с таймаутом 30 сек.
# При 300 Мбит/с трек 5-10 МБ качается за 1-3 сек.
# 30 сек — десятикратный запас. Не успел — Яндекс глючит, ждать нет смысла.
#
# ── FIX (этап 0.4): download_track не видит новый файл ────────────
#   ... (не трогаем, твои фиксы) ...
#
# ── DOWNLOAD_BY_ID (v6.5) ───────────────────────────────────────
#   download_by_id(track_id) — скачать трек по yandex track_id напрямую,
#   без search_track.
#
# ── FIX (v6.6): prefetch_worker + LikedTrack ────────────────────
#   LikedTrack — лёгкий дескриптор (artist + title + track_id),
#   НЕ Track Яндекса. Раньше prefetch_worker падал на track.artists.
#   Теперь:
#     - LocalTrack → в prefetched как path;
#     - LikedTrack → ищем локально (search_local) → иначе download_by_id;
#     - Track → как раньше (find_local_by_track / download_track).

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
    LocalTrack, LikedTrack, invalidate_index, add_path_to_index,
)


DOWNLOAD_TIMEOUT_SEC = 30


def _find_new_file(track):
    """FIX (этап 0.4): точечный поиск нового файла по title (glob, 1–2 сек)."""
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


def download_by_id(track_id: str) -> str | None:
    """Скачать трек по yandex track_id напрямую (без search_track).
    Возвращает путь к файлу или None."""
    if state.client is None or not track_id:
        return None
    try:
        tracks = state.client.tracks([str(track_id)])
        if not tracks:
            return None
        return download_track(tracks[0])
    except Exception as e:
        log(f"download_by_id({track_id}) error: {e}",
            tag="MUSIC", level="WARN")
        return None


def prefetch_worker(indices):
    for idx in indices:
        with state.playlist_lock:
            if idx >= len(state.playlist) or idx in state.prefetched:
                continue
            track = state.playlist[idx]

        # ── LocalTrack ────────────────────────────────────
        if isinstance(track, LocalTrack):
            with state.playlist_lock:
                state.prefetched[idx] = track.path
            log(f"[prefetch] Локально: {track.title}",
                tag="MUSIC", level="DEBUG")
            continue

        # ── LikedTrack (лёгкий дескриптор) ───────────────
        if isinstance(track, LikedTrack):
            title = f"{track.title} — {track.artist}"
            # 1) Локально есть?
            try:
                from workers.music.search import search_local
                local = search_local(f"{track.artist} {track.title}".strip())
            except Exception as e:
                log(f"[prefetch] search_local: {e}",
                    tag="MUSIC", level="WARN")
                local = None
            if local:
                with state.playlist_lock:
                    state.prefetched[idx] = local["path"]
                log(f"[prefetch] Локально (лайк): {title}",
                    tag="MUSIC", level="DEBUG")
                continue
            # 2) Скачать по track_id
            log(f"[prefetch] Качаю лайк: {title}",
                tag="MUSIC", level="DEBUG")
            path = download_by_id(track.id)
            with state.playlist_lock:
                state.prefetched[idx] = path
            if path:
                log(f"[prefetch] Готово (лайк): {title}",
                    tag="MUSIC", level="DEBUG")
            else:
                log(f"[prefetch] ✗ Не удалось (лайк): {title}",
                    tag="MUSIC", level="WARN")
            continue

        # ── Track Яндекса (как раньше) ───────────────────
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