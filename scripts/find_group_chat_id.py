import os
import requests

def main():
    token = os.environ["BOT_TOKEN"].strip()
    r = requests.get(
        f"https://api.telegram.org/bot{token}/getUpdates",
        params={"limit": 100, "timeout": 0},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()

    found = []
    for upd in data.get("result", []):
        msg = (
            upd.get("message")
            or upd.get("channel_post")
            or upd.get("my_chat_member")
            or {}
        )
        chat = msg.get("chat", {}) if isinstance(msg, dict) else {}
        if chat.get("type") in ("group", "supergroup"):
            item = (chat.get("id"), chat.get("type"), chat.get("title") or "")
            if item not in found:
                found.append(item)

    if not found:
        print("NO_GROUP_UPDATES")
        return

    for chat_id, chat_type, title in found:
        print(f"GROUP chat_id={chat_id} type={chat_type} title={title}")

if __name__ == "__main__":
    main()
