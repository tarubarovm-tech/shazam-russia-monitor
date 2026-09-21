import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import monitor

CASES = [
    {"title": "Die With A Smile", "artist": "Lady Gaga & Bruno Mars", "label": ""},
    {"title": "Starburster", "artist": "Fontaines D.C.", "label": ""},
    {"title": "Вены-реки", "artist": "Анастасия Стоцкая", "label": ""},
]

results = []
failed = False
for track in CASES:
    result = monitor.check_yandex_track(track)
    results.append({"track": track, "result": result})
    if result.get("status") not in {"found", "uncertain"}:
        failed = True

print(json.dumps(results, ensure_ascii=False, indent=2))
if failed:
    sys.exit(1)
