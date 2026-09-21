import csv
import hashlib
import html as html_lib
import io
import json
import os
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
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
SCHEMA_VERSION = 2
DUPLICATE_WINDOW_SECONDS = 6 * 60 * 60


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


def display_track(track):
    title = repair_text(track.get("title", ""))
    artist = repair_text(track.get("artist", ""))
    return title + (f" — {artist}" if artist else "")


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


def get(url):
    r = requests.get(url, headers=H, timeout=35)
    r.raise_for_status()
    return r


def clean_track(title, artist=""):
    return {"title": repair_text(title), "artist": repair_text(artist)}


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
            out.append(clean_track(low["title"], low.get("artist", "")))
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
                out.append(clean_track(attrs.get("name"), attrs.get("artistName", "")))

        types = node.get("@type")
        if isinstance(types, str):
            types = [types]
        if isinstance(types, list) and any(t in ("MusicRecording", "Song") for t in types):
            title = node.get("name")
            artist = node.get("byArtist") or node.get("author")
            if not artist and isinstance(node.get("inAlbum"), dict):
                album = node["inAlbum"]
                artist = album.get("byArtist") or album.get("author")
            if title:
                out.append(clean_track(title, artist_name(artist)))

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
            item = clean_track(track.get("title", ""), track.get("artist", ""))
            by_title[title] = item
            ordered.append(item)
        elif not existing.get("artist") and track.get("artist"):
            existing["artist"] = repair_text(track["artist"])
    return ordered


def enrich_artists(tracks, fallback):
    if not fallback:
        return tracks

    counts = Counter(text_id(x.get("title", "")) for x in fallback)
    artists = {
        text_id(x.get("title", "")): repair_text(x.get("artist", ""))
        for x in fallback
        if counts[text_id(x.get("title", ""))] == 1 and x.get("artist")
    }
    for track in tracks:
        if not track.get("artist"):
            track["artist"] = artists.get(text_id(track.get("title", "")), "")
    return tracks


def apple(fallback=None):
    # Apple may omit charset in text/html; requests then treats UTF-8 as Latin-1.
    page = get(APPLE).content.decode("utf-8", errors="replace")
    candidates = []
    pattern = r'<script[^>]+type=["\']application/(?:ld\+json|json)["\'][^>]*>(.*?)</script>'
    for raw in re.findall(pattern, page, re.I | re.S):
        try:
            collect_apple_songs(json.loads(raw), candidates)
        except (json.JSONDecodeError, TypeError):
            continue

    out = merge_candidates(candidates)
    out = enrich_artists(out, fallback)
    if len(out) < 100:
        raise RuntimeError(f"получено только {len(out)} треков")
    artist_count = sum(bool(x.get("artist")) for x in out[:200])
    if artist_count < 100:
        raise RuntimeError(f"исполнитель распознан только у {artist_count} треков")
    return out[:200]


def normalized_tracks(items):
    if not isinstance(items, list):
        return items
    return [clean_track(x.get("title", ""), x.get("artist", "")) for x in items if isinstance(x, dict)]


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


def report(name, delta, now):
    added, gone, moved = delta["added"], delta["gone"], delta["moved"]
    lines = [
        f"🔄 {name}",
        f"Обнаружено: {now}",
        f"Новых: {len(added)} | Ушло: {len(gone)} | Сменили позицию: {len(moved)}",
    ]
    if added:
        lines += ["", "🆕 НОВЫЕ:"] + [f"#{p} {display_track(track)}" for p, _, track in added[:30]]
    if gone:
        lines += ["", "❌ УШЛИ:"] + [f"было #{p} {display_track(track)}" for p, _, track in gone[:20]]
    if moved:
        lines += ["", "📈 ИЗМЕНЕНИЯ ПОЗИЦИЙ:"] + [
            f"{'↑' if p < old_p else '↓'} #{p} {display_track(track)} (было #{old_p})"
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
    results = {}

    shazam_name = "Shazam Top 200 Russia"
    sources = [
        (shazam_name, lambda: shazam()),
        ("Apple Music — Shazam Charts Russia", lambda: apple(results.get(shazam_name))),
    ]

    for name, fetcher in sources:
        try:
            current = fetcher()
            results[name] = current
            old = state.get(name)

            if old:
                delta = make_delta(old, current)
                if has_chart_change(delta):
                    fingerprint = event_fingerprint(delta)
                    if recent_duplicate(state, fingerprint, now_utc):
                        print(f"Duplicate chart event suppressed for {name}")
                    else:
                        send(report(name, delta, now_local))
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
            message = f"{name}: {exc}"
            errors.append(message)
            print(message)
            try:
                send(f"⚠️ {name}: ошибка проверки, {now_local}\n{exc}")
            except Exception as notify_exc:
                errors.append(f"Telegram error notification failed for {name}: {notify_exc}")

    if dirty:
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", "utf-8")

    if errors:
        raise RuntimeError("; ".join(errors))


if __name__ == "__main__":
    main()
