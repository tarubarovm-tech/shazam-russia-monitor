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
YANDEX_SEARCH = "https://music.yandex.ru/handlers/music-search.jsx"
SCHEMA_VERSION = 4
DUPLICATE_WINDOW_SECONDS = 6 * 60 * 60
LABEL_CACHE_SECONDS = 30 * 24 * 60 * 60
YANDEX_CACHE_SECONDS = {
    "found": 24 * 60 * 60,
    "not_found": 2 * 60 * 60,
    "uncertain": 30 * 60,
    "error": 10 * 60,
}
YANDEX_RETRIES = 3


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
    return re.sub(r"[^\w]+", " ", text_id(value), flags=re.UNICODE).strip()


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
        "not_found": "⚪ Яндекс: не найден",
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
    return SequenceMatcher(None, a, b).ratio()


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


def yandex_item_title(item):
    title = repair_text(item.get("title", ""))
    version = repair_text(item.get("version", ""))
    if version and match_id(version) not in match_id(title):
        return f"{title} ({version})"
    return title


def yandex_item_artists(item):
    result = []
    for artist in item.get("artists", []) if isinstance(item.get("artists"), list) else []:
        if isinstance(artist, dict) and artist.get("name"):
            result.append(repair_text(artist["name"]))
    return result


def yandex_title_score(expected, candidate):
    exact = similarity(expected, candidate)
    if match_id(expected) == match_id(candidate):
        return 1.0
    expected_core = title_core(expected)
    candidate_core = title_core(candidate)
    if expected_core and expected_core == candidate_core:
        return max(exact, 0.985)
    return exact


def yandex_artist_score(expected, actual_names):
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


def yandex_candidate_quality(track, item):
    candidate_title = yandex_item_title(item)
    candidate_artists = yandex_item_artists(item)
    title_score = yandex_title_score(track.get("title", ""), candidate_title)
    artist_score = yandex_artist_score(track.get("artist", ""), candidate_artists)

    if title_score >= 0.995 and artist_score >= 0.82:
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
        "matched_title": candidate_title,
        "matched_artist": ", ".join(candidate_artists),
    }


def yandex_track_url(item):
    track_id = item.get("id")
    albums = item.get("albums") if isinstance(item.get("albums"), list) else []
    album_id = albums[0].get("id") if albums and isinstance(albums[0], dict) else None
    if track_id and album_id:
        return f"https://music.yandex.ru/album/{album_id}/track/{track_id}"
    return ""


def yandex_search_results(query):
    last_error = None
    headers = dict(H)
    headers.update(
        {
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://music.yandex.ru/",
        }
    )
    for attempt in range(1, YANDEX_RETRIES + 1):
        try:
            response = get(
                YANDEX_SEARCH,
                params={"text": query, "page": 0},
                headers=headers,
                timeout=18,
            )
            data = response.json()
            tracks = data.get("tracks", {}) if isinstance(data, dict) else {}
            items = tracks.get("items", []) if isinstance(tracks, dict) else []
            if not isinstance(items, list):
                raise ValueError("Yandex Music returned an invalid tracks list")
            return [item for item in items if isinstance(item, dict)]
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < YANDEX_RETRIES:
                time.sleep(attempt)
    raise RuntimeError(f"Yandex Music search failed: {last_error}")


def yandex_queries(track):
    title = repair_text(track.get("title", ""))
    artist = repair_text(track.get("artist", ""))
    parts = artist_parts(artist)
    primary = parts[0] if parts else ""
    queries = [
        " ".join(x for x in (title, artist) if x),
        " ".join(x for x in (title, primary) if x),
        title,
    ]
    out = []
    for query in queries:
        query = query.strip()
        if query and query not in out:
            out.append(query)
    return out


def check_yandex_track(track):
    best_uncertain = None
    successful_queries = 0
    errors = []
    seen = set()

    for query in yandex_queries(track):
        try:
            items = yandex_search_results(query)
            successful_queries += 1
        except Exception as exc:
            errors.append(str(exc))
            continue

        for item in items:
            identity = str(item.get("id") or "") + "|" + yandex_item_title(item)
            if identity in seen:
                continue
            seen.add(identity)
            quality = yandex_candidate_quality(track, item)
            if quality["status"] == "reject":
                continue

            quality["url"] = yandex_track_url(item)
            if quality["status"] == "found":
                return quality

            if best_uncertain is None or quality["score"] > best_uncertain["score"]:
                best_uncertain = quality

    if best_uncertain:
        return best_uncertain
    if successful_queries:
        return {"status": "not_found", "score": 0.0, "url": ""}
    return {
        "status": "error",
        "score": 0.0,
        "url": "",
        "error": "; ".join(errors[-2:]) or "Yandex Music search unavailable",
    }


def find_collection(track):
    title = match_id(track.get("title", ""))
    if not title:
        return None
    artist = track.get("artist", "")
    term = " ".join(x for x in (track.get("title", ""), artist) if x)
    for country in ("ru", "us"):
        try:
            data = get(
                ITUNES_SEARCH,
                params={"term": term, "media": "music", "entity": "song", "country": country, "limit": 10},
                timeout=15,
            ).json()
        except (requests.RequestException, ValueError):
            continue
        for item in data.get("results", []):
            if match_id(item.get("trackName", "")) != title:
                continue
            if not artist_matches(artist, item.get("artistName", "")):
                continue
            collection_id = item.get("collectionId")
            if collection_id:
                return str(collection_id), country
    return None


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


def report(name, delta, now, yandex_info=None):
    added, gone, moved = delta["added"], delta["gone"], delta["moved"]
    lines = [
        f"🔄 {name}",
        f"Обнаружено: {now}",
        f"Новых: {len(added)} | Ушло: {len(gone)} | Сменили позицию: {len(moved)}",
    ]
    if added:
        lines += ["", "🆕 НОВЫЕ:"] + [f"#{p} {display_track(track, (yandex_info or {}).get(cache_key(track)))}" for p, _, track in added[:30]]
    if gone:
        lines += ["", "❌ УШЛИ:"] + [f"было #{p} {display_track(track, (yandex_info or {}).get(cache_key(track)))}" for p, _, track in gone[:20]]
    if moved:
        lines += ["", "📈 ИЗМЕНЕНИЯ ПОЗИЦИЙ:"] + [
            f"{'↑' if p < old_p else '↓'} #{p} {display_track(track, (yandex_info or {}).get(cache_key(track)))} (было #{old_p})"
            for _, p, old_p, _, track in moved[:25]
        ]
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


def main():
    state, dirty = load_state()
    now_local = datetime.now(TZ).strftime("%d.%m.%Y %H:%M МСК")
    now_utc = datetime.now(timezone.utc)
    errors = []

    shazam_name = "Shazam Top 200 Russia"
    apple_name = "Apple Music — Shazam Charts Russia"
    results = {}

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

    for name in (shazam_name, apple_name):
        current = results.get(name)
        if current is None:
            continue
        try:
            old = state.get(name)
            if old:
                delta = make_delta(old, current)
                if has_chart_change(delta):
                    fingerprint = event_fingerprint(delta)
                    if recent_duplicate(state, fingerprint, now_utc):
                        print(f"Duplicate chart event suppressed for {name}")
                    else:
                        enrich_report_labels(state, delta, now_utc)
                        yandex_info = enrich_report_yandex(state, delta, now_utc)
                        send(report(name, delta, now_local, yandex_info))
                        remember_event(state, fingerprint, name, now_utc)
                        dirty = True
            else:
                send(
                    f"📌 {name}: GitHub-монитор сохранил исходное состояние — "
                    f"{len(current)}/200, {now_local}."
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
