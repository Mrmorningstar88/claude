"""Minimal Telegram Bot API client (sendPhoto / sendMessage / getUpdates)."""
from __future__ import annotations

import re
from pathlib import Path

import requests

TOKEN_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]{20,}")

# Signs that a request never left this machine (so it certainly was not posted).
_NOT_SENT = ("NameResolutionError", "Failed to resolve", "NewConnectionError",
             "Failed to establish a new connection", "Unable to connect to proxy")


def redact(text: str) -> str:
    return TOKEN_RE.sub("bot<redacted>", text)


class TelegramError(RuntimeError):
    """Telegram answered but did not accept the call."""

    def __init__(self, msg: str, status: int | None = None, error_code: int | None = None):
        super().__init__(msg)
        self.status = status
        self.error_code = error_code

    @property
    def refused(self) -> bool:
        """A definite 4xx rejection: nothing was posted, a retry is safe.
        5xx and non-JSON answers (gateway errors) may hide a delivered post."""
        return self.error_code is not None and 400 <= self.error_code < 500


class TelegramNetworkError(RuntimeError):
    """No answer from Telegram. Message is token-free."""

    def __init__(self, msg: str, not_sent: bool):
        super().__init__(msg)
        self.not_sent = not_sent


class Telegram:
    def __init__(self, token: str):
        if not token:
            raise TelegramError("TELEGRAM_BOT_TOKEN is not set")
        self.base = f"https://api.telegram.org/bot{token}"

    def _call(self, method: str, timeout: float = 30, **kw) -> dict:
        try:
            r = requests.post(f"{self.base}/{method}", timeout=timeout, **kw)
        except requests.RequestException as e:
            # requests puts the full URL, token included, into its messages.
            text = redact(f"{type(e).__name__}: {e}")
            not_sent = isinstance(e, requests.exceptions.ConnectTimeout) or \
                (isinstance(e, requests.exceptions.ConnectionError)
                 and any(k in text for k in _NOT_SENT))
            raise TelegramNetworkError(f"{method}: {text}", not_sent) from None
        try:
            body = r.json()
        except ValueError:
            raise TelegramError(f"{method}: HTTP {r.status_code}", status=r.status_code)
        if not body.get("ok"):
            raise TelegramError(f"{method}: {body.get('description', 'failed')}",
                                status=r.status_code, error_code=body.get("error_code"))
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
