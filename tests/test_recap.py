from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import signalbot.market as market
from signalbot.recap import build, outcome, week_bounds
from signalbot.service import Config, Service


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr(market, "candles", lambda *a, **k: [])
    return Service(Config(dry_run=True, out_dir=tmp_path, state_file=tmp_path / "s.json"))


def rec(svc, sym):
    return next(r for r in svc.store.data["signals"].values()
                if r["signal"]["base"] + r["signal"]["quote"] == sym)


def test_outcomes(svc):
    H = svc.handle
    H({"ticker": "EURUSD", "side": "buy", "entry": 1.1230, "tp": [1.1265, 1.1300], "sl": 1.1195})
    H({"ticker": "GBPUSD", "side": "buy", "entry": 1.3050, "tp": [1.3090], "sl": 1.3020})
    H({"ticker": "XAUUSD", "side": "sell", "entry": 4188, "tp": [4170, 4155], "sl": 4202})
    H({"ticker": "AUDUSD", "side": "sell", "entry": 0.6610, "tp": [0.6580], "sl": 0.6630})
    H({"event": "tp", "ticker": "EURUSD", "tp": 2})
    H({"event": "sl", "ticker": "GBPUSD"})
    H({"event": "tp", "ticker": "XAUUSD", "tp": 1})
    H({"event": "sl", "ticker": "XAUUSD"})

    eu = outcome(rec(svc, "EURUSD"))
    assert (eu.label, round(eu.pips), round(eu.r, 1)) == ("TP2", 70, 2.0)
    gu = outcome(rec(svc, "GBPUSD"))
    assert (gu.label, round(gu.pips), gu.r) == ("SL", -30, -1.0)
    xau = rec(svc, "XAUUSD")
    assert outcome(xau, "breakeven").r == 0.0
    assert outcome(xau, "best_tp").label == "TP1"
    assert outcome(xau, "loss").r == -1.0
    assert outcome(rec(svc, "AUDUSD")).status == "running"


def test_build_counts_only_this_week(svc):
    svc.handle({"ticker": "EURUSD", "side": "buy", "entry": 1.12, "tp": [1.13], "sl": 1.11})
    svc.handle({"event": "cancel", "ticker": "EURUSD"})
    svc.handle({"ticker": "GBPUSD", "side": "buy", "entry": 1.30, "tp": [1.31], "sl": 1.29})
    svc.handle({"event": "tp", "ticker": "GBPUSD", "tp": 1})
    now = datetime.now(ZoneInfo("Asia/Almaty"))
    rc = build(svc.store, now)
    assert [r.sig.base for r in rc.rows] == ["GBP"]  # cancelled is excluded
    assert rc.wins == 1 and rc.win_rate == 100
    next_week = build(svc.store, week_bounds(now)[1])
    assert next_week.rows == []


def test_week_bounds_monday_start():
    tz = ZoneInfo("Asia/Almaty")
    s, e = week_bounds(datetime(2026, 10, 10, 15, 0, tzinfo=tz))  # Saturday
    assert s == datetime(2026, 10, 5, tzinfo=tz) and (e - s).days == 7
