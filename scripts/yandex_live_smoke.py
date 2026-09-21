import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import monitor

CASES = [
    (
        {"title": "Вены-реки", "artist": "Анастасия Стоцкая", "label": ""},
        {"found"},
    ),
    (
        {"title": "Die With A Smile", "artist": "Lady Gaga & Bruno Mars", "label": ""},
        {"found", "not_confirmed"},
    ),
]

out = []
failed = False
for track, allowed in CASES:
    result = monitor.check_yandex_track(track)
    out.append({"track": track, "result": result, "allowed": sorted(allowed)})
    if result.get("status") not in allowed:
        failed = True
    if result.get("status") == "found" and "music.yandex." not in result.get("url", ""):
        failed = True

print(json.dumps(out, ensure_ascii=False, indent=2))
if failed:
    sys.exit(1)
