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
        for key in ("message", "edited_message", "callback_query"):
            obj = upd.get(key)
            if not isinstance(obj, dict):
                continue
            msg = obj.get("message", obj) if key == "callback_query" else obj
            chat = msg.get("chat", {}) if isinstance(msg, dict) else {}
            if chat.get("type") == "private":
                item = (
                    chat.get("id"),
                    chat.get("first_name") or "",
                    chat.get("username") or "",
                )
                if item not in found:
                    found.append(item)

    if not found:
        print("NO_PRIVATE_UPDATES")
        return

    for chat_id, first_name, username in found:
        print(f"PRIVATE chat_id={chat_id} first_name={first_name} username={username}")

if __name__ == "__main__":
    main()
