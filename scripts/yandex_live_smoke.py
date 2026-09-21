import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import monitor

CASES = [
    {"title": "Die With A Smile", "artist": "Lady Gaga & Bruno Mars", "label": ""},
    {"title": "Вены-реки", "artist": "Анастасия Стоцкая", "label": ""},
]

out = []
failed = False
for track in CASES:
    query = " ".join([track["title"], track["artist"]]).strip()
    items = monitor.yandex_search_results(query)
    qualities = [monitor.yandex_candidate_quality(track, item) for item in items[:20]]
    accepted = [q for q in qualities if q["status"] in {"found", "uncertain"}]
    out.append(
        {
            "query": query,
            "items": len(items),
            "best": max(qualities, key=lambda x: x["score"]) if qualities else None,
            "accepted": accepted[:3],
        }
    )
    if not accepted:
        failed = True

print(json.dumps(out, ensure_ascii=False, indent=2))
if failed:
    sys.exit(1)
