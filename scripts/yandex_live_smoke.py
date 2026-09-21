import html
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote, unquote, urlparse, parse_qs

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import monitor

CASES = [
    {"title": "Die With A Smile", "artist": "Lady Gaga & Bruno Mars"},
    {"title": "Вены-реки", "artist": "Анастасия Стоцкая"},
]


def extract_yandex_urls(text):
    text = html.unescape(text)
    urls = set()

    for raw in re.findall(r'https?://[^"\'<>\s]+', text):
        candidate = unquote(raw.replace("\\u0026", "&").replace("\\/", "/"))
        if "music.yandex." in candidate:
            urls.add(candidate)

        if "duckduckgo.com/l/?" in candidate:
            qs = parse_qs(urlparse(candidate).query)
            target = unquote(qs.get("uddg", [""])[0])
            if "music.yandex." in target:
                urls.add(target)

    return sorted(urls)


ENGINES = {
    "duckduckgo": lambda q: (
        "https://html.duckduckgo.com/html/?q=" + quote(q),
        {"User-Agent": monitor.H["User-Agent"]},
    ),
    "bing": lambda q: (
        "https://www.bing.com/search?q=" + quote(q),
        {"User-Agent": monitor.H["User-Agent"], "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"},
    ),
    "yandex_web": lambda q: (
        "https://yandex.ru/search/?text=" + quote(q),
        {"User-Agent": monitor.H["User-Agent"], "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"},
    ),
}

out = []
for track in CASES:
    q = f'site:music.yandex.ru/album "{track["title"]}" "{track["artist"]}"'
    record = {"track": track, "query": q, "engines": {}}
    for name, builder in ENGINES.items():
        url, headers = builder(q)
        try:
            r = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
            urls = extract_yandex_urls(r.text)
            record["engines"][name] = {
                "status": r.status_code,
                "body_len": len(r.text),
                "urls": urls[:10],
                "has_title": monitor.match_id(track["title"]) in monitor.match_id(r.text),
                "has_artist": monitor.match_id(track["artist"]) in monitor.match_id(r.text),
            }
        except Exception as exc:
            record["engines"][name] = {"error": str(exc)}
    out.append(record)

print(json.dumps(out, ensure_ascii=False, indent=2))
