import json
import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import monitor


def itunes_track_url(track):
    term = f"{track['title']} {track['artist']}"
    data = monitor.get(
        monitor.ITUNES_SEARCH,
        params={
            "term": term,
            "media": "music",
            "entity": "song",
            "country": "ru",
            "limit": 10,
        },
        timeout=15,
    ).json()
    expected_title = monitor.match_id(track["title"])
    for item in data.get("results", []):
        if monitor.match_id(item.get("trackName", "")) != expected_title:
            continue
        if not monitor.artist_matches(track["artist"], item.get("artistName", "")):
            continue
        if item.get("trackViewUrl"):
            return item["trackViewUrl"]
    return ""


CASES = [
    {"title": "Die With A Smile", "artist": "Lady Gaga & Bruno Mars"},
    {"title": "Starburster", "artist": "Fontaines D.C."},
]

out = []
failed = False
for track in CASES:
    source = itunes_track_url(track)
    if not source:
        out.append({"track": track, "error": "no iTunes source URL"})
        failed = True
        continue

    response = requests.get(
        "https://ml.jadquir.com/resolve",
        params={"q": source},
        headers={"User-Agent": monitor.H["User-Agent"]},
        timeout=30,
        allow_redirects=True,
    )
    text = response.text
    yandex_links = sorted(set(re.findall(r"https?://music\\.yandex\\.(?:ru|com)/[^\\\"'<>\\s]+", text)))
    out.append(
        {
            "track": track,
            "source": source,
            "status": response.status_code,
            "final_url": response.url,
            "yandex_links": yandex_links[:5],
            "body_prefix": text[:300],
        }
    )
    if response.status_code >= 400:
        failed = True

print(json.dumps(out, ensure_ascii=False, indent=2))
if failed:
    sys.exit(1)
