"""Одноразовый импорт лайков из Яндекса в data/liked_tracks.json.
Пауза 5 сек между запросами (153 × 5 = ~13 мин). Retry при 429."""
import time
import sys

from shared import yandex
from shared.log import log
from brain.liked_tracks import merge_from_yandex, get_liked
from yandex_music.exceptions import NetworkError


def fetch_with_retry(client, track_id, max_retries=5):
    delay = 5.0
    for attempt in range(max_retries):
        try:
            tracks = client.tracks([str(track_id)])
            if tracks:
                return tracks[0]
            return None
        except NetworkError as e:
            msg = str(e)
            if "429" in msg or "Concurrency" in msg or "Too Many" in msg:
                print(f"  429 на {track_id}, жду {delay:.0f}с (попытка {attempt+1}/{max_retries})")
                time.sleep(delay)
                delay *= 2
                continue
            print(f"  Ошибка {track_id}: {e}")
            return None
        except Exception as e:
            print(f"  Ошибка {track_id}: {e}")
            return None
    return None


def main():
    print("Инициализация Яндекса...")
    if not yandex.init():
        print("Яндекс не подключён")
        return 1
    client = yandex.get_client()
    if client is None:
        print("Нет клиента Яндекса")
        return 1

    print("Получаю список лайков...")
    liked = client.users_likes_tracks()
    if not liked or not liked.tracks:
        print("Лайков нет")
        return 0

    total = len(liked.tracks)
    print(f"Лайков в Яндексе: {total}")
    print(f"Ожидаемое время: ~{total * 5 // 60} мин (пауза 5 сек/трек)")

    tracks = []
    for i, short in enumerate(liked.tracks, 1):
        if not short or not short.track_id:
            continue
        t = fetch_with_retry(client, short.track_id)
        if t is None:
            print(f"[{i}/{total}] Пропуск: не удалось получить трек")
            time.sleep(5)
            continue
        artist = ', '.join(a.name for a in t.artists) if t.artists else ""
        tracks.append({
            "artist": artist,
            "title": t.title or "",
            "track_id": str(t.id) if t.id else "",
        })
        print(f"[{i}/{total}] {artist} — {t.title}")
        time.sleep(5)

    print(f"\nСобрано треков: {len(tracks)}. Записываю...")
    added = merge_from_yandex(tracks)
    print(f"Добавлено: {added}")
    print(f"Всего в liked_tracks.json: {len(get_liked())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())