"""Telegram: send alerts (HTML) and read simple commands."""
import logging
import time
import requests
import config

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{}/{}"


def send(message: str) -> bool:
    url = API.format(config.TELEGRAM_BOT_TOKEN, "sendMessage")
    for chunk in [message[i:i + 4000] for i in range(0, len(message), 4000)] or [""]:
        for attempt in range(3):
            try:
                r = requests.post(url, json={"chat_id": config.TELEGRAM_CHAT_ID, "text": chunk,
                                             "parse_mode": "HTML",
                                             "disable_web_page_preview": True}, timeout=10)
                if r.ok:
                    break
                log.warning("Telegram error %s: %s", r.status_code, r.text[:200])
                if r.status_code == 429:
                    time.sleep(r.json().get("parameters", {}).get("retry_after", 5))
            except requests.RequestException as e:
                log.warning("Telegram network error: %s", e)
            time.sleep(2 * (attempt + 1))
        else:
            return False
    return True


def get_updates(offset=None):
    try:
        r = requests.get(API.format(config.TELEGRAM_BOT_TOKEN, "getUpdates"),
                         params={"offset": offset, "timeout": 0}, timeout=10)
        return r.json().get("result", []) if r.ok else []
    except Exception:
        return []
