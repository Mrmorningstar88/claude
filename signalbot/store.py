"""JSON-file store: signal numbering, open signals and their Telegram message ids."""
from __future__ import annotations

import base64
import json
import os
import threading
from pathlib import Path

from .signal import Signal


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.data = {"next": 1, "signals": {}}
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
        elif os.environ.get("STATE_SEED_B64"):
            # One-time carry-over of numbering / open trades onto a fresh host.
            self.data = json.loads(base64.b64decode(os.environ["STATE_SEED_B64"]))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._save()

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2))
        tmp.replace(self.path)

    def add(self, sig: Signal, message_id: int | None) -> None:
        with self.lock:
            key = sig.id or f"auto-{sig.number}"
            self.data["signals"][key] = {
                "signal": sig.to_dict(), "message_id": message_id,
                "state": "open", "tp_hit": 0,
            }
            self._save()

    def next_number(self) -> int:
        with self.lock:
            n = self.data["next"]
            self.data["next"] = n + 1
            self._save()
            return n

    def has(self, sig_id: str) -> bool:
        return bool(sig_id) and sig_id in self.data["signals"]

    def find(self, sig_id: str = "", base: str = "", quote: str | None = None
             ) -> tuple[str, dict] | None:
        """By id, else the most recent still-open signal on the same instrument."""
        s = self.data["signals"]
        if sig_id and sig_id in s:
            return sig_id, s[sig_id]
        if base:
            for key, rec in reversed(list(s.items())):
                sg = rec["signal"]
                if rec["state"] == "open" and sg["base"] == base and \
                        (quote is None or sg["quote"] == quote):
                    return key, rec
        return None

    def update(self, key: str, **fields) -> None:
        with self.lock:
            self.data["signals"][key].update(fields)
            self._save()

    def get_meta(self, key: str, default=None):
        return self.data.get("meta", {}).get(key, default)

    def set_meta(self, key: str, value) -> None:
        with self.lock:
            self.data.setdefault("meta", {})[key] = value
            self._save()

    @staticmethod
    def signal(rec: dict) -> Signal:
        return Signal.from_dict(rec["signal"])
