"""Optional: tells Andy the morning email is ready. Telegram is the simplest free option.

Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID as repo secrets to turn it on.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request


def send(text: str) -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return "skipped (no Telegram settings)"
    data = urllib.parse.urlencode({"chat_id": chat, "text": text, "disable_web_page_preview": "true"}).encode()
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=20) as r:
            ok = json.load(r).get("ok")
        return "sent" if ok else "failed"
    except Exception as e:
        return f"failed: {e}"
