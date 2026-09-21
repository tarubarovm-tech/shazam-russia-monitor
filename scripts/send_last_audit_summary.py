import os
import requests

def main():
    token = os.environ["BOT_TOKEN"].strip()
    chat_id = os.environ["CHAT_ID"].strip()
    text = """🧪 РЕЗУЛЬТАТ ПРОВЕРКИ APPLE TOP 200

Проверено: 200
🟡 Найдено на Яндексе: 79
🟠 Не удалось подтвердить: 19
🟠 Неоднозначно: 102
⚪ Verified missing: 0
✅ После non-major фильтра: 0

Итог: подходящих треков для отправки сейчас нет.

Важно: MUSICFETCH_TOKEN пока не настроен, поэтому строгая финальная проверка Apple↔Yandex по ISRC + score для неподтверждённых треков не выполняется."""
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        },
        timeout=30,
    )
    r.raise_for_status()

if __name__ == "__main__":
    main()
