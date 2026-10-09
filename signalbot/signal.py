"""Turn a TradingView alert payload into a validated Signal.

The bot never invents levels: every price comes from the alert. Payloads with
levels on the wrong side of the entry are rejected rather than posted.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

FIAT = ("USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD", "SEK", "NOK",
        "SGD", "HKD", "ZAR", "MXN", "TRY", "PLN", "CNH", "DKK", "HUF", "CZK")
METALS = ("XAU", "XAG", "XPT", "XPD")
CRYPTO_QUOTES = ("USDT", "USDC")

# CFD index / commodity names (and common aliases) -> display name
INDICES = {
    "US30": "US30", "DJ30": "US30", "DJI": "US30", "WS30": "US30",
    "NAS100": "NAS100", "US100": "NAS100", "USTEC": "NAS100", "NDX": "NAS100",
    "SPX500": "SPX500", "US500": "SPX500", "SPX": "SPX500",
    "GER40": "GER40", "DE40": "GER40", "DAX": "GER40", "GER30": "GER40",
    "UK100": "UK100", "FTSE": "UK100", "JP225": "JP225",
    "USOIL": "USOIL", "WTI": "USOIL", "UKOIL": "UKOIL", "BRENT": "UKOIL",
}

# TradingView {{interval}} values -> display label
INTERVALS = {
    "1": "1m", "3": "3m", "5": "5m", "15": "15m", "30": "30m",
    "45": "45m", "60": "1H", "120": "2H", "240": "4H", "360": "6H",
    "480": "8H", "720": "12H", "D": "1D", "1D": "1D", "W": "1W", "1W": "1W",
}

EVENTS = ("open", "tp", "sl", "close", "cancel")


class SignalError(ValueError):
    pass


@dataclass
class Signal:
    base: str
    quote: str
    side: str                 # "long" | "short"
    entry: float              # reference entry (midpoint when a zone is given)
    entry_low: float
    entry_high: float
    tps: list[float]
    sl: float
    interval: str = ""
    exchange: str = ""
    leverage: str = ""
    setup: str = ""
    note: str = ""
    id: str = ""
    number: int = 0
    created: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def symbol(self) -> str:
        return f"{self.base}/{self.quote}" if self.quote else self.base

    @property
    def kind(self) -> str:
        return instrument_kind(self.base, self.quote)

    @property
    def pip(self) -> float:
        return pip_size(self.base, self.quote)

    @property
    def unit(self) -> str:
        return "pts" if self.kind in ("index", "metal") else "pips"

    def pips(self, price: float) -> float:
        """Signed distance from entry in pips (points for indices), in the trade's favour."""
        move = (price - self.entry) / self.pip
        return move if self.is_long else -move

    @property
    def is_long(self) -> bool:
        return self.side == "long"

    @property
    def is_zone(self) -> bool:
        return self.entry_low != self.entry_high

    def pct(self, price: float) -> float:
        """Signed % move from entry in the trade's favour."""
        move = (price - self.entry) / self.entry * 100
        return move if self.is_long else -move

    @property
    def risk(self) -> float:
        return abs(self.entry - self.sl)

    def pip_text(self, price: float, sign: bool = True) -> str:
        """'+35 pips', '−12.5 pips', '+120 pts'."""
        v = self.pips(price) if sign else abs(self.pips(price))
        num = f"{v:{'+' if sign else ''},.0f}" if abs(v - round(v)) < 0.05 else \
            f"{v:{'+' if sign else ''},.1f}"
        return f"{num} {self.unit}".replace("-", "−")

    def r_multiple(self, price: float) -> float:
        return abs(price - self.entry) / self.risk

    @property
    def rr(self) -> float:
        return self.r_multiple(self.tps[-1])

    def decimals(self) -> int:
        levels = [self.entry_low, self.entry_high, self.sl, *self.tps]
        d = max(price_decimals(p) for p in levels)
        if self.kind == "fx":  # quote FX to the pip: 1.0850, 157.20
            d = max(d, 2 if self.quote == "JPY" else 4)
        return min(d, 5 if self.kind == "fx" else d)

    def fmt(self, price: float) -> str:
        return f"{price:,.{self.decimals()}f}"

    def to_dict(self) -> dict:
        return {
            "base": self.base, "quote": self.quote, "side": self.side,
            "entry_low": self.entry_low, "entry_high": self.entry_high,
            "tp": self.tps, "sl": self.sl, "interval": self.interval,
            "exchange": self.exchange, "leverage": self.leverage,
            "setup": self.setup, "note": self.note, "id": self.id,
            "number": self.number, "created": self.created.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Signal":
        lo, hi = float(d["entry_low"]), float(d["entry_high"])
        return cls(
            base=d["base"], quote=d["quote"], side=d["side"],
            entry=(lo + hi) / 2, entry_low=lo, entry_high=hi,
            tps=[float(x) for x in d["tp"]], sl=float(d["sl"]),
            interval=d.get("interval", ""), exchange=d.get("exchange", ""),
            leverage=d.get("leverage", ""), setup=d.get("setup", ""),
            note=d.get("note", ""), id=d.get("id", ""),
            number=int(d.get("number", 0)),
            created=datetime.fromisoformat(d["created"]),
        )


def price_decimals(p: float) -> int:
    """Decimals that show ~6 significant digits, without trailing zeros."""
    if p <= 0:
        return 2
    digits = max(0, 6 - (int(math.floor(math.log10(p))) + 1))
    text = f"{p:.{min(digits, 10)}f}".rstrip("0")
    return len(text.split(".")[1]) if "." in text else 0


def instrument_kind(base: str, quote: str) -> str:
    if not quote:
        return "index"
    if base in METALS:
        return "metal"
    if base in FIAT and quote in FIAT:
        return "fx"
    return "crypto"


def pip_size(base: str, quote: str) -> float:
    kind = instrument_kind(base, quote)
    if kind == "index":
        return 0.01 if base in ("USOIL", "UKOIL") else 1.0
    if kind == "metal":
        return 0.01 if base == "XAU" else 0.001  # points, as the channel quotes them
    if kind == "fx":
        return 0.01 if quote == "JPY" else 0.0001
    return 1.0  # crypto: "pips" = whole units


def split_symbol(ticker: str) -> tuple[str, str]:
    """'OANDA:EURUSD' -> ('EUR','USD'); 'XAUUSD' -> ('XAU','USD');
    'NAS100' -> ('NAS100',''); 'BINANCE:BTCUSDT.P' -> ('BTC','USDT')."""
    t = ticker.strip().upper().lstrip("#$")
    t = t.split(":")[-1]
    t = re.sub(r"\.P$|\.PERP$|PERP$|\.(?:RAW|PRO|ECN|M|I|C)$|[.+#!]$", "", t)
    if t in INDICES:
        return INDICES[t], ""
    if t in ("GOLD", "SILVER"):
        return ("XAU" if t == "GOLD" else "XAG"), "USD"
    for sep in ("/", "-", "_"):
        if sep in t:
            base, quote = t.split(sep, 1)
            return base, quote
    if len(t) >= 6 and (t[:3] in FIAT + METALS) and t[3:6] in FIAT:
        return t[:3], t[3:6]
    for q in CRYPTO_QUOTES + ("USD", "BTC", "ETH"):
        if t.endswith(q) and len(t) > len(q):
            return t[: -len(q)], q
    raise SignalError(f"can't recognise symbol {ticker!r}")


def _num(v, name: str) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise SignalError(f"{name} is not a number: {v!r}")
    if not math.isfinite(x) or x <= 0:
        raise SignalError(f"{name} must be a positive number: {v!r}")
    return x


def _list(v) -> list:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return list(v)
    if isinstance(v, str):
        return [s for s in re.split(r"[,\s]+", v) if s]
    return [v]


def parse_event(payload: dict) -> str:
    event = str(payload.get("event", "open")).lower().strip()
    if event not in EVENTS:
        raise SignalError(f"unknown event {event!r}; use one of {', '.join(EVENTS)}")
    return event


def parse_signal(p: dict) -> Signal:
    """Build a Signal from an 'open' payload. See README for the schema."""
    if not p.get("ticker"):
        raise SignalError("ticker is required")
    base, quote = split_symbol(str(p["ticker"]))

    side = str(p.get("side", "")).lower().strip()
    side = {"buy": "long", "sell": "short"}.get(side, side)
    if side not in ("long", "short"):
        raise SignalError("side must be long/short (or buy/sell)")
    long = side == "long"

    if "entry_low" in p or "entry_high" in p:
        lo = _num(p.get("entry_low"), "entry_low")
        hi = _num(p.get("entry_high"), "entry_high")
        lo, hi = min(lo, hi), max(lo, hi)
    else:
        lo = hi = _num(p.get("entry"), "entry")
    entry = (lo + hi) / 2

    tps = [_num(x, "tp") for x in _list(p.get("tp"))]
    if not tps and p.get("tp_pct") is not None:
        pcts = [_num(x, "tp_pct") for x in _list(p.get("tp_pct"))]
        tps = [entry * (1 + x / 100 if long else 1 - x / 100) for x in pcts]
    if not tps:
        raise SignalError("at least one take-profit (tp or tp_pct) is required")
    if len(tps) > 4:
        raise SignalError("at most 4 take-profits are supported")

    if p.get("sl") is not None:
        sl = _num(p.get("sl"), "sl")
    elif p.get("sl_pct") is not None:
        x = _num(p.get("sl_pct"), "sl_pct")
        sl = entry * (1 - x / 100 if long else 1 + x / 100)
    else:
        raise SignalError("a stop-loss (sl or sl_pct) is required")

    # Sanity: everything on the correct side, TPs in order of distance.
    if long:
        if not sl < lo:
            raise SignalError("long: sl must be below the entry")
        if not all(t > hi for t in tps):
            raise SignalError("long: every tp must be above the entry")
        tps.sort()
    else:
        if not sl > hi:
            raise SignalError("short: sl must be above the entry")
        if not all(t < lo for t in tps):
            raise SignalError("short: every tp must be below the entry")
        tps.sort(reverse=True)

    interval = str(p.get("interval", "")).strip()
    interval = INTERVALS.get(interval.upper(), INTERVALS.get(interval, interval))

    lev = str(p.get("leverage", "") or "").strip()
    if lev and not lev.lower().endswith("x"):
        lev += "x"

    return Signal(
        base=base, quote=quote, side=side, entry=entry, entry_low=lo,
        entry_high=hi, tps=tps, sl=sl, interval=interval,
        exchange=str(p.get("exchange", "") or "").upper(),
        leverage=lev.lower(),
        setup=str(p.get("setup", "") or "")[:40],
        note=str(p.get("note", "") or "")[:280],
        id=str(p.get("id", "") or ""),
    )
