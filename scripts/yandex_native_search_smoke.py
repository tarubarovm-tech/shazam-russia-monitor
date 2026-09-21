import json
import re
from urllib.parse import quote

import requests

KNOWN = "Если я буду танцевать Баста Моя Мишель"
MISSING = "zzzz-no-such-track-9f0d8c7b6a5e4d3c2b1a"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://music.yandex.ru/",
    "X-Retpath-Y": "https://music.yandex.ru/search",
}


def summarize_response(name, response):
    print("\n===", name, "===")
    print("status", response.status_code)
    print("content-type", response.headers.get("content-type"))
    print("url", response.url)
    text = response.text
    print("len", len(text))
    print("head", text[:500].replace("\n", " "))
    try:
        data = response.json()
    except Exception:
        data = None
    if isinstance(data, dict):
        print("json_keys", sorted(data.keys())[:50])
        tracks = data.get("tracks")
        if isinstance(tracks, dict):
            print("tracks_total", tracks.get("total"))
            items = tracks.get("items")
            if isinstance(items, list):
                print("track_items", len(items))
                for item in items[:5]:
                    if isinstance(item, dict):
                        artists = item.get("artists") or []
                        artist_names = [
                            a.get("name", "") for a in artists if isinstance(a, dict)
                        ]
                        albums = item.get("albums") or []
                        album_ids = [
                            str(a.get("id")) for a in albums
                            if isinstance(a, dict) and a.get("id") is not None
                        ]
                        print(
                            "ITEM",
                            item.get("id"),
                            item.get("title"),
                            artist_names,
                            album_ids[:2],
                        )
    urls = sorted(set(re.findall(
        r'https?://music\.yandex\.(?:ru|com)/(?:album/\d+/track/\d+|track/\d+)',
        text,
    )))
    print("direct_track_urls", urls[:10])


def probe(query):
    q = quote(query)
    endpoints = [
        (
            "legacy_ru",
            f"https://music.yandex.ru/handlers/music-search.jsx?text={q}&type=tracks&page=0",
        ),
        (
            "legacy_com",
            f"https://music.yandex.com/handlers/music-search.jsx?text={q}&type=tracks&page=0",
        ),
        (
            "api_track",
            f"https://api.music.yandex.net/search?text={q}&type=track&page=0&nocorrect=true",
        ),
        (
            "api_all",
            f"https://api.music.yandex.net/search?text={q}&type=all&page=0&nocorrect=true",
        ),
        (
            "web_search",
            f"https://music.yandex.ru/search?text={q}&type=tracks",
        ),
    ]
    print("\n\n######## QUERY", query, "########")
    for name, url in endpoints:
        try:
            response = requests.get(url, headers=HEADERS, timeout=25, allow_redirects=True)
        except Exception as exc:
            print("\n===", name, "===")
            print("ERROR", repr(exc))
            continue
        summarize_response(name, response)


if __name__ == "__main__":
    probe(KNOWN)
    probe(MISSING)
