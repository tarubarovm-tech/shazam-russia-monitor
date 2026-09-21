import html
import json
import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import monitor

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


def extract_yandex_urls(text):
    text = html.unescape(text).replace("\\/", "/")
    urls = []
    for raw in re.findall(r'https?://[^"\'<>\s]+', text):
        if "music.yandex." in raw and raw not in urls:
            urls.append(raw)
    return urls


out = []
failed = False
for track in CASES:
    item = itunes_match(track)
    if not item or not item.get("trackId"):
        out.append({"track": track, "error": "no verified iTunes track id"})
        failed = True
        continue

    url = f"https://song.link/i/{item['trackId']}"
    r = requests.get(
        url,
        headers={
            "User-Agent": monitor.H["User-Agent"],
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        },
        timeout=30,
        allow_redirects=True,
    )
    body = r.text
    out.append({
        "track": track,
        "itunes_track_id": item["trackId"],
        "status": r.status_code,
        "final_url": r.url,
        "body_len": len(body),
        "has_title": monitor.match_id(track["title"]) in monitor.match_id(body),
        "has_artist": monitor.match_id(track["artist"]) in monitor.match_id(body),
        "has_yandex_word": "yandex" in body.casefold(),
        "yandex_urls": extract_yandex_urls(body)[:10],
        "prefix": body[:250],
    })
    if r.status_code >= 400:
        failed = True

print(json.dumps(out, ensure_ascii=False, indent=2))
if failed:
    sys.exit(1)
