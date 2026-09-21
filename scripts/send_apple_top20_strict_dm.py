from datetime import datetime, timezone
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import monitor


def main():
    now_utc = datetime.now(timezone.utc)
    now_local = datetime.now(monitor.TZ).strftime("%d.%m.%Y %H:%M МСК")

    tracks = monitor.apple()[:20]
    delta = {
        "added": [(pos, f"apple-top20-{pos}", track) for pos, track in enumerate(tracks, 1)],
        "gone": [],
        "moved": [],
    }

    temp_state = {}
    monitor.enrich_report_labels(temp_state, delta, now_utc)
    yandex_info = monitor.enrich_report_yandex(temp_state, delta, now_utc)

    verified_missing = monitor.select_new_without_yandex(delta, yandex_info)
    selected = monitor.filter_non_major_entries(
        temp_state,
        "Apple Music — Top 20 strict test",
        verified_missing,
        now_utc,
        yandex_info,
    )

    found = 0
    inconclusive = 0
    for pos, _, track in delta["added"]:
        info = yandex_info.get(monitor.cache_key(track), {})
        status = info.get("status", "unknown")
        if status == "found":
            found += 1
        elif status != "verified_missing":
            inconclusive += 1
        print(
            f"#{pos} {track.get('title')} — {track.get('artist')} | "
            f"label={track.get('label','')} | status={status} | "
            f"verification={info.get('verification','')} | "
            f"isrc={info.get('isrc','')} | distributor={info.get('distributor','')} | "
            f"url_match={info.get('url_match',{})} | "
            f"isrc_match={info.get('isrc_match',{})} | "
            f"yandex={info.get('url','')} | error={info.get('error','')}"
        )

    lines = [
        "🧪 STRICT APPLE TOP 20",
        f"Проверено: {len(tracks)}",
        f"Яндекс подтверждён: {found}",
        f"Verified missing: {len(verified_missing)}",
        f"Неоднозначно/недоступно: {inconclusive}",
        f"После major-фильтра: {len(selected)}",
        f"Время: {now_local}",
    ]

    if selected:
        lines += ["", "✅ ОТПРАВЛЯЕМ:"]
        for pos, _, track in selected:
            info = yandex_info.get(monitor.cache_key(track), {})
            lines.append(f"#{pos} {monitor.display_track(track, info)}")
            lines.append(
                "match "
                f"text={info.get('url_match',{}).get('title_text','?')}/"
                f"{info.get('url_match',{}).get('artist_text','?')} "
                f"latin={info.get('url_match',{}).get('title_latin','?')}/"
                f"{info.get('url_match',{}).get('artist_latin','?')}"
            )
            url = monitor.track_open_url(info)
            if url:
                lines.append(f"🔗 {url}")
    else:
        lines += ["", "Подходящих треков среди первых 20 сейчас нет."]

    monitor.send("\n".join(lines))


if __name__ == "__main__":
    main()
