import os
import requests

PRIVATE_CHAT_ID = "710097553"

MOCK_MESSAGE = """🧪 ТЕСТ / СИМУЛЯЦИЯ

✅ ПОДХОДЯЩИЙ ТРЕК
#47 Agromoza — Raigañu
🏷 Label: Raigañu / Musicasturiana.com
Yandex match: title=0.93 · artist=0.74
🔗 Spotify: https://open.spotify.com/track/5xTAyq5zGggEZUftVmv1dz

Смоделированный алерт для проверки формата."""

def main():
    token = os.environ["BOT_TOKEN"].strip()
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={
            "chat_id": PRIVATE_CHAT_ID,
            "text": MOCK_MESSAGE,
            "disable_web_page_preview": "true",
        },
        timeout=30,
    )
    r.raise_for_status()

if __name__ == "__main__":
    main()
