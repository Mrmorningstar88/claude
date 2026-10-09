import pytest

from signalbot.signal import SignalError, parse_signal, split_symbol
from signalbot.textparse import parse_text


@pytest.mark.parametrize("ticker,expected", [
    ("OANDA:EURUSD", ("EUR", "USD")),
    ("FX:GBPJPY", ("GBP", "JPY")),
    ("XAUUSD", ("XAU", "USD")),
    ("GOLD", ("XAU", "USD")),
    ("EURUSD.raw", ("EUR", "USD")),
    ("US100", ("NAS100", "")),
    ("BINANCE:BTCUSDT.P", ("BTC", "USDT")),
])
def test_split_symbol(ticker, expected):
    assert split_symbol(ticker) == expected


def test_pips_fx_jpy_gold():
    eu = parse_signal({"ticker": "EURUSD", "side": "buy", "entry": 1.1230,
                       "tp": [1.1265], "sl": 1.1195})
    assert eu.pip_text(1.1265) == "+35 pips"
    gj = parse_signal({"ticker": "GBPJPY", "side": "sell", "entry": 209.25,
                       "tp": [208.95], "sl": 209.55})
    assert gj.pip_text(208.95) == "+30 pips"
    xau = parse_signal({"ticker": "XAUUSD", "side": "sell", "entry": 4188,
                        "tp": [4170], "sl": 4202})
    assert xau.pip_text(4202) == "−140 pips"


def test_rejects_wrong_side_levels():
    with pytest.raises(SignalError):
        parse_signal({"ticker": "EURUSD", "side": "buy", "entry": 1.12,
                      "tp": [1.13], "sl": 1.125})


def test_tp_pct_and_sl_pct():
    s = parse_signal({"ticker": "NAS100", "side": "buy", "entry": 25000,
                      "tp_pct": [0.4], "sl_pct": 0.2})
    assert s.tps == [pytest.approx(25100)] and s.sl == pytest.approx(24950)


def test_text_signal_inline_entry_zone():
    p = parse_text("XAUUSD SELL 4185 - 4190 1H\nSL: 4202\nTP1: 4170\nTP2: 4155")
    assert p["ticker"] == "XAUUSD" and p["side"] == "sell"
    assert (p["entry_low"], p["entry_high"]) == (4185, 4190)
    assert p["tp"] == [4170, 4155] and p["sl"] == 4202 and p["interval"] == "1H"


def test_text_signal_buy_now():
    p = parse_text("EURUSD BUY NOW @ 1.1230\nSL 1.1195\nTP1 1.1265\nTP2 1.1300")
    assert p["entry"] == 1.1230 and p["tp"] == [1.1265, 1.1300]


@pytest.mark.parametrize("text,event", [
    ("EURUSD TP1 hit ✅", "tp"),
    ("GOLD SL hit", "sl"),
    ("GBPJPY stopped out", "sl"),
    ("EURUSD closed at 1.1250", "close"),
    ("XAUUSD cancelled", "cancel"),
])
def test_text_updates(text, event):
    assert parse_text(text)["event"] == event


def test_commentary_is_ignored():
    assert parse_text("Good morning traders! Big news day, stay careful.") is None


def test_update_matches_full_pair(tmp_path):
    from signalbot.service import Config, Service
    svc = Service(Config(dry_run=True, out_dir=tmp_path, state_file=tmp_path / "s.json"))
    import signalbot.market as m
    m.candles = lambda *a, **k: []  # offline
    svc.handle(parse_text("EURJPY BUY 170.00\nSL 169.50\nTP1 170.50"))
    svc.handle(parse_text("EURUSD BUY 1.1230\nSL 1.1195\nTP1 1.1265"))
    svc.handle(parse_text("EURJPY TP1 hit"))
    recs = svc.store.data["signals"]
    states = {r["signal"]["quote"]: r["tp_hit"] for r in recs.values()}
    assert states == {"JPY": 1, "USD": 0}
