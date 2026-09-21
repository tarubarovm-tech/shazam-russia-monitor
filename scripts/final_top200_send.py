from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import monitor


def main():
    now_utc = datetime.now(timezone.utc)
    now_local = datetime.now(monitor.TZ).strftime("%d.%m.%Y %H:%M МСК")

    tracks = monitor.apple()[:200]
    entries = [(pos, f"apple-top200-{pos}", track) for pos, track in enumerate(tracks, 1)]

    yandex_info = {}
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {
            pool.submit(monitor.check_yandex_track, track): (pos, key, track)
            for pos, key, track in entries
        }
        for future in as_completed(futures):
            pos, key, track = futures[future]
            try:
                info = future.result()
            except Exception as exc:
                info = {
                    "status": "error",
                    "score": 0.0,
                    "url": "",
                    "error": str(exc),
                }
            yandex_info[monitor.cache_key(track)] = info
            print(
                f"#{pos} {track.get('title')} — {track.get('artist')} | "
                f"status={info.get('status')} | "
                f"title_score={info.get('url_match',{}).get('title_score','')} | "
                f"artist_score={info.get('url_match',{}).get('artist_score','')} | "
                f"verification={info.get('verification','')} | "
                f"error={info.get('error','')}"
            )

    counts = {}
    for info in yandex_info.values():
        status = info.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1

    verified = [
        entry
        for entry in entries
        if yandex_info.get(monitor.cache_key(entry[2]), {}).get("status")
        == "verified_missing"
    ]

    temp_state = {}
    if verified:
        verified_delta = {"added": verified, "gone": [], "moved": []}
        monitor.enrich_report_labels(temp_state, verified_delta, now_utc)

    final = monitor.filter_non_major_entries(
        temp_state,
        "Apple Music — Final Top 200",
        verified,
        now_utc,
        yandex_info,
    )

    print("=== FINAL SUMMARY ===")
    print("TOTAL", len(tracks))
    print("COUNTS", counts)
    print("VERIFIED_MISSING", len(verified))
    print("FINAL_NON_MAJOR", len(final))
    print("MUSICFETCH_CONFIGURED", bool(monitor.musicfetch_token()))

    if not final:
        print("NO_TELEGRAM_MESSAGE")
        return

    lines = [
        "✅ НЕТ НА ЯНДЕКСЕ + NON-MAJOR",
        f"Проверено Apple Top 200: {len(tracks)}",
        f"Подходящих: {len(final)}",
        f"Время: {now_local}",
        "",
    ]
    for pos, _, track in final:
        info = yandex_info.get(monitor.cache_key(track), {})
        lines.append(f"#{pos} {monitor.display_track(track, info)}")
        url_match = info.get("url_match", {})
        isrc_match = info.get("isrc_match", {})
        if url_match:
            lines.append(
                "score URL: "
                f"title={url_match.get('title_score','?')} "
                f"artist={url_match.get('artist_score','?')}"
            )
        if isrc_match:
            lines.append(
                "score ISRC: "
                f"title={isrc_match.get('title_score','?')} "
                f"artist={isrc_match.get('artist_score','?')}"
            )
        open_url = monitor.track_open_url(info)
        if open_url:
            lines.append(f"🔗 {open_url}")
        lines.append("")

    monitor.send("\n".join(lines))


if __name__ == "__main__":
    main()
