import json
import sys
from pathlib import Path
from urllib.parse import urlencode

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import monitor

ODESLI = "https://api.song.link/v1-alpha.1/links"

CASES = [
    {"title": "Die With A Smile", "artist": "Lady Gaga & Bruno Mars"},
    {"title": "Вены-реки", "artist": "Анастасия Стоцкая"},
]


def itunes_match(track):
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
        return item
    return None


out = []
failed = False
for track in CASES:
    item = itunes_match(track)
    if not item or not item.get("trackViewUrl"):
        out.append({"track": track, "error": "no verified iTunes match"})
        failed = True
        continue

    r = requests.get(
        ODESLI,
        params={"url": item["trackViewUrl"], "userCountry": "RU"},
        headers={"User-Agent": monitor.H["User-Agent"], "Accept": "application/json"},
        timeout=30,
    )
    payload = {}
    try:
        payload = r.json()
    except ValueError:
        pass

    links = payload.get("linksByPlatform", {}) if isinstance(payload, dict) else {}
    yandex = links.get("yandex") or links.get("yandexMusic") or {}
    out.append({
        "track": track,
        "itunes": item.get("trackViewUrl"),
        "status": r.status_code,
        "platform_keys": sorted(links.keys()) if isinstance(links, dict) else [],
        "yandex": yandex,
        "pageUrl": payload.get("pageUrl") if isinstance(payload, dict) else None,
        "error_body": r.text[:300] if r.status_code >= 400 else "",
    })
    if r.status_code >= 400:
        failed = True

print(json.dumps(out, ensure_ascii=False, indent=2))
if failed:
    sys.exit(1)
