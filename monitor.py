import csv
import hashlib
import html as html_lib
import io
import json
import os
import re
import time
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

STATE = Path("chart_state.json")
TZ = ZoneInfo("Europe/Moscow")
H = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}
SHAZAM = "https://www.shazam.com/services/charts/csv/top-200/russia/"
APPLE = "https://music.apple.com/ru/playlist/shazam-charts-russia/pl.b96cdf2da806490ea383b8a0cb45790d"
ITUNES_SEARCH = "https://itunes.apple.com/search"
ITUNES_LOOKUP = "https://itunes.apple.com/lookup"
SONGLINK_TRACK = "https://song.link/i/{track_id}"
SCHEMA_VERSION = 5
DUPLICATE_WINDOW_SECONDS = 6 * 60 * 60
LABEL_CACHE_SECONDS = 30 * 24 * 60 * 60
YANDEX_CACHE_SECONDS = {
    "found": 24 * 60 * 60,
    "not_confirmed": 2 * 60 * 60,
    "uncertain": 30 * 60,
    "error": 10 * 60,
}
SONGLINK_RETRIES = 2
ALERT_MODE = "new_tracks_without_yandex_v1"
REPORT_YANDEX_STATUSES = {"not_confirmed"}
_ITUNES_MATCH_MEMO = {}


def repair_text(value):
    """Repair UTF-8 text that was accidentally decoded as Latin-1."""
    s = html_lib.unescape(str(value or "")).strip()
    for _ in range(3):
        try:
            fixed = s.encode("latin1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            break
        if fixed == s:
            break
        s = fixed
    return unicodedata.normalize("NFC", s)


def text_id(value):
    return re.sub(r"\s+", " ", repair_text(value).casefold()).strip()


def match_id(value):
    value = repair_text(value).casefold().replace("ё", "е")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"[^\w]+", " ", value, flags=re.UNICODE).strip()


_RU_LATIN = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l",
    "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s",
    "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch",
    "ш": "sh", "щ": "shch", "ы": "y", "э": "e", "ю": "yu", "я": "ya",
    "ь": "", "ъ": "",
})


def latinize_ru(value):
    return match_id(value).translate(_RU_LATIN)


def clean_track(title, artist="", label=""):
    return {
        "title": repair_text(title),
        "artist": repair_text(artist),
        "label": repair_text(label),
    }


def yandex_status_text(info):
    status = (info or {}).get("status")
    return {
        "found": "🟡 Яндекс: есть",
        "not_confirmed": "⚪ Яндекс: не подтверждён",
        "uncertain": "🟠 Яндекс: неоднозначно",
        "error": "⚠️ Яндекс: проверка недоступна",
    }.get(status, "⚠️ Яндекс: не проверен")


def display_track(track, yandex_info=None):
    title = repair_text(track.get("title", ""))
    artist = repair_text(track.get("artist", ""))
    label = repair_text(track.get("label", ""))
    text = title + (f" — {artist}" if artist else "")
    return f"{text} · 🏷 {label or 'не найден'} · {yandex_status_text(yandex_info)}"


def send(text):
    token = os.environ["BOT_TOKEN"].strip()
    chat_id = os.environ["CHAT_ID"].strip()
    while text:
        cut = min(3900, len(text))
        if cut < len(text):
            p = text.rfind("\n", 0, cut)
            if p > 2000:
                cut = p
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text[:cut], "disable_web_page_preview": "true"},
            timeout=30,
        )
        r.raise_for_status()
        text = text[cut:].lstrip()


def get(url, **kwargs):
    headers = kwargs.pop("headers", H)
    r = requests.get(url, headers=headers, timeout=kwargs.pop("timeout", 35), **kwargs)
    r.raise_for_status()
    return r


def shazam():
    text = get(SHAZAM).content.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    i = next(
        (i for i, x in enumerate(lines) if x.lstrip("\ufeff").strip().lower().startswith("rank,artist,title")),
        None,
    )
    if i is None:
        raise RuntimeError("не найден заголовок Rank,Artist,Title")

    out = []
    for row in csv.DictReader(io.StringIO("\n".join(lines[i:]))):
        low = {str(k).strip().lower(): (v or "").strip() for k, v in row.items() if k}
        if low.get("title"):
            label = low.get("label") or low.get("record label") or low.get("recordlabel") or ""
            out.append(clean_track(low["title"], low.get("artist", ""), label))
    if len(out) < 150:
        raise RuntimeError(f"получено только {len(out)} треков")
    return out[:200]


def artist_name(value):
    if isinstance(value, list):
        return ", ".join(x for x in (artist_name(v) for v in value) if x)
    if isinstance(value, dict):
        for field in ("name", "artistName"):
            if value.get(field):
                return repair_text(value[field])
        return ""
    return repair_text(value)


def label_name(value):
    if isinstance(value, list):
        return ", ".join(x for x in (label_name(v) for v in value) if x)
    if isinstance(value, dict):
        attrs = value.get("attributes") if isinstance(value.get("attributes"), dict) else {}
        for field in ("recordLabel", "recordLabelName", "label", "name"):
            candidate = attrs.get(field) or value.get(field)
            if candidate and not isinstance(candidate, (dict, list)):
                return repair_text(candidate)
        return ""
    return repair_text(value)


def label_from_node(node):
    if not isinstance(node, dict):
        return ""
    attrs = node.get("attributes") if isinstance(node.get("attributes"), dict) else {}
    for field in ("recordLabel", "recordLabelName", "label"):
        if attrs.get(field):
            return repair_text(attrs[field])

    relationships = node.get("relationships") if isinstance(node.get("relationships"), dict) else {}
    albums = relationships.get("albums") if isinstance(relationships.get("albums"), dict) else {}
    for album in albums.get("data", []) if isinstance(albums.get("data"), list) else []:
        if isinstance(album, dict):
            album_attrs = album.get("attributes") if isinstance(album.get("attributes"), dict) else {}
            for field in ("recordLabel", "recordLabelName", "label"):
                if album_attrs.get(field):
                    return repair_text(album_attrs[field])
    return ""


def collect_apple_songs(node, out):
    if isinstance(node, dict):
        attrs = node.get("attributes")
        if isinstance(attrs, dict):
            play_params = attrs.get("playParams") if isinstance(attrs.get("playParams"), dict) else {}
            is_song = (
                node.get("type") == "songs"
                or play_params.get("kind") == "song"
                or "durationInMillis" in attrs
            )
            if is_song and attrs.get("name"):
                out.append(
                    clean_track(
                        attrs.get("name"),
                        attrs.get("artistName", ""),
                        label_from_node(node),
                    )
                )

        types = node.get("@type")
        if isinstance(types, str):
            types = [types]
        if isinstance(types, list) and any(t in ("MusicRecording", "Song") for t in types):
            title = node.get("name")
            artist = node.get("byArtist") or node.get("author")
            album = node.get("inAlbum") if isinstance(node.get("inAlbum"), dict) else {}
            if not artist:
                artist = album.get("byArtist") or album.get("author")
            label = (
                label_name(node.get("recordLabel"))
                or label_name(node.get("publisher"))
                or label_name(album.get("recordLabel"))
                or label_name(album.get("publisher"))
            )
            if title:
                out.append(clean_track(title, artist_name(artist), label))

        for value in node.values():
            collect_apple_songs(value, out)
    elif isinstance(node, list):
        for value in node:
            collect_apple_songs(value, out)


def merge_candidates(candidates):
    ordered = []
    by_title = {}
    for track in candidates:
        title = text_id(track.get("title", ""))
        if not title:
            continue
        existing = by_title.get(title)
        if existing is None:
            item = clean_track(
                track.get("title", ""),
                track.get("artist", ""),
                track.get("label", ""),
            )
            by_title[title] = item
            ordered.append(item)
        else:
            if not existing.get("artist") and track.get("artist"):
                existing["artist"] = repair_text(track["artist"])
            if not existing.get("label") and track.get("label"):
                existing["label"] = repair_text(track["label"])
    return ordered


def enrich_metadata(tracks, fallback):
    if not fallback:
        return tracks

    counts = Counter(text_id(x.get("title", "")) for x in fallback)
    meta = {
        text_id(x.get("title", "")): x
        for x in fallback
        if counts[text_id(x.get("title", ""))] == 1
    }
    for track in tracks:
        source = meta.get(text_id(track.get("title", "")))
        if not source:
            continue
        if not track.get("artist") and source.get("artist"):
            track["artist"] = repair_text(source["artist"])
        if not track.get("label") and source.get("label"):
            track["label"] = repair_text(source["label"])
    return tracks


def apple(fallback=None):
    page = get(APPLE).content.decode("utf-8", errors="replace")
    candidates = []
    pattern = r'<script[^>]+type=["\']application/(?:ld\+json|json)["\'][^>]*>(.*?)</script>'
    for raw in re.findall(pattern, page, re.I | re.S):
        try:
            collect_apple_songs(json.loads(raw), candidates)
        except (json.JSONDecodeError, TypeError):
            continue

    out = merge_candidates(candidates)
    out = enrich_metadata(out, fallback)
    if len(out) < 100:
        raise RuntimeError(f"получено только {len(out)} треков")
    artist_count = sum(bool(x.get("artist")) for x in out[:200])
    if artist_count < 100:
        raise RuntimeError(f"исполнитель распознан только у {artist_count} треков")
    return out[:200]


def normalized_tracks(items):
    if not isinstance(items, list):
        return items
    return [
        clean_track(x.get("title", ""), x.get("artist", ""), x.get("label", ""))
        for x in items
        if isinstance(x, dict)
    ]


def indexed(items):
    bases = [text_id(x.get("title", "")) for x in items]
    counts = Counter(bases)
    result = {}
    for pos, (base, track) in enumerate(zip(bases, items), 1):
        ident = base
        if counts[base] > 1:
            artist = text_id(track.get("artist", ""))
            ident = f"{base}\x1f{artist or pos}"
        result[ident] = (pos, track)
    return result


def make_delta(old, new):
    op = indexed(old)
    np = indexed(new)
    added = sorted((p, ident, track) for ident, (p, track) in np.items() if ident not in op)
    gone = sorted((p, ident, track) for ident, (p, track) in op.items() if ident not in np)
    moved = sorted(
        (
            abs(op[ident][0] - p),
            p,
            op[ident][0],
            ident,
            track,
        )
        for ident, (p, track) in np.items()
        if ident in op and op[ident][0] != p
    )
    moved.sort(reverse=True)
    return {"added": added, "gone": gone, "moved": moved}


def has_chart_change(delta):
    return bool(delta["added"] or delta["gone"] or delta["moved"])


def event_fingerprint(delta):
    if delta["added"] or delta["gone"]:
        payload = {
            "added": [(p, ident) for p, ident, _ in delta["added"]],
            "gone": [(p, ident) for p, ident, _ in delta["gone"]],
            "moved_count": len(delta["moved"]),
        }
    else:
        payload = {
            "moved": [(p, old_p, ident) for _, p, old_p, ident, _ in delta["moved"]],
        }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def parse_label_copyright(value):
    value = repair_text(value)
    value = re.sub(r"^[℗©]\s*", "", value)
    value = re.sub(r"^\d{4}(?:[-–]\d{4})?\s*", "", value)
    return value.strip(" .")


def artist_matches(expected, actual):
    expected = match_id(expected)
    actual = match_id(actual)
    if not expected or not actual:
        return True
    if expected == actual or expected in actual or actual in expected:
        return True
    a = set(expected.split())
    b = set(actual.split())
    return bool(a and b and len(a & b) / min(len(a), len(b)) >= 0.5)


def title_core(value):
    value = repair_text(value)
    value = re.sub(
        r"\s*[\(\[]\s*(?:feat|ft|featuring)\.?\s+.*?[\)\]]\s*$",
        "",
        value,
        flags=re.I,
    )
    value = re.sub(r"\s+(?:feat|ft|featuring)\.?\s+.+$", "", value, flags=re.I)
    return match_id(value)


def similarity(a, b):
    a = match_id(a)
    b = match_id(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    direct = SequenceMatcher(None, a, b).ratio()
    translit = SequenceMatcher(None, latinize_ru(a), latinize_ru(b)).ratio()
    return max(direct, translit)


def artist_parts(value):
    value = repair_text(value)
    value = re.sub(r"\s+(?:feat|ft|featuring)\.?\s+", ",", value, flags=re.I)
    parts = re.split(r"\s*(?:,|&|;|×|\+)\s*", value)
    out = []
    for part in parts:
        normalized = match_id(part)
        if normalized and normalized not in out:
            out.append(normalized)
    return out


def title_match_score(expected, candidate):
    exact = similarity(expected, candidate)
    if match_id(expected) == match_id(candidate):
        return 1.0
    expected_core = title_core(expected)
    candidate_core = title_core(candidate)
    if expected_core and expected_core == candidate_core:
        return max(exact, 0.985)
    return exact


def artist_match_score(expected, actual_names):
    expected_parts = artist_parts(expected)
    actual = [match_id(x) for x in actual_names if match_id(x)]
    if not expected_parts or not actual:
        return 0.0

    if len(expected_parts) == 1:
        return max(similarity(expected_parts[0], name) for name in actual)

    best_each = [
        max(similarity(expected_part, actual_name) for actual_name in actual)
        for expected_part in expected_parts
    ]
    coverage = sum(score >= 0.88 for score in best_each) / len(best_each)
    mean_score = sum(best_each) / len(best_each)
    primary_score = best_each[0]

    expected_words = set(" ".join(expected_parts).split())
    actual_words = set(" ".join(actual).split())
    word_overlap = (
        len(expected_words & actual_words) / len(expected_words)
        if expected_words
        else 0.0
    )
    return max(
        0.55 * mean_score + 0.30 * coverage + 0.15 * primary_score,
        0.85 * word_overlap + 0.15 * primary_score,
    )


def itunes_candidate_quality(track, item):
    title_score = title_match_score(
        track.get("title", ""),
        item.get("trackName", ""),
    )
    artist_score = artist_match_score(
        track.get("artist", ""),
        [item.get("artistName", "")],
    )

    if not repair_text(track.get("artist", "")) and title_score >= 0.995:
        status = "uncertain"
    elif title_score >= 0.995 and artist_score >= 0.82:
        status = "found"
    elif title_score >= 0.97 and artist_score >= 0.93:
        status = "found"
    elif title_score >= 0.92 and artist_score >= 0.60:
        status = "uncertain"
    else:
        status = "reject"

    return {
        "status": status,
        "score": round(0.62 * title_score + 0.38 * artist_score, 4),
        "title_score": round(title_score, 4),
        "artist_score": round(artist_score, 4),
    }


def find_itunes_track(track):
    key = cache_key(track)
    if key in _ITUNES_MATCH_MEMO:
        return _ITUNES_MATCH_MEMO[key]

    title = repair_text(track.get("title", ""))
    artist = repair_text(track.get("artist", ""))
    if not title:
        _ITUNES_MATCH_MEMO[key] = None
        return None

    term = " ".join(x for x in (title, artist) if x)
    best = None
    best_score = -1.0

    for country in ("ru", "us"):
        try:
            data = get(
                ITUNES_SEARCH,
                params={
                    "term": term,
                    "media": "music",
                    "entity": "song",
                    "country": country,
                    "limit": 15,
                },
                timeout=15,
            ).json()
        except (requests.RequestException, ValueError):
            continue

        for item in data.get("results", []):
            if not isinstance(item, dict):
                continue
            quality = itunes_candidate_quality(track, item)
            if quality["status"] == "reject":
                continue
            score = quality["score"]
            candidate = {
                "item": item,
                "country": country,
                "quality": quality,
            }
            if quality["status"] == "found" and score > best_score:
                best = candidate
                best_score = score

    _ITUNES_MATCH_MEMO[key] = best
    return best


def find_collection(track):
    match = find_itunes_track(track)
    if not match:
        return None
    item = match["item"]
    collection_id = item.get("collectionId")
    if not collection_id:
        return None
    return str(collection_id), match["country"]


def extract_yandex_urls(page):
    text = html_lib.unescape(str(page or "")).replace("\\/", "/")
    urls = []
    pattern = (
        r"https?://music\.yandex\.(?:ru|com)/"
        r"(?:album/\d+/track/\d+|track/\d+)"
        r"[^\"'<>\s]*"
    )
    for url in re.findall(pattern, text, flags=re.I):
        url = url.rstrip(".,);]")
        if url not in urls:
            urls.append(url)
    return urls


def songlink_title_matches(track, page):
    title = match_id(track.get("title", ""))
    if not title:
        return False
    text = match_id(html_lib.unescape(str(page or "")))
    return title in text or title_core(track.get("title", "")) in text


def check_yandex_track(track):
    source = find_itunes_track(track)
    if not source:
        return {
            "status": "uncertain",
            "score": 0.0,
            "url": "",
            "error": "точный iTunes-источник не найден",
        }

    item = source["item"]
    track_id = item.get("trackId")
    apple_url = repair_text(item.get("trackViewUrl", ""))
    if not track_id:
        return {
            "status": "uncertain",
            "score": source["quality"]["score"],
            "url": "",
            "error": "у подтверждённого iTunes-трека нет trackId",
        }

    resolver_url = SONGLINK_TRACK.format(track_id=track_id)
    last_error = None
    for attempt in range(1, SONGLINK_RETRIES + 1):
        try:
            response = get(
                resolver_url,
                headers={
                    "User-Agent": H["User-Agent"],
                    "Accept-Language": H["Accept-Language"],
                },
                timeout=25,
            )
            page = response.text
            if not songlink_title_matches(track, page):
                return {
                    "status": "uncertain",
                    "score": source["quality"]["score"],
                    "url": "",
                    "source_url": resolver_url,
                    "apple_url": apple_url,
                    "error": "Songlink не подтвердил название трека",
                }

            urls = extract_yandex_urls(page)
            if urls:
                return {
                    "status": "found",
                    "score": source["quality"]["score"],
                    "url": urls[0],
                    "source_url": resolver_url,
                    "apple_url": apple_url,
                    "itunes_track_id": str(track_id),
                    "matched_title": repair_text(item.get("trackName", "")),
                    "matched_artist": repair_text(item.get("artistName", "")),
                }

            return {
                "status": "not_confirmed",
                "score": source["quality"]["score"],
                "url": "",
                "source_url": resolver_url,
                "apple_url": apple_url,
                "itunes_track_id": str(track_id),
                "matched_title": repair_text(item.get("trackName", "")),
                "matched_artist": repair_text(item.get("artistName", "")),
            }
        except requests.RequestException as exc:
            last_error = exc
            if attempt < SONGLINK_RETRIES:
                time.sleep(attempt)

    return {
        "status": "error",
        "score": source["quality"]["score"],
        "url": "",
        "source_url": resolver_url,
        "apple_url": apple_url,
        "error": f"Songlink недоступен: {last_error}",
    }


def lookup_collection_labels(collections):
    result = {}
    by_country = {}
    for collection_id, country in collections:
        by_country.setdefault(country, []).append(collection_id)

    for country, ids in by_country.items():
        for start in range(0, len(ids), 50):
            chunk = ids[start:start + 50]
            try:
                data = get(
                    ITUNES_LOOKUP,
                    params={"id": ",".join(chunk), "entity": "album", "country": country},
                    timeout=20,
                ).json()
            except (requests.RequestException, ValueError):
                continue
            for item in data.get("results", []):
                collection_id = item.get("collectionId")
                if not collection_id:
                    continue
                label = repair_text(item.get("recordLabel", "")) or parse_label_copyright(item.get("copyright", ""))
                if label:
                    result[(str(collection_id), country)] = label
    return result


def cache_key(track):
    return f"{text_id(track.get('title', ''))}\x1f{text_id(track.get('artist', ''))}"


def cached_yandex(state, track, now_utc):
    entry = state.get("_yandex_cache", {}).get(cache_key(track))
    if not isinstance(entry, dict):
        return None
    status = entry.get("status")
    ttl = YANDEX_CACHE_SECONDS.get(status, 0)
    at = parse_utc(entry.get("at"))
    if not at or not ttl or (now_utc - at).total_seconds() > ttl:
        return None
    return dict(entry)


def store_yandex_cache(state, track, info, now_utc):
    cache = state.setdefault("_yandex_cache", {})
    entry = {
        "status": info.get("status", "error"),
        "score": info.get("score", 0.0),
        "url": info.get("url", ""),
        "matched_title": info.get("matched_title", ""),
        "matched_artist": info.get("matched_artist", ""),
        "source_url": info.get("source_url", ""),
        "apple_url": info.get("apple_url", ""),
        "itunes_track_id": info.get("itunes_track_id", ""),
        "at": now_utc.isoformat().replace("+00:00", "Z"),
    }
    if info.get("error"):
        entry["error"] = info["error"]
    cache[cache_key(track)] = entry
    if len(cache) > 1500:
        newest = sorted(
            cache.items(),
            key=lambda item: parse_utc(item[1].get("at"))
            or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )[:1200]
        state["_yandex_cache"] = dict(newest)


def cached_label(state, track, now_utc):
    entry = state.get("_label_cache", {}).get(cache_key(track))
    if not isinstance(entry, dict):
        return ""
    at = parse_utc(entry.get("at"))
    if not at or (now_utc - at).total_seconds() > LABEL_CACHE_SECONDS:
        return ""
    return repair_text(entry.get("label", ""))


def store_label_cache(state, track, label, now_utc):
    cache = state.setdefault("_label_cache", {})
    cache[cache_key(track)] = {
        "label": repair_text(label),
        "at": now_utc.isoformat().replace("+00:00", "Z"),
    }
    if len(cache) > 1000:
        newest = sorted(
            cache.items(),
            key=lambda item: parse_utc(item[1].get("at")) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )[:800]
        state["_label_cache"] = dict(newest)


def report_tracks(delta):
    tracks = []
    tracks.extend(track for _, _, track in delta["added"][:30])
    tracks.extend(track for _, _, track in delta["gone"][:20])
    tracks.extend(track for _, _, _, _, track in delta["moved"][:25])
    unique = {}
    for track in tracks:
        unique.setdefault(cache_key(track), track)
    return list(unique.values())


def enrich_report_labels(state, delta, now_utc):
    tracks = report_tracks(delta)
    unresolved = []
    for track in tracks:
        if track.get("label"):
            store_label_cache(state, track, track["label"], now_utc)
            continue
        cached = cached_label(state, track, now_utc)
        if cached:
            track["label"] = cached
        else:
            unresolved.append(track)

    if not unresolved:
        return

    found = {}
    workers = min(8, len(unresolved))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(find_collection, track): track for track in unresolved}
        for future in as_completed(futures):
            track = futures[future]
            try:
                collection = future.result()
            except Exception:
                collection = None
            if collection:
                found[cache_key(track)] = collection

    labels = lookup_collection_labels(sorted(set(found.values())))
    for track in unresolved:
        collection = found.get(cache_key(track))
        label = labels.get(collection, "") if collection else ""
        if label:
            track["label"] = label
            store_label_cache(state, track, label, now_utc)


def enrich_report_yandex(state, delta, now_utc):
    tracks = report_tracks(delta)
    result = {}
    unresolved = []

    for track in tracks:
        key = cache_key(track)
        cached = cached_yandex(state, track, now_utc)
        if cached:
            result[key] = cached
        else:
            unresolved.append(track)

    if unresolved:
        workers = min(4, len(unresolved))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(check_yandex_track, track): track for track in unresolved}
            for future in as_completed(futures):
                track = futures[future]
                key = cache_key(track)
                try:
                    info = future.result()
                except Exception as exc:
                    info = {
                        "status": "error",
                        "score": 0.0,
                        "url": "",
                        "error": str(exc),
                    }
                result[key] = info
                store_yandex_cache(state, track, info, now_utc)

    return result


def added_only_delta(delta):
    return {"added": list(delta.get("added", [])), "gone": [], "moved": []}


def select_new_without_yandex(delta, yandex_info):
    selected = []
    for entry in delta.get("added", []):
        _, _, track = entry
        info = (yandex_info or {}).get(cache_key(track), {})
        if info.get("status") in REPORT_YANDEX_STATUSES:
            selected.append(entry)
    return selected


def track_open_url(info):
    info = info or {}
    return repair_text(info.get("apple_url", "")) or repair_text(info.get("source_url", ""))


def report_new_without_yandex(name, added, now, yandex_info=None):
    lines = [
        f"🚨 {name}",
        f"Обнаружено: {now}",
        f"Новых треков без подтверждения в Яндекс Музыке: {len(added)}",
        "",
        "🆕 НОВЫЕ:",
    ]
    for p, _, track in added:
        info = (yandex_info or {}).get(cache_key(track), {})
        lines.append(f"#{p} {display_track(track, info)}")
        open_url = track_open_url(info)
        if open_url:
            lines.append(f"🔗 Открыть трек: {open_url}")
    return "\n".join(lines)


def parse_utc(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def recent_duplicate(state, fingerprint, now_utc):
    for event in state.get("_recent_events", []):
        if event.get("fingerprint") != fingerprint:
            continue
        at = parse_utc(event.get("at"))
        if at and (now_utc - at).total_seconds() <= DUPLICATE_WINDOW_SECONDS:
            return True
    return False


def remember_event(state, fingerprint, source, now_utc):
    recent = []
    for event in state.get("_recent_events", []):
        at = parse_utc(event.get("at"))
        if at and (now_utc - at).total_seconds() <= DUPLICATE_WINDOW_SECONDS:
            recent.append(event)
    recent.append(
        {
            "fingerprint": fingerprint,
            "source": source,
            "at": now_utc.isoformat().replace("+00:00", "Z"),
        }
    )
    state["_recent_events"] = recent[-10:]


def load_state():
    try:
        raw = json.loads(STATE.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        raw = {}

    state = dict(raw)
    dirty = False
    for name, value in list(state.items()):
        if name.startswith("_") or not isinstance(value, list):
            continue
        fixed = normalized_tracks(value)
        if fixed != value:
            state[name] = fixed
            dirty = True

    if "_last_check" in state:
        state.pop("_last_check", None)
        dirty = True
    if state.get("_schema_version") != SCHEMA_VERSION:
        state["_schema_version"] = SCHEMA_VERSION
        dirty = True
    return state, dirty


def needs_alert_baseline(state):
    return state.get("_alert_mode") != ALERT_MODE


def activate_alert_baseline(state, results, source_names, now_utc):
    for name in source_names:
        state[name] = results[name]
    state["_alert_mode"] = ALERT_MODE
    state["_baseline_at"] = now_utc.isoformat().replace("+00:00", "Z")
    state["_recent_events"] = []


def main():
    state, dirty = load_state()
    now_local = datetime.now(TZ).strftime("%d.%m.%Y %H:%M МСК")
    now_utc = datetime.now(timezone.utc)
    errors = []

    shazam_name = "Shazam Top 200 Russia"
    apple_name = "Apple Music — Shazam Charts Russia"
    results = {}
    initialize_baseline = needs_alert_baseline(state)

    try:
        results[shazam_name] = shazam()
    except Exception as exc:
        errors.append(f"{shazam_name}: {exc}")

    try:
        results[apple_name] = apple(results.get(shazam_name))
    except Exception as exc:
        errors.append(f"{apple_name}: {exc}")

    if results.get(shazam_name) and results.get(apple_name):
        enrich_metadata(results[shazam_name], results[apple_name])
        enrich_metadata(results[apple_name], results[shazam_name])

    if initialize_baseline and not errors and results.get(shazam_name) and results.get(apple_name):
        activate_alert_baseline(
            state,
            results,
            (shazam_name, apple_name),
            now_utc,
        )
        dirty = True
        print(f"Alert baseline initialized at {now_local}; no Telegram alert sent.")
    else:
        for name in (shazam_name, apple_name):
            current = results.get(name)
            if current is None:
                continue
            try:
                old = state.get(name)
                if old:
                    delta = make_delta(old, current)
                    if delta["added"]:
                        new_delta = added_only_delta(delta)
                        enrich_report_labels(state, new_delta, now_utc)
                        yandex_info = enrich_report_yandex(state, new_delta, now_utc)
                        selected = select_new_without_yandex(new_delta, yandex_info)

                        if selected:
                            filtered_delta = {"added": selected, "gone": [], "moved": []}
                            fingerprint = event_fingerprint(filtered_delta)
                            if recent_duplicate(state, fingerprint, now_utc):
                                print(f"Duplicate new-track alert suppressed for {name}")
                            else:
                                send(report_new_without_yandex(name, selected, now_local, yandex_info))
                                remember_event(state, fingerprint, name, now_utc)
                                dirty = True
                        else:
                            print(
                                f"{name}: {len(delta['added'])} new track(s), "
                                "but all were confirmed on Yandex or could not be classified as missing."
                            )
                if old != current:
                    state[name] = current
                    dirty = True
            except Exception as exc:
                errors.append(f"{name}: {exc}")

    for message in errors:
        print(message)
        try:
            send(f"⚠️ ошибка проверки, {now_local}\n{message}")
        except Exception as notify_exc:
            print(f"Telegram error notification failed: {notify_exc}")

    if dirty:
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", "utf-8")

    if errors:
        raise RuntimeError("; ".join(errors))


if __name__ == "__main__":
    main()
