"""Read plain-text signal posts from the source channel.

Accepts JSON (same schema as the webhook) or free text like:

    XAUUSD SELL 4185 - 4190 1H
    SL: 4202
    TP1: 4170
    TP2: 4155
    Setup: Supply zone rejection

and short updates like "EURUSD TP1 hit", "GOLD SL hit", "EURUSD closed 1.1250",
"XAUUSD cancelled". Returns a webhook-style payload dict, or None if the post
isn't a signal (commentary, charts without levels, etc.).
"""
from __future__ import annotations

import json
import re

from .signal import SignalError, split_symbol

NUM = r"(\d[\d,]*\.?\d*)"


def _n(s: str) -> float:
    return float(s.replace(",", ""))


def _field(text: str, *names: str) -> str | None:
    for name in names:
        m = re.search(rf"(?im)^\W*{name}\b\W*[:=\-–]?\s*(.+)$", text)
        if m:
            return m.group(1).strip()
    return None


SKIP = {"BUY", "SELL", "LONG", "SHORT", "NOW", "LIMIT", "STOP", "SL", "TP", "HIT",
        "ENTRY", "SIGNAL", "NEW", "CLOSE", "CLOSED", "CANCEL", "CANCELLED", "THE",
        "AT", "MARKET", "ALERT", "FOREX", "FX", "TRADE", "UPDATE", "DONE"}


def find_symbol(text: str) -> str:
    for tok in re.findall(r"[A-Z][A-Z0-9/:\-_.]{2,20}", text.upper()):
        tok = tok.strip(".:-_")
        if tok in SKIP or re.fullmatch(r"TP\d|\d+[MHDW]", tok):
            continue
        try:
            split_symbol(tok)
            return tok
        except SignalError:
            continue
    return ""


def parse_text(text: str) -> dict | None:
    text = (text or "").strip()
    if not text:
        return None
    if text.startswith("{"):
        try:
            return json.loads(text)
        except ValueError:
            return None

    upper = text.upper()
    coin = find_symbol(text)

    # --- updates -------------------------------------------------------------
    first = upper.splitlines()[0]
    m = re.search(r"\bTP\s*(\d)\b.*\b(HIT|DONE|REACHED|✅)", first) or \
        re.search(r"(HIT|DONE|REACHED|✅).*\bTP\s*(\d)\b", first)
    if m and "ENTRY" not in upper:
        idx = next(g for g in m.groups() if g.isdigit())
        return {"event": "tp", "ticker": coin, "tp": int(idx)}
    if re.search(r"\b(SL|STOP(PED)?( OUT)?|STOP ?LOSS)\b.*\b(HIT|OUT)\b|\bSTOPPED OUT\b", first) \
            and "ENTRY" not in upper:
        return {"event": "sl", "ticker": coin}
    if re.search(r"\bCANCEL(L?ED)?\b", first):
        return {"event": "cancel", "ticker": coin}
    m = re.search(rf"\bCLOSE[D]?\b(?:\s+(?:AT|@))?\s*{NUM}?", first)
    if m and "ENTRY" not in upper:
        p = {"event": "close", "ticker": coin}
        if m.group(1):
            p["price"] = _n(m.group(1))
        return p

    # --- new signal --------------------------------------------------------
    side = re.search(r"\b(LONG|SHORT|BUY|SELL)\b", upper)
    entry = _field(text, "entry", "entry zone", "entry price", "buy zone", "sell zone",
                   "open", "price")
    if entry is None:  # a line holding only the entry: "📍 4182 - 4184"
        m = re.search(rf"(?m)^[^\w\n]*{NUM}(?:\s*[-–/]\s*{NUM})?\s*$", text)
        if m:
            entry = " ".join(g for g in m.groups() if g)
    if entry is None:  # "XAUUSD SELL 4185-4190" / "EURUSD BUY NOW @ 1.0850"
        m = re.search(rf"\b(?:BUY|SELL|LONG|SHORT)\b[^\n\d]*{NUM}(?:\s*[-–/]\s*{NUM})?", upper)
        if m:
            entry = " ".join(g for g in m.groups() if g)
    sl = _field(text, "sl", "stop", "stop loss", "stoploss", "stop-loss")
    if not (coin and side and entry and sl):
        return None

    p: dict = {"event": "open", "ticker": coin, "side": side.group(1).lower()}
    nums = [_n(x) for x in re.findall(NUM, entry)]
    if len(nums) >= 2:
        p["entry_low"], p["entry_high"] = nums[0], nums[1]
    elif nums:
        p["entry"] = nums[0]
    m = re.search(NUM, sl)
    if m:
        p["sl"] = _n(m.group(1))

    tps = [(_n(v)) for _, v in sorted(
        re.findall(rf"(?im)^\W*(?:tp|target|take profit)\s*(\d)\W*[:=\-–]?\s*{NUM}", text))]
    if not tps:
        line = _field(text, "tp", "tps", "targets", "take profit")
        if line:
            tps = [_n(x) for x in re.findall(NUM, line)]
    p["tp"] = tps

    tf = re.search(r"\b(\d{1,2}\s?(?:M|MIN|H|D|W))\b", first)
    if tf:
        v = tf.group(1).replace(" ", "").replace("MIN", "m")
        p["interval"] = v[:-1] + ("m" if v[-1] == "M" else v[-1])
    for key, names in (("setup", ("setup", "strategy", "reason")),
                       ("leverage", ("leverage", "lev")),
                       ("exchange", ("exchange",)),
                       ("note", ("note", "comment"))):
        v = _field(text, *names)
        if v:
            p[key] = v
    return p
