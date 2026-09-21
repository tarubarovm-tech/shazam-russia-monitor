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

def snippets(body, needle, radius=260, limit=4):
    out = []
    low = body.casefold()
    target = needle.casefold()
    start = 0
    while len(out) < limit:
        pos = low.find(target, start)
        if pos < 0:
            break
        out.append(body[max(0, pos-radius):pos+len(needle)+radius])
        start = pos + len(target)
    return out

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
    plain_links = sorted(set(re.findall(r"/album/\d+/track/\d+", body)))
    escaped_links = sorted(set(
        m.replace("\\/", "/")
        for m in re.findall(r"\\/album\\/\d+\\/track\\/\d+", body)
    ))
    result = {
        "track": track,
        "status": r.status_code,
        "body_len": len(body),
        "plain_links": plain_links[:10],
        "escaped_links": escaped_links[:10],
        "title_snippets": snippets(body, track["title"]),
        "artist_snippets": snippets(body, track["artist"]),
        "markers": {
            "__next_data__": "__NEXT_DATA__" in body,
            "tracks_results": '"tracks"' in body and '"results"' in body,
            "artists": '"artists"' in body,
            "search_page": "/search" in body,
        },
    }
    out.append(result)
    if r.status_code >= 400:
        failed = True

print(json.dumps(out, ensure_ascii=False, indent=2))
if failed:
    sys.exit(1)
