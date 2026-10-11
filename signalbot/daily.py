"""Daily "signal of the day" for a second channel (e.g. @arutnews), in Russian.

Once a day, inside a fixed window (default 19:20–19:50 Asia/Almaty), the bot
reposts ONE real AFX signal that is still open: the most recent signal
opened that day that has not hit a target yet, else the most recent open one.
No AFX signal that day means no post: levels are never invented.

Posting is at most once per day. The day is marked as done just before the
photo is sent. The mark is cleared (so the window can retry) only when the
post certainly did not happen: a 4xx refusal from Telegram, or a connection
that never left this machine. A timeout, a dropped connection or a 5xx/gateway
answer is never retried, because the post may already be in the channel.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from html import escape
from zoneinfo import ZoneInfo

from . import market
from .card import Brand, render
from .signal import Signal
from .telegram import Telegram, TelegramError, TelegramNetworkError

log = logging.getLogger(__name__)


@dataclass
class Daily:
    chat: str                       # @arutnews or a numeric id
    token: str                      # bot that is admin in that channel
    at: str = "19:20"               # earliest post time, local
    latest: str = "19:50"           # no post after this, local
    tz: str = "Asia/Almaty"
    start: date | None = None       # local date of the first post; None = immediately
    cta: str = ""
    handle: str = ""
    disclaimer: str = "Не финансовый совет. Торговля связана с риском."


def _hm(s: str) -> tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


def validate(d: Daily) -> None:
    """Raise ValueError for settings that would silently never post."""
    for name, v in (("DAILY_TIME", d.at), ("DAILY_LATEST", d.latest)):
        try:
            h, m = _hm(v)
        except ValueError:
            raise ValueError(f"{name} must be HH:MM, got {v!r}") from None
        if not (0 <= h < 24 and 0 <= m < 60):
            raise ValueError(f"{name} is not a valid time: {v!r}")
    if _hm(d.at) > _hm(d.latest):
        raise ValueError(f"DAILY_TIME {d.at} is after DAILY_LATEST {d.latest}")
    ZoneInfo(d.tz)  # raises for an unknown time zone
    if not d.chat or not d.token:
        raise ValueError("DAILY_CHAT_ID and a bot token are required")


def pick(store, now_local: datetime) -> dict | None:
    """Today's most recent still-open signal, preferring ones with no TP hit yet."""
    day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    today = []
    for rec in store.data["signals"].values():
        if rec.get("state") != "open":
            continue
        created = datetime.fromisoformat(rec["signal"]["created"]).astimezone(now_local.tzinfo)
        if day_start <= created <= now_local:
            today.append((created, rec))
    if not today:
        return None
    fresh = [x for x in today if not x[1].get("tp_hit")]
    return max(fresh or today, key=lambda x: x[0])[1]


def _ladder(sig: Signal) -> str:
    rows = [(tp, f"ТЕЙК {i}", sig.pip_text(tp)) for i, tp in enumerate(sig.tps, 1)]
    entry = (f"{sig.fmt(sig.entry_low)}–{sig.fmt(sig.entry_high)}"
             if sig.is_zone else sig.fmt(sig.entry))
    rows.append((sig.entry, "ВХОД", ""))
    rows.append((sig.sl, "СТОП", sig.pip_text(sig.sl)))
    rows.sort(key=lambda r: -r[0])  # highest price first, like the card
    lines = []
    for price, name, dist in rows:
        shown = entry if name == "ВХОД" else sig.fmt(price)
        lines.append(f"{name:<7} {shown:>13}  {dist}".rstrip())
    return "<pre>" + escape("\n".join(lines)) + "</pre>"


def caption(sig: Signal, d: Daily) -> str:
    sig.lang = "ru"
    side = "🟢 ПОКУПКА" if sig.is_long else "🔴 ПРОДАЖА"
    head = f"<b>Сделка дня из сообщества AFX</b>\n{side} <b>{escape(sig.symbol)}</b>"
    if sig.interval:
        head += f" · {escape(sig.interval)}"
    parts = [head, _ladder(sig),
             f"Риск/прибыль <b>1 : {sig.rr:.1f}</b> · риск {sig.pip_text(sig.sl, sign=False)}"]
    if d.cta:
        parts.append(escape(d.cta))
    parts.append(f"<i>{escape(d.disclaimer)}</i>")
    return "\n\n".join(parts)


def prepare(svc, d: Daily, now_local: datetime):
    """Pick and render today's post. Returns (record, png, caption) or None."""
    rec = pick(svc.store, now_local)
    if rec is None:
        return None
    sig = Signal.from_dict(rec["signal"])
    sig.lang = "ru"
    data = market.candles(sig.base, sig.quote, sig.interval or "1H")
    png = svc.cfg.out_dir / f"daily-{now_local:%Y-%m-%d}.png"
    brand = Brand(name=svc.cfg.brand.name, handle=d.handle or svc.cfg.brand.handle,
                  disclaimer=d.disclaimer)
    render(sig, data, brand, png, lang="ru", tz=d.tz)
    text = caption(sig, d)
    png.with_suffix(".txt").write_text(text)
    return rec, png, text


def post_daily(svc, d: Daily, now_local: datetime, send: bool = True) -> str:
    key = f"{now_local:%Y-%m-%d}"
    done = svc.store.get_meta("daily_done", {})
    if key in done:
        if done[key].get("message_id") is None:
            return (f"daily {key}: #{done[key]['number']:04d} send outcome unknown "
                    "(not retried, to avoid a double post)")
        return f"daily {key}: already posted #{done[key]['number']:04d}, skipped"
    prepared = prepare(svc, d, now_local)
    if prepared is None:
        return f"daily {key}: no open AFX signal today yet, nothing posted"
    rec, png, text = prepared
    number = rec["signal"]["number"]
    if not send:
        return f"daily {key}: rendered #{number:04d} -> {png}"
    svc.store.set_meta("daily_done", {**done, key: {"number": number, "message_id": None}})
    try:
        msg = Telegram(d.token).send_photo(d.chat, png, text)
    except TelegramError as e:
        if e.refused:  # definite 4xx refusal: nothing was posted, allow a retry
            svc.store.set_meta("daily_done", done)
        raise
    except TelegramNetworkError as e:
        if e.not_sent:  # the request never left this machine
            svc.store.set_meta("daily_done", done)
        raise
    svc.store.set_meta("daily_done", {**done, key: {"number": number, "message_id": msg}})
    return f"daily {key}: posted #{number:04d} to {d.chat} (message {msg})"


def start_daily(svc, d: Daily) -> threading.Thread:
    tz = ZoneInfo(d.tz)
    first, last = _hm(d.at), _hm(d.latest)

    def loop():
        waiting_logged = None
        while True:
            try:
                now = datetime.now(timezone.utc).astimezone(tz)
                key = f"{now:%Y-%m-%d}"
                in_window = first <= (now.hour, now.minute) <= last
                started = d.start is None or now.date() >= d.start
                if in_window and started and key not in svc.store.get_meta("daily_done", {}):
                    result = post_daily(svc, d, now)
                    if "nothing posted" in result:
                        if waiting_logged != key:
                            log.info(result + "; will keep checking until %s", d.latest)
                            waiting_logged = key
                    else:
                        log.info(result)
            except (TelegramError, TelegramNetworkError) as e:
                retry = getattr(e, "refused", False) or getattr(e, "not_sent", False)
                if retry:
                    log.warning("daily post not sent (will retry in window): %s", e)
                else:
                    log.error("daily post outcome unknown, not retried today "
                              "(check the channel): %s", e)
            except Exception:
                # Before the send (data, render) the day is not marked, so the next
                # tick retries; during the send it is marked, so it is not repeated.
                log.exception("daily post failed")
            time.sleep(30)

    t = threading.Thread(target=loop, daemon=True, name="daily")
    t.start()
    log.info("daily signal scheduled: %s–%s %s -> %s%s", d.at, d.latest, d.tz, d.chat,
             f" from {d.start}" if d.start else "")
    return t
