"""Core flow shared by relay mode and webhook mode:
payload -> validate -> candles -> card + caption -> post to the target chat.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import market
from .caption import open_caption, update_caption
from .card import Brand, Status, render
from .signal import SignalError, parse_event, parse_signal, split_symbol
from .store import Store
from .telegram import Telegram

log = logging.getLogger(__name__)


@dataclass
class Config:
    token: str = ""
    target_chat: str = ""        # private group that receives the cards
    target_thread: int | None = None  # topic inside that group (t.me/c/<group>/<topic>)
    source_chat: str = ""        # channel the raw signals come from (relay mode)
    webhook_secret: str = ""
    out_dir: Path = Path("out")
    state_file: Path = Path("signals.json")
    dry_run: bool = False
    brand: Brand = field(default_factory=Brand)

    @classmethod
    def from_env(cls) -> "Config":
        e = os.environ.get
        return cls(
            token=e("TELEGRAM_BOT_TOKEN", ""),
            target_chat=e("TARGET_CHAT_ID", ""),
            target_thread=int(e("TARGET_THREAD_ID")) if e("TARGET_THREAD_ID") else None,
            source_chat=e("SOURCE_CHAT_ID", ""),
            webhook_secret=e("WEBHOOK_SECRET", ""),
            out_dir=Path(e("OUT_DIR", "out")),
            state_file=Path(e("STATE_FILE", "signals.json")),
            dry_run=e("DRY_RUN", "0") == "1",
            brand=Brand(
                name=e("BRAND_NAME", Brand.name),
                handle=e("BRAND_HANDLE", Brand.handle),
                disclaimer=e("BRAND_DISCLAIMER", Brand.disclaimer),
            ),
        )


class Service:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        cfg.out_dir.mkdir(parents=True, exist_ok=True)
        self.store = Store(cfg.state_file)
        self.tg = None if cfg.dry_run else Telegram(cfg.token)

    def handle(self, payload: dict) -> str:
        """Process one payload; returns a short log line. Raises SignalError."""
        event = parse_event(payload)
        if event == "open":
            return self._open(payload)
        return self._update(event, payload)

    def _post(self, png: Path, caption: str, reply_to: int | None = None) -> int | None:
        (png.with_suffix(".txt")).write_text(caption)
        if self.tg is None:
            return None
        return self.tg.send_photo(self.cfg.target_chat, png, caption, reply_to,
                                  self.cfg.target_thread)

    def _open(self, payload: dict) -> str:
        sig = parse_signal(payload)
        if self.store.has(sig.id):
            return f"duplicate {sig.id}, skipped"
        sig.number = self.store.next_number()
        data = market.candles(sig.base, sig.quote, sig.interval or "1H")
        png = self.cfg.out_dir / f"signal-{sig.number:04d}.png"
        render(sig, data, self.cfg.brand, png)
        msg = self._post(png, open_caption(sig, self.cfg.brand))
        self.store.add(sig, msg)
        return f"posted #{sig.number:04d} {sig.symbol} {sig.side} -> {png}"

    def _update(self, event: str, payload: dict) -> str:
        base, quote = "", None
        if payload.get("ticker"):
            try:
                base, quote = split_symbol(str(payload["ticker"]))
            except SignalError:
                base = str(payload["ticker"]).upper().lstrip("#$")
        found = self.store.find(str(payload.get("id", "") or ""), base, quote)
        if not found:
            raise SignalError(f"{event}: no open signal for id/ticker {payload.get('id') or base!r}")
        key, rec = found
        sig = self.store.signal(rec)

        if event == "tp":
            idx = int(payload.get("tp", rec["tp_hit"] + 1))
            if not 1 <= idx <= len(sig.tps):
                raise SignalError(f"tp index {idx} out of range 1..{len(sig.tps)}")
            if idx <= rec["tp_hit"]:
                return f"#{sig.number:04d} TP{idx} already reported, skipped"
            st = Status("tp", idx)
        elif event == "close":
            price = payload.get("price")
            st = Status("close", price=float(price) if price else None)
        else:
            st = Status(event)

        data = market.candles(sig.base, sig.quote, sig.interval or "1H")
        png = self.cfg.out_dir / f"signal-{sig.number:04d}-{event}{st.tp_index or ''}.png"
        render(sig, data, self.cfg.brand, png, status=st,
               when=datetime.now(timezone.utc))
        self._post(png, update_caption(sig, st, self.cfg.brand), rec["message_id"])

        done = (event != "tp") or st.tp_index == len(sig.tps)
        events = rec.get("events", []) + [{
            "kind": event, "tp": st.tp_index, "price": st.price,
            "at": datetime.now(timezone.utc).isoformat()}]
        self.store.update(key, tp_hit=max(rec["tp_hit"], st.tp_index),
                          state="closed" if done else "open", events=events)
        return f"update #{sig.number:04d} {event}{st.tp_index or ''} -> {png}"
