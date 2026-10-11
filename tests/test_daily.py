from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import signalbot.daily as daily
import signalbot.market as market
from signalbot.daily import Daily, caption, pick, post_daily
from signalbot.service import Config, Service
from signalbot.signal import parse_signal
from signalbot.telegram import TelegramError

ALMATY = ZoneInfo("Asia/Almaty")


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr(market, "candles", lambda *a, **k: [])
    return Service(Config(dry_run=True, out_dir=tmp_path, state_file=tmp_path / "s.json"))


def add(svc, ticker, side, entry, tp, sl, created, state="open", tp_hit=0):
    s = parse_signal({"ticker": ticker, "side": side, "entry": entry, "tp": tp, "sl": sl})
    s.number = svc.store.next_number()
    s.created = created
    svc.store.add(s, message_id=100 + s.number)
    key = list(svc.store.data["signals"])[-1]
    svc.store.update(key, state=state, tp_hit=tp_hit)
    return s


def at(h, m, day=12):
    return datetime(2026, 10, day, h, m, tzinfo=ALMATY)


def test_pick_latest_fresh_open_signal_of_today(svc):
    add(svc, "EURUSD", "buy", 1.12, [1.13], 1.11, at(9, 0, day=11))           # yesterday
    add(svc, "GBPUSD", "buy", 1.30, [1.31], 1.29, at(10, 0))                   # fresh
    add(svc, "XAUUSD", "sell", 4188, [4170], 4202, at(15, 0), tp_hit=1)        # TP hit
    add(svc, "USDJPY", "buy", 151.2, [151.6], 150.9, at(16, 0), state="closed")
    add(svc, "USDCHF", "buy", 0.83, [0.84], 0.82, at(19, 30))                  # after now
    rec = pick(svc.store, at(19, 20))
    assert rec["signal"]["base"] == "GBP"


def test_pick_falls_back_to_tp_hit_then_none(svc):
    add(svc, "XAUUSD", "sell", 4188, [4170, 4150], 4202, at(15, 0), tp_hit=1)
    assert pick(svc.store, at(19, 20))["signal"]["base"] == "XAU"
    assert pick(svc.store, at(19, 20, day=13)) is None


def test_caption_is_russian_with_cta():
    s = parse_signal({"ticker": "XAUUSD", "side": "sell", "entry_low": 4182, "entry_high": 4184,
                      "tp": [4179, 4168], "sl": 4202})
    d = Daily(chat="@x", token="t", cta="Вступай в бесплатное коммьюнити тут https://linktw.in/skauch")
    text = caption(s, d)
    assert "ПРОДАЖА" in text and "СТОП" in text and "ТЕЙК 2" in text
    assert "4 182–4 184" in text and "−1 900 п." in text
    assert text.count("https://linktw.in/skauch") == 1
    assert "Не финансовый совет" in text
    assert not any(w in text for w in ("pips", "pts", "ENTRY", "STOP", "SHORT"))
    assert len(text) <= 1024


class FakeTG:
    calls = []
    fail = None

    def __init__(self, token):
        pass

    def send_photo(self, chat, png, text):
        FakeTG.calls.append(chat)
        if FakeTG.fail:
            raise FakeTG.fail
        return 555


@pytest.fixture
def tg(monkeypatch):
    FakeTG.calls, FakeTG.fail = [], None
    monkeypatch.setattr(daily, "Telegram", FakeTG)
    return FakeTG


def test_posts_once_per_day(svc, tg):
    add(svc, "GBPUSD", "buy", 1.30, [1.31], 1.29, at(10, 0))
    d = Daily(chat="@arutnews", token="t")
    assert "posted #0001" in post_daily(svc, d, at(19, 20))
    assert "already posted" in post_daily(svc, d, at(19, 25))
    assert tg.calls == ["@arutnews"]
    assert svc.store.get_meta("daily_done")["2026-10-12"]["message_id"] == 555


def test_telegram_refusal_allows_retry_but_timeout_does_not(svc, tg):
    add(svc, "GBPUSD", "buy", 1.30, [1.31], 1.29, at(10, 0))
    d = Daily(chat="@arutnews", token="t")
    tg.fail = TelegramError("sendPhoto: chat not found")
    with pytest.raises(TelegramError):
        post_daily(svc, d, at(19, 20))
    assert "2026-10-12" not in svc.store.get_meta("daily_done", {})
    tg.fail = TimeoutError("read timed out")
    with pytest.raises(TimeoutError):
        post_daily(svc, d, at(19, 21))
    assert "2026-10-12" in svc.store.get_meta("daily_done")      # never resent
    assert "already posted" in post_daily(svc, d, at(19, 22))


def test_nothing_today_posts_nothing(svc, tg):
    d = Daily(chat="@arutnews", token="t")
    assert "nothing posted" in post_daily(svc, d, at(19, 20))
    assert tg.calls == [] and svc.store.get_meta("daily_done") is None
