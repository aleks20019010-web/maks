from shared import yandex
yandex.init()
c = yandex.get_client()

for tag in ["Энергичное", "Радостная музыка", "Грустная музыка", "Спокойная музыка"]:
    print(f"\n=== {tag} ===")
    try:
        mt = c.metatag(tag)
        print(f"  title={mt.title}, station_id={getattr(mt, 'station_id', None)}")
        print(f"  playlists={len(getattr(mt, 'playlists', []) or [])}")
        for pl in (getattr(mt, 'playlists', []) or [])[:3]:
            print(f"    - {pl.title} (owner={pl.owner.name if pl.owner else '?'})")
        print(f"  albums={len(getattr(mt, 'albums', []) or [])}")
    except Exception as e:
        print(f"  ошибка: {e}")