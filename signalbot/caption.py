"""Telegram captions (HTML parse mode, stays under the 1024-char limit)."""
from __future__ import annotations

from html import escape

from .card import Brand, Status
from .signal import Signal

RULE = "━━━━━━━━━━━━━━━━━━"


def _pips(sig: Signal, price: float) -> str:
    return f"{sig.pip_text(price):>10}"


def _levels(sig: Signal, hit: set[int] = frozenset(), stopped: bool = False) -> str:
    rows = []
    for i, tp in enumerate(sig.tps, 1):
        mark = " ✅" if i in hit else ""
        rows.append(f"TP{i}    {sig.fmt(tp):>11}  {_pips(sig, tp)}{mark}")
    entry = (f"{sig.fmt(sig.entry_low)}–{sig.fmt(sig.entry_high)}"
             if sig.is_zone else sig.fmt(sig.entry))
    rows.append(f"ENTRY  {entry:>11}")
    rows.append(f"STOP   {sig.fmt(sig.sl):>11}  {_pips(sig, sig.sl)}{' ❌' if stopped else ''}")
    if sig.is_long:  # price order, highest first, like the card
        rows = rows[: len(sig.tps)][::-1] + rows[len(sig.tps):]
    else:
        rows = [rows[-1], rows[-2]] + rows[: len(sig.tps)]
    return "<pre>" + escape("\n".join(rows)) + "</pre>"


def open_caption(sig: Signal, brand: Brand) -> str:
    arrow = "🟢 LONG" if sig.is_long else "🔴 SHORT"
    meta = " · ".join(x for x in (sig.interval, sig.leverage, sig.exchange) if x)
    parts = [
        f"<b>⚡ SIGNAL #{sig.number:04d}</b>  ·  <b>{escape(sig.symbol)}</b>",
        f"{arrow}{'  ·  ' + escape(meta) if meta else ''}"
        + (f"\n<i>{escape(sig.setup)}</i>" if sig.setup else ""),
        _levels(sig),
        f"R:R <b>1 : {sig.rr:.1f}</b>  ·  risk {sig.pip_text(sig.sl, sign=False)}",
    ]
    if sig.note:
        parts.append(escape(sig.note))
    parts.append(f"{RULE}\n<i>{escape(brand.disclaimer)}</i>")
    return "\n\n".join(parts)[:1024]


def update_caption(sig: Signal, st: Status, brand: Brand) -> str:
    tag = f"#{sig.number:04d} {escape(sig.symbol)}"
    if st.kind == "tp":
        price = sig.tps[st.tp_index - 1]
        last = st.tp_index == len(sig.tps)
        head = f"🎯 <b>TP{st.tp_index} HIT</b>  ·  {tag}"
        body = (f"<b>{sig.pip_text(price)}</b>  ·  +{sig.r_multiple(price):.1f}R\n"
                + ("All targets reached — trade complete. 🏁" if last
                   else "Consider securing partials / moving stop to entry."))
        lv = _levels(sig, hit=set(range(1, st.tp_index + 1)))
    elif st.kind == "sl":
        head = f"🛑 <b>STOPPED OUT</b>  ·  {tag}"
        body = (f"<b>{sig.pip_text(sig.sl)}</b>  ·  −1.0R\n"
                "Planned loss taken. Next setup.").replace("-", "−")
        lv = _levels(sig, stopped=True)
    elif st.kind == "close":
        p = st.price
        head = f"🔒 <b>CLOSED</b>  ·  {tag}"
        body = (f"Closed at {sig.fmt(p)}  ·  <b>{sig.pip_text(p)}</b>".replace("-", "−")
                if p else "Position closed.")
        lv = ""
    else:
        head = f"⏸ <b>CANCELLED</b>  ·  {tag}"
        body = "Entry was not triggered — setup removed."
        lv = ""
    parts = [head, body] + ([lv] if lv else []) + [f"<i>{escape(brand.disclaimer)}</i>"]
    return "\n\n".join(parts)[:1024]
