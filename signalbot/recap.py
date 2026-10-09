"""Weekly results recap: totals every signal opened in the week, from the
bot's own records (the signal post and the TP/SL/close/cancel updates).

Result rules
- Final TP hit            -> that TP.
- SL hit, no TP before    -> loss (-1R).
- SL hit after a TP       -> RECAP_SL_AFTER_TP: "breakeven" (default, 0),
                             "best_tp" (highest TP reached) or "loss" (-1R).
- Closed at a price       -> pips to that price; closed with no price -> highest
                             TP reached, else breakeven.
- Cancelled               -> not counted.
- Still open              -> listed as running, not counted.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

from .card import (BG, BORDER, DIM, DOWN, F, MUTED, PANEL, TEXT, UP, AMBER,
                   W, Brand, Canvas)
from .signal import Signal

log = logging.getLogger(__name__)
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@dataclass
class Row:
    sig: Signal
    label: str
    status: str               # closed | running | cancelled
    pips: float | None = None
    r: float | None = None
    closed_at: str = ""


def outcome(rec: dict, sl_after_tp: str = "breakeven") -> Row:
    sig = Signal.from_dict(rec["signal"])
    hit = rec.get("tp_hit", 0)
    events = rec.get("events", [])
    last = events[-1]["kind"] if events else None

    when = events[-1]["at"] if events else ""

    def at(price):
        return (sig.pips(price),
                sig.r_multiple(price) * (1 if sig.pips(price) >= 0 else -1), when)

    if rec.get("state") == "open" or last is None:
        return Row(sig, f"TP{hit} · running" if hit else "running", "running")
    if last == "cancel":
        return Row(sig, "cancelled", "cancelled")
    if last == "tp":
        return Row(sig, f"TP{hit}", "closed", *at(sig.tps[hit - 1]))
    if last == "sl":
        if not hit or sl_after_tp == "loss":
            return Row(sig, "SL" if not hit else f"SL after TP{hit}", "closed", *at(sig.sl))
        if sl_after_tp == "best_tp":
            return Row(sig, f"TP{hit}", "closed", *at(sig.tps[hit - 1]))
        return Row(sig, f"BE after TP{hit}", "closed", 0.0, 0.0, when)
    # close
    price = events[-1].get("price")
    if price:
        return Row(sig, "closed", "closed", *at(float(price)))
    if hit:
        return Row(sig, f"closed · TP{hit}", "closed", *at(sig.tps[hit - 1]))
    return Row(sig, "closed · BE", "closed", 0.0, 0.0, when)


def week_bounds(now_local: datetime) -> tuple[datetime, datetime]:
    start = (now_local - timedelta(days=now_local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=7)


@dataclass
class Recap:
    start: datetime
    end: datetime
    rows: list[Row]

    @property
    def closed(self):
        return [r for r in self.rows if r.status == "closed"]

    @property
    def running(self):
        return [r for r in self.rows if r.status == "running"]

    @property
    def wins(self):
        return sum(1 for r in self.closed if r.r > 0)

    @property
    def losses(self):
        return sum(1 for r in self.closed if r.r < 0)

    @property
    def breakeven(self):
        return sum(1 for r in self.closed if r.r == 0)

    @property
    def total_pips(self):
        return sum(r.pips for r in self.closed)

    @property
    def total_r(self):
        return sum(r.r for r in self.closed)

    @property
    def win_rate(self):
        return self.wins / len(self.closed) * 100 if self.closed else 0.0

    @property
    def week_label(self):
        last = self.end - timedelta(days=1)
        return (f"WEEK {self.start.isocalendar()[1]} · {self.start:%d %b} – "
                f"{last:%d %b %Y}").upper()


def build(store, now_local: datetime, sl_after_tp: str = "breakeven") -> Recap:
    start, end = week_bounds(now_local)
    rows = []
    for rec in store.data["signals"].values():
        created = datetime.fromisoformat(rec["signal"]["created"]).astimezone(now_local.tzinfo)
        if start <= created < end:
            rows.append(outcome(rec, sl_after_tp))
    rows.sort(key=lambda r: r.sig.number)
    return Recap(start, end, [r for r in rows if r.status != "cancelled"])


def _signed(v: float, fmt: str = ",.0f") -> str:
    return f"{v:+{fmt}}".replace("-", "−")


def _pips_str(v: float) -> str:
    if abs(v) < 0.05:
        return "0"
    return _signed(v, ",.0f" if abs(v - round(v)) < 0.05 else ",.1f")


# --- card ---------------------------------------------------------------------
def render(rc: Recap, brand: Brand, out: str | Path, note: str) -> Path:
    c = Canvas()
    col = UP if rc.total_r >= 0 else DOWN
    for gy in range(20, 1350, 36):
        for gx in range(20, W, 36):
            c.rect((gx - 1, gy - 1, gx + 1, gy + 1), fill=(22, 28, 46))
    c.glow((W - 120, 60), 520, col, 60)

    c.rect((56, 48, 94, 86), fill=col, r=10)
    c.text((75, 67), brand.name[:1].upper(), F["bold"](22), BG, anchor="mm")
    c.text((108, 67), brand.name.upper(), F["bold"](24), TEXT, anchor="lm")
    c.text((W - 56, 56), "WEEKLY RECAP", F["monob"](22), TEXT, anchor="ra")
    c.text((W - 56, 88), rc.week_label, F["mono"](17), MUTED, anchor="ra")

    # headline number
    big = F["bold"](112)
    num = _pips_str(rc.total_pips)
    c.text((52, 122), num, big, col)
    c.text((52 + c.tlen(num, big) + 16, 236), "pips", F["med"](40), DIM, anchor="ls")
    c.text((56, 284), f"{_signed(rc.total_r, '.1f')}R net  ·  {len(rc.closed)} closed"
           + (f"  ·  {len(rc.running)} running" if rc.running else ""),
           F["semi"](24), MUTED, anchor="lm")

    # tiles
    tiles = [("SIGNALS", str(len(rc.rows))), ("WIN RATE", f"{rc.win_rate:.0f}%"),
             ("W / L / BE", f"{rc.wins} / {rc.losses} / {rc.breakeven}"),
             ("AVG PER TRADE", f"{_signed(rc.total_r / len(rc.closed), '.2f')}R"
              if rc.closed else "—")]
    tw = (W - 112 - 36) / 4
    for i, (label, val) in enumerate(tiles):
        x0 = 56 + i * (tw + 12)
        c.rect((x0, 326, x0 + tw, 420), fill=PANEL, outline=BORDER, r=14)
        c.text((x0 + 18, 344), label, F["semi"](15), MUTED)
        c.text((x0 + 18, 402), val, F["bold"](32), TEXT, anchor="ls")

    # trade list
    max_rows = 12
    rows = rc.rows[:max_rows]
    top, limit = 446, 1236
    hy = top + 32
    rh = min(58, (limit - hy - 40 - (30 if len(rc.rows) > max_rows else 0)) / max(len(rows), 1))
    bottom = min(limit, hy + 40 + len(rows) * rh + (44 if len(rc.rows) > max_rows else 14))
    c.rect((56, top, W - 56, bottom), fill=PANEL, outline=BORDER, r=20)
    fh = F["semi"](15)
    for x, label, a in ((84, "#", "lm"), (180, "PAIR", "lm"), (360, "SIDE", "lm"),
                        (470, "RESULT", "lm"), (760, "PIPS", "rm"), (884, "R", "mm")):
        c.text((x, hy), label, fh, MUTED, anchor=a)
    c.line([(76, hy + 22), (W - 76, hy + 22)], BORDER, 1)

    max_r = max([abs(r.r) for r in rows if r.r] + [1.0])
    bar_l, bar_r = 800, W - 80
    zero = (bar_l + bar_r) / 2
    fm, fs = F["monob"](21), F["semi"](20)
    for i, r in enumerate(rows):
        ym = hy + 40 + i * rh + rh / 2
        if i % 2:
            c.rect((66, ym - rh / 2, W - 66, ym + rh / 2), fill=(15, 21, 38), r=8)
        s = r.sig
        c.text((84, ym), f"{s.number:04d}", F["mono"](18), DIM, anchor="lm")
        c.text((180, ym), s.symbol, fs, TEXT, anchor="lm")
        c.text((360, ym), "BUY" if s.is_long else "SELL", F["semi"](17),
               UP if s.is_long else DOWN, anchor="lm")
        if r.status == "running":
            c.text((470, ym), r.label.upper(), F["semi"](17), AMBER, anchor="lm")
            c.text((760, ym), "—", fm, DIM, anchor="rm")
            continue
        rc_col = UP if r.r > 0 else DOWN if r.r < 0 else MUTED
        c.text((470, ym), r.label.upper(), F["semi"](17), rc_col, anchor="lm")
        c.text((760, ym), _pips_str(r.pips), fm, rc_col, anchor="rm")
        c.line([(zero, ym - rh * 0.32), (zero, ym + rh * 0.32)], BORDER, 1.5)
        if r.r:
            half = (bar_r - bar_l) / 2 - 4
            length = abs(r.r) / max_r * half
            x0, x1 = (zero, zero + length) if r.r > 0 else (zero - length, zero)
            c.rect((x0, ym - 9, x1, ym + 9), fill=rc_col, r=4)
    if len(rc.rows) > max_rows:
        c.text((W / 2, bottom - 22), f"+ {len(rc.rows) - max_rows} more signals",
               F["semi"](17), MUTED, anchor="mm")

    if limit - bottom >= 220 and len(rc.closed) >= 2:
        _equity(c, rc, (56, bottom + 20, W - 56, limit))
    c.text((56, 1262), note, F["reg"](15), DIM, anchor="lm")
    c.text((56, 1314), brand.disclaimer, F["reg"](16), DIM, anchor="lm")
    c.text((W - 56, 1314), brand.handle, F["semi"](17), MUTED, anchor="rm")
    out = Path(out)
    c.png(out)
    return out


def _equity(c: Canvas, rc: Recap, box):
    """Cumulative R across the week's closed trades, in the order they closed."""
    x0, y0, x1, y1 = box
    c.rect(box, fill=PANEL, outline=BORDER, r=20)
    c.text((x0 + 28, y0 + 30), "CUMULATIVE R · BY CLOSE TIME", F["semi"](15), MUTED, anchor="lm")
    pts, total = [0.0], 0.0
    for r in sorted(rc.closed, key=lambda r: r.closed_at):
        total += r.r
        pts.append(total)
    lo, hi = min(pts + [0.0]), max(pts + [0.0])
    if hi - lo < 1:
        hi, lo = hi + 0.5, lo - 0.5
    px0, px1, py0, py1 = x0 + 70, x1 - 40, y0 + 58, y1 - 26

    def P(i, v):
        return (px0 + (px1 - px0) * i / (len(pts) - 1),
                py1 - (v - lo) / (hi - lo) * (py1 - py0))

    zy = P(0, 0)[1]
    c.dashed(px0, px1, zy, BORDER, 1.5, 6, 6)
    c.text((px0 - 14, zy), "0R", F["mono"](14), DIM, anchor="rm")
    col = UP if total >= 0 else DOWN
    xy = [P(i, v) for i, v in enumerate(pts)]
    c.line(xy, col, 3.5)
    for (x, y), v in zip(xy[1:], pts[1:]):
        c.rect((x - 5, y - 5, x + 5, y + 5), fill=col, r=5)
    lx, ly = xy[-1]
    c.text((lx - 10, ly - 20), f"{_signed(total, '.1f')}R", F["monob"](18), col, anchor="rs")


def caption(rc: Recap, brand: Brand, note: str) -> str:
    lines = []
    for r in rc.rows[:14]:
        s = r.sig
        res = r.label if r.status == "running" else f"{r.label:<12} {_pips_str(r.pips):>5}"
        lines.append(f"#{s.number:04d} {s.symbol:<8} {'BUY ' if s.is_long else 'SELL'} {res}")
    if len(rc.rows) > 14:
        lines.append(f"+ {len(rc.rows) - 14} more")
    head = (f"📊 <b>WEEKLY RECAP</b>  ·  {escape(rc.week_label.title())}\n\n"
            f"<b>{_pips_str(rc.total_pips)} pips</b>  ·  {_signed(rc.total_r, '.1f')}R net\n"
            f"✅ {rc.wins} win{'s' * (rc.wins != 1)}  ·  ❌ {rc.losses} loss{'es' * (rc.losses != 1)}"
            f"  ·  ➖ {rc.breakeven} BE  ·  win rate {rc.win_rate:.0f}%")
    body = "<pre>" + escape("\n".join(lines)) + "</pre>"
    foot = f"<i>{escape(note)}</i>\n<i>{escape(brand.disclaimer)}</i>"
    return "\n\n".join([head, body, foot])[:1024]


def note_text(sl_after_tp: str, now_local: datetime) -> str:
    rule = {"breakeven": "SL after a TP counted as breakeven",
            "best_tp": "SL after a TP counted at the highest TP reached",
            "loss": "SL after a TP counted as a loss"}[sl_after_tp]
    tz = getattr(now_local.tzinfo, "key", now_local.tzname())
    return (f"Signals opened this week · results as of {now_local:%d %b %H:%M} "
            f"({tz}) · {rule}")


# --- posting + schedule -------------------------------------------------------
def post_recap(svc, now_local: datetime, sl_after_tp: str, send: bool = True) -> str:
    rc = build(svc.store, now_local, sl_after_tp)
    if not rc.rows:
        return f"recap {rc.week_label}: no signals this week, nothing posted"
    note = note_text(sl_after_tp, now_local)
    png = svc.cfg.out_dir / f"recap-{rc.start:%G-W%V}.png"
    render(rc, svc.cfg.brand, png, note)
    text = caption(rc, svc.cfg.brand, note)
    png.with_suffix(".txt").write_text(text)
    if send and svc.tg is not None:
        svc.tg.send_photo(svc.cfg.target_chat, png, text, thread_id=svc.cfg.target_thread)
        return f"recap {rc.week_label} posted -> {png}"
    return f"recap {rc.week_label} rendered -> {png}"


@dataclass
class Schedule:
    day: str = "sat"
    at: str = "12:00"
    tz: str = "Asia/Almaty"
    sl_after_tp: str = "breakeven"


def start_scheduler(svc, sched: Schedule) -> threading.Thread:
    """Posts once per week at the scheduled local time (also catches up later
    that same day if the bot was down at the exact minute)."""
    tz = ZoneInfo(sched.tz)
    day = DAYS.index(sched.day.lower()[:3])
    hh, mm = (int(x) for x in sched.at.split(":"))

    def loop():
        while True:
            try:
                now = datetime.now(timezone.utc).astimezone(tz)
                key = f"{now:%G-W%V}"
                due = now.weekday() == day and (now.hour, now.minute) >= (hh, mm)
                if due and svc.store.get_meta("last_recap") != key:
                    log.info(post_recap(svc, now, sched.sl_after_tp))
                    svc.store.set_meta("last_recap", key)
            except Exception:
                log.exception("weekly recap failed; will retry")
            time.sleep(30)

    t = threading.Thread(target=loop, daemon=True, name="recap")
    t.start()
    log.info("weekly recap scheduled: %s %s %s", sched.day, sched.at, sched.tz)
    return t
