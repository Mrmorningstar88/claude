"""Recent candles for the card's chart, from free public endpoints.

FX pairs and indices: Yahoo Finance chart API (4H/8H/12H built from 1H bars).
Gold/silver: OKX XAU/XAG perpetuals, which track spot closely.
Crypto: OKX, Binance fallback.
Charts are context only; broker CFD prices can differ slightly. If no source
has the instrument, the card is drawn without candles instead of failing.
"""
from __future__ import annotations

import logging

import requests

from .signal import instrument_kind

log = logging.getLogger(__name__)

Candle = tuple[int, float, float, float, float]  # ts_ms, open, high, low, close
UA = {"User-Agent": "Mozilla/5.0 (signalbot)"}

YAHOO_INDEX = {
    "US30": "^DJI", "NAS100": "^NDX", "SPX500": "^GSPC", "GER40": "^GDAXI",
    "UK100": "^FTSE", "JP225": "^N225", "USOIL": "CL=F", "UKOIL": "BZ=F",
}
# display interval -> (yahoo interval, yahoo range, hours to aggregate)
YAHOO_BARS = {
    "1m": ("1m", "1d", 1), "5m": ("5m", "1d", 1), "15m": ("15m", "5d", 1),
    "30m": ("30m", "5d", 1), "1H": ("60m", "1mo", 1), "2H": ("60m", "1mo", 2),
    "4H": ("60m", "3mo", 4), "6H": ("60m", "3mo", 6), "8H": ("60m", "6mo", 8),
    "12H": ("60m", "6mo", 12), "1D": ("1d", "6mo", 1), "1W": ("1wk", "2y", 1),
}
# display interval -> (OKX bar, Binance interval)
CRYPTO_BARS = {
    "1m": ("1m", "1m"), "3m": ("3m", "3m"), "5m": ("5m", "5m"),
    "15m": ("15m", "15m"), "30m": ("30m", "30m"), "45m": ("30m", "30m"),
    "1H": ("1H", "1h"), "2H": ("2H", "2h"), "4H": ("4H", "4h"),
    "6H": ("6H", "6h"), "8H": ("4H", "8h"), "12H": ("12H", "12h"),
    "1D": ("1D", "1d"), "1W": ("1W", "1w"),
}


def _aggregate(rows: list[Candle], n: int) -> list[Candle]:
    if n <= 1:
        return rows
    out = []
    for i in range(0, len(rows) - len(rows) % n, n):
        chunk = rows[i:i + n]
        out.append((chunk[0][0], chunk[0][1], max(c[2] for c in chunk),
                    min(c[3] for c in chunk), chunk[-1][4]))
    return out


def _yahoo(symbol: str, interval: str, limit: int) -> list[Candle]:
    yi, yr, agg = YAHOO_BARS.get(interval, ("60m", "1mo", 1))
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                     params={"interval": yi, "range": yr}, headers=UA, timeout=8)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    rows = [(t * 1000, o, h, l, c) for t, o, h, l, c in
            zip(res["timestamp"], q["open"], q["high"], q["low"], q["close"])
            if None not in (o, h, l, c)]
    return _aggregate(rows, agg)[-limit:]


def _okx(inst: str, bar: str, limit: int) -> list[Candle]:
    r = requests.get("https://www.okx.com/api/v5/market/candles",
                     params={"instId": inst, "bar": bar, "limit": limit}, timeout=8)
    r.raise_for_status()
    rows = r.json().get("data") or []
    return [(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]))
            for x in reversed(rows)]


def _binance(symbol: str, interval: str, limit: int) -> list[Candle]:
    r = requests.get("https://api.binance.com/api/v3/klines",
                     params={"symbol": symbol, "interval": interval, "limit": limit},
                     timeout=8)
    r.raise_for_status()
    return [(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]))
            for x in r.json()]


def _sources(base: str, quote: str, interval: str, limit: int):
    kind = instrument_kind(base, quote)
    okx_bar, bn_bar = CRYPTO_BARS.get(interval, ("1H", "1h"))
    if kind == "fx":
        yield "yahoo", lambda: _yahoo(f"{base}{quote}=X", interval, limit)
    elif kind == "index" and base in YAHOO_INDEX:
        yield "yahoo", lambda: _yahoo(YAHOO_INDEX[base], interval, limit)
    elif kind == "metal" and quote == "USD":
        yield "okx", lambda: _okx(f"{base}-USDT-SWAP", okx_bar, limit)
    elif kind == "crypto":
        q = "USDT" if quote in ("USD",) else quote
        yield "okx", lambda: _okx(f"{base}-{q}", okx_bar, limit)
        yield "binance", lambda: _binance(f"{base}{q}", bn_bar, limit)


def candles(base: str, quote: str, interval: str, limit: int = 64) -> list[Candle]:
    for name, fetch in _sources(base, quote, interval, limit):
        try:
            data = fetch()
            if len(data) >= 10:
                return data
        except Exception as e:  # network, 4xx, bad JSON
            log.info("candles %s %s%s failed: %s", name, base, quote, e)
    log.warning("no candles for %s%s; drawing levels only", base, quote)
    return []
