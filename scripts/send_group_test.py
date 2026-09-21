import os
import requests

def main():
    token = os.environ["BOT_TOKEN"].strip()
    chat_id = "-1003292773382"
    text = "дима сосал"
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id": chat_id, "text": text},
        timeout=30,
    )
    r.raise_for_status()

if __name__ == "__main__":
    main()
