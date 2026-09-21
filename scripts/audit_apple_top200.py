from datetime import datetime, timezone
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import monitor


def main():
    now_utc = datetime.now(timezone.utc)
    tracks = monitor.apple()[:200]
    delta = {
        "added": [(pos, f"apple-top200-{pos}", track) for pos, track in enumerate(tracks, 1)],
        "gone": [],
        "moved": [],
    }

    temp_state = {}
    yandex_info = {}

    for start in range(0, len(delta["added"]), 30):
        chunk = {
            "added": delta["added"][start:start + 30],
            "gone": [],
            "moved": [],
        }
        monitor.enrich_report_labels(temp_state, chunk, now_utc)
        yandex_info.update(
            monitor.enrich_report_yandex(temp_state, chunk, now_utc)
        )

    counts = {}
    verified_missing = []
    not_confirmed = []
    uncertain = []
    found = []
    error = []

    for entry in delta["added"]:
        pos, _, track = entry
        info = yandex_info.get(monitor.cache_key(track), {})
        status = info.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1
        if status == "verified_missing":
            verified_missing.append(entry)
        elif status == "not_confirmed":
            not_confirmed.append(entry)
        elif status == "uncertain":
            uncertain.append(entry)
        elif status == "found":
            found.append(entry)
        elif status == "error":
            error.append(entry)

    final = monitor.filter_non_major_entries(
        temp_state,
        "Apple Music — Top 200 strict audit",
        verified_missing,
        now_utc,
        yandex_info,
    )

    candidate_non_major = monitor.filter_non_major_entries(
        temp_state,
        "Apple Music — Top 200 candidate audit",
        not_confirmed,
        now_utc,
        yandex_info,
    )

    print("=== SUMMARY ===")
    print("TOTAL", len(tracks))
    print("COUNTS", counts)
    print("VERIFIED_MISSING_BEFORE_MAJOR", len(verified_missing))
    print("FINAL_AFTER_MAJOR", len(final))
    print("NOT_CONFIRMED_NON_MAJOR_CANDIDATES", len(candidate_non_major))
    print("MUSICFETCH_CONFIGURED", bool(monitor.musicfetch_token()))

    print("\n=== FINAL VERIFIED MISSING ===")
    for pos, _, track in final:
        info = yandex_info.get(monitor.cache_key(track), {})
        print(
            f"FINAL #{pos} {track.get('title')} — {track.get('artist')} | "
            f"label={track.get('label','')} | distributor={info.get('distributor','')} | "
            f"isrc={info.get('isrc','')} | verification={info.get('verification','')} | "
            f"url_match={info.get('url_match',{})} | isrc_match={info.get('isrc_match',{})}"
        )

    print("\n=== TOP NOT-CONFIRMED NON-MAJOR CANDIDATES ===")
    for pos, _, track in candidate_non_major[:40]:
        info = yandex_info.get(monitor.cache_key(track), {})
        print(
            f"CANDIDATE #{pos} {track.get('title')} — {track.get('artist')} | "
            f"label={track.get('label','')} | error={info.get('error','')} | "
            f"apple={info.get('apple_url','')} | songlink={info.get('source_url','')}"
        )

    print("\n=== UNCERTAIN SAMPLE ===")
    for pos, _, track in uncertain[:20]:
        info = yandex_info.get(monitor.cache_key(track), {})
        print(
            f"UNCERTAIN #{pos} {track.get('title')} — {track.get('artist')} | "
            f"label={track.get('label','')} | error={info.get('error','')}"
        )


if __name__ == "__main__":
    main()
