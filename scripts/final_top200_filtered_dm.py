from datetime import datetime, timezone
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import monitor


BATCH = 25


def batch_entries(entries, size=BATCH):
    for start in range(0, len(entries), size):
        yield entries[start:start + size]


def main():
    now_utc = datetime.now(timezone.utc)
    now_local = datetime.now(monitor.TZ).strftime("%d.%m.%Y %H:%M МСК")
    tracks = monitor.apple()[:200]
    entries = [
        (pos, f"apple-top200-{pos}", track)
        for pos, track in enumerate(tracks, 1)
    ]

    temp_state = {}
    yandex_info = {}

    for chunk in batch_entries(entries):
        delta = {"added": chunk, "gone": [], "moved": []}
        yandex_info.update(
            monitor.enrich_report_yandex(temp_state, delta, now_utc)
        )

    verified_missing = []
    counts = {}
    for entry in entries:
        _, _, track = entry
        info = yandex_info.get(monitor.cache_key(track), {})
        status = info.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1
        if status == "verified_missing":
            verified_missing.append(entry)

    for chunk in batch_entries(verified_missing):
        delta = {"added": chunk, "gone": [], "moved": []}
        monitor.enrich_report_labels(temp_state, delta, now_utc)

    final = monitor.filter_non_major_entries(
        temp_state,
        "Apple Music — Final Top 200",
        verified_missing,
        now_utc,
        yandex_info,
    )

    print("TOTAL", len(entries))
    print("COUNTS", counts)
    print("MUSICFETCH_CONFIGURED", bool(monitor.musicfetch_token()))
    print("VERIFIED_MISSING", len(verified_missing))
    print("FINAL_NON_MAJOR", len(final))

    for pos, _, track in final:
        info = yandex_info.get(monitor.cache_key(track), {})
        print(
            f"FINAL #{pos} {track.get('title')} — {track.get('artist')} | "
            f"label={track.get('label','')} | distributor={info.get('distributor','')} | "
            f"isrc={info.get('isrc','')} | verification={info.get('verification','')} | "
            f"url_match={info.get('url_match',{})} | isrc_match={info.get('isrc_match',{})}"
        )

    if not final:
        return

    monitor.send(
        monitor.report_new_without_yandex(
            "Apple Music — Top 200",
            final,
            now_local,
            yandex_info,
        )
    )


if __name__ == "__main__":
    main()
