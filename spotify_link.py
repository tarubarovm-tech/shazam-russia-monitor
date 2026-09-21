#!/usr/bin/env python3
"""
Ссылка на трек в Spotify — через официальный Spotify Web API.

ЗАЧЕМ ОТДЕЛЬНЫЙ МОДУЛЬ
----------------------
extract_spotify_urls() в monitor.py ищет «open.spotify.com/track/...» в HTML
страницы Songlink. Замер 21.09.2026: таких ссылок там НЕТ НИ ДЛЯ ОДНОГО
трека — ни для локальных новинок, ни для мировых хитов вроде Blinding Lights.

Songlink отдаёт платформы в блоке __NEXT_DATA__, и у Spotify запись есть,
но БЕЗ поля url — в отличие от Apple Music, Deezer, TIDAL и Yandex,
у которых url на месте. То есть Songlink сознательно не публикует ссылки
Spotify (как и YouTube) в открытой выдаче.

Поэтому ссылку берём у самого Spotify: Client Credentials flow, поиск по
названию и артисту. Замер: находит и «Ты не бойся ночи — ENZRO»,
и «Море — Женя Трофимов», и мировые хиты.

НАСТРОЙКА
---------
Нужны SPOTIFY_CLIENT_ID и SPOTIFY_CLIENT_SECRET в переменных окружения
(https://developer.spotify.com/dashboard -> Create app).

Без них модуль молча выключается: available() вернёт False, и монитор
продолжит ставить в алерт ссылку на Apple Music, как раньше.

ВАЖНО ПРО РЫНОК. Spotify ушёл из России, рынок RU не обслуживается —
при market=RU поиск падает. Используем DE: каталог там полный, ссылка
на трек одинаковая для всех стран.
"""
import base64
import os
import re
import threading
import time
import unicodedata

import requests

TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"
MARKET = os.environ.get("SPOTIFY_MARKET", "DE")
TIMEOUT = 20
REQUEST_DELAY = 0.12          # мягкая пауза, чтобы не выбивать лимит
MATCH_THRESHOLD = 70          # минимальное совпадение названия, %

_token = None
_token_exp = 0.0
_token_lock = threading.Lock()
_memo = {}
_memo_lock = threading.Lock()

try:
    from thefuzz import fuzz
except ImportError:
    fuzz = None


def credentials():
    return (
        os.environ.get("SPOTIFY_CLIENT_ID", "").strip(),
        os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip(),
    )


def available():
    """Можно ли вообще искать в Spotify."""
    cid, secret = credentials()
    return bool(cid and secret)


def _get_token():
    global _token, _token_exp
    with _token_lock:
        if _token and time.time() < _token_exp - 30:
            return _token
        cid, secret = credentials()
        if not (cid and secret):
            raise RuntimeError("не заданы SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET")
        auth = base64.b64encode(f"{cid}:{secret}".encode()).decode()
        r = requests.post(
            TOKEN_URL,
            data={"grant_type": "client_credentials"},
            headers={"Authorization": f"Basic {auth}"},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        _token = data["access_token"]
        _token_exp = time.time() + int(data.get("expires_in", 3600))
        return _token


def _norm(value):
    """Для сравнения названий: нижний регистр, без версий и пунктуации."""
    s = (value or "").lower().strip().replace("ё", "е")
    s = re.sub(
        r"[\(\[][^)\]]*(remix|mix|version|edit|slowed|sped up|remaster(?:ed)?|"
        r"live|acoustic|instrumental|extended|radio|explicit|clean)[^)\]]*[\)\]]",
        " ", s,
    )
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^a-zа-я0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _similar(a, b):
    if fuzz is None:
        return 100 if _norm(a) == _norm(b) else 0
    return fuzz.token_sort_ratio(_norm(a), _norm(b))


def _search(query, limit=5):
    r = requests.get(
        f"{API}/search",
        headers={"Authorization": f"Bearer {_get_token()}"},
        params={"q": query, "type": "track", "limit": limit, "market": MARKET},
        timeout=TIMEOUT,
    )
    if r.status_code == 429:
        wait = int(r.headers.get("Retry-After", "2"))
        if wait <= 10:
            time.sleep(wait + 1)
            return _search(query, limit)
        raise RuntimeError(f"Spotify rate limit, Retry-After={wait}s")
    r.raise_for_status()
    time.sleep(REQUEST_DELAY)
    return r.json().get("tracks", {}).get("items", []) or []


def find_track_url(artist, title):
    """
    Прямая ссылка на трек в Spotify или "" если не нашлось.

    Ищем по «артист название», сверяем название найденного с искомым —
    чтобы не подсунуть чужой трек с похожим именем. Артиста в сверку не
    берём жёстко: в чартах он часто записан со всеми соисполнителями,
    а у Spotify — только главный.
    """
    artist = (artist or "").strip()
    title = (title or "").strip()
    if not title or not available():
        return ""

    key = (_norm(artist), _norm(title))
    with _memo_lock:
        if key in _memo:
            return _memo[key]

    url = ""
    try:
        queries = [f"{artist} {title}".strip()]
        if artist:
            # поле-ориентированный запрос точнее, но иногда не находит
            queries.insert(0, f'track:"{title}" artist:"{artist.split(",")[0].split("&")[0].strip()}"')
        else:
            queries.append(title)

        for query in queries:
            try:
                items = _search(query)
            except Exception:
                continue
            best, best_score = None, 0
            for item in items:
                score = _similar(title, item.get("name", ""))
                if score > best_score:
                    best, best_score = item, score
            if best and best_score >= MATCH_THRESHOLD:
                url = (best.get("external_urls") or {}).get("spotify", "") or ""
                if url:
                    break
    except Exception:
        url = ""

    with _memo_lock:
        _memo[key] = url
    return url
