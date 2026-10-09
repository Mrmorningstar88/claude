"""Minimal Telegram Bot API client (sendPhoto / sendMessage / getUpdates)."""
from __future__ import annotations

from pathlib import Path

import requests


class TelegramError(RuntimeError):
    pass


class Telegram:
    def __init__(self, token: str):
        if not token:
            raise TelegramError("TELEGRAM_BOT_TOKEN is not set")
        self.base = f"https://api.telegram.org/bot{token}"

    def _call(self, method: str, timeout: float = 30, **kw) -> dict:
        r = requests.post(f"{self.base}/{method}", timeout=timeout, **kw)
        try:
            body = r.json()
        except ValueError:
            raise TelegramError(f"{method}: HTTP {r.status_code}")
        if not body.get("ok"):
            raise TelegramError(f"{method}: {body.get('description', 'failed')}")
        return body["result"]

    def send_photo(self, chat_id: str, png: Path, caption: str,
                   reply_to: int | None = None, thread_id: int | None = None) -> int:
        data = {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}
        if thread_id:  # a topic inside a forum group
            data["message_thread_id"] = thread_id
        if reply_to:
            data["reply_to_message_id"] = reply_to
            data["allow_sending_without_reply"] = "true"
        with open(png, "rb") as f:
            res = self._call("sendPhoto", data=data, files={"photo": f})
        return res["message_id"]

    def get_updates(self, offset: int | None, timeout: int = 50) -> list[dict]:
        return self._call(
            "getUpdates", timeout=timeout + 10,
            json={"offset": offset, "timeout": timeout,
                  "allowed_updates": ["channel_post", "message"]})

    def me(self) -> dict:
        return self._call("getMe")
