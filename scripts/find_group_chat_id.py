import os
import requests

TARGET = "ваававфы"

def main():
    token = os.environ["BOT_TOKEN"].strip()
    r = requests.get(
        f"https://api.telegram.org/bot{token}/getUpdates",
        params={"limit": 100, "timeout": 0},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()

    matches = []
    for upd in data.get("result", []):
        msg = upd.get("message") or upd.get("channel_post") or {}
        text = msg.get("text", "")
        chat = msg.get("chat", {})
        if text == TARGET:
            matches.append({
                "chat_id": chat.get("id"),
                "type": chat.get("type"),
                "title": chat.get("title") or "",
            })

    if not matches:
        print("MATCH_NOT_FOUND")
        return

    for item in matches:
        print(
            f"MATCH chat_id={item['chat_id']} "
            f"type={item['type']} "
            f"title={item['title']}"
        )

if __name__ == "__main__":
    main()
