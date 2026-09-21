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

    not_on_yandex = monitor.select_new_without_yandex(delta, yandex_info)
    selected = monitor.filter_non_major_entries(
        temp_state,
        "Apple Music — Top 20 test",
        not_on_yandex,
        now_utc,
    )

    lines = [
        "🧪 ТЕСТ APPLE TOP 20",
        f"Проверено: {len(tracks)}",
        f"Яндекс не подтверждён: {len(not_on_yandex)}",
        f"После major-фильтра: {len(selected)}",
        f"Время: {now_local}",
    ]

    if selected:
        lines += ["", "✅ ОТПРАВЛЯЕМ:"]
        for pos, _, track in selected:
            info = yandex_info.get(monitor.cache_key(track), {})
            lines.append(f"#{pos} {monitor.display_track(track, info)}")
            url = monitor.track_open_url(info)
            if url:
                lines.append(f"🔗 {url}")
    else:
        lines += ["", "Подходящих треков среди первых 20 сейчас нет."]

    monitor.send("\n".join(lines))


if __name__ == "__main__":
    main()
