import json
import re
import sys
from pathlib import Path
from urllib.parse import quote

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import monitor

CASES = [
    {"title": "Die With A Smile", "artist": "Lady Gaga & Bruno Mars"},
    {"title": "Вены-реки", "artist": "Анастасия Стоцкая"},
]

out = []
failed = False
for track in CASES:
    query = f"{track['title']} {track['artist']}"
    url = "https://music.yandex.ru/search?text=" + quote(query)
    r = requests.get(
        url,
        headers={
            "User-Agent": monitor.H["User-Agent"],
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        },
        timeout=25,
        allow_redirects=True,
    )
    body = r.text
    has_title = monitor.match_id(track["title"]) in monitor.match_id(body)
    has_artist = monitor.match_id(track["artist"]) in monitor.match_id(body)
    track_links = sorted(set(re.findall(r"/album/\d+/track/\d+", body)))
    out.append({
        "track": track,
        "status": r.status_code,
        "final_url": r.url,
        "body_len": len(body),
        "has_title": has_title,
        "has_artist": has_artist,
        "track_links": track_links[:10],
        "prefix": body[:200],
    })
    if r.status_code >= 400:
        failed = True

print(json.dumps(out, ensure_ascii=False, indent=2))
if failed:
    sys.exit(1)
