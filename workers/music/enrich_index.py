import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from phon import phon

# ⚠️ ПОДСТАВЬ ПРАВИЛЬНЫЙ ПУТЬ К ТВОЕМУ local_index.json
INDEX_FILE = Path("D:/Maks/data/local_index.json")  # или music_index.json

def enrich():
    with open(INDEX_FILE, encoding="utf-8") as f:
        data = json.load(f)

    tracks = data["tracks"]
    updated = 0
    for t in tracks:
        if "phon_artist" in t and "phon_title" in t:
            continue
        t["phon_artist"] = phon(t.get("artist", ""))
        t["phon_title"] = phon(t.get("title", ""))
        updated += 1

    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"Обогащено {updated} из {len(tracks)} треков")

if __name__ == "__main__":
    enrich()