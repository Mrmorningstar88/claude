"""Display strings for the signal card in English and Russian."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

TZ_LABELS = {"Asia/Almaty": "АЛМАТЫ", "Asia/Dubai": "ДУБАЙ", "Europe/Moscow": "МСК"}
MONTHS_RU = "ЯНВ ФЕВ МАР АПР МАЙ ИЮН ИЮЛ АВГ СЕН ОКТ НОЯ ДЕК".split()


def ru_targets(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        word = "цель"
    elif n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        word = "цели"
    else:
        word = "целей"
    return f"{n} {word}"


T = {
    "en": {
        "signal": "SIGNAL", "long": "LONG", "short": "SHORT", "update": "UPDATE",
        "row_tp": "TP{n}", "row_entry": "ENTRY", "row_stop": "STOP",
        "tag_tp": "TP{n}", "tag_entry": "ENTRY", "tag_stop": "SL",
        "zone": "zone", "market": "market", "hit": "HIT",
        "rr": "RISK : REWARD", "stop_dist": "STOP DISTANCE", "setup": "SETUP",
        "targets": "TARGETS", "n_targets": lambda n: f"{n} TP",
        "no_chart": "live chart unavailable · levels to scale",
        "tp_hit": "TP{n} HIT", "stopped": "STOPPED OUT", "closed": "CLOSED",
        "cancelled": "CANCELLED", "not_triggered": "entry not triggered",
    },
    "ru": {
        "signal": "СИГНАЛ", "long": "ПОКУПКА", "short": "ПРОДАЖА", "update": "ОБНОВЛЕНИЕ",
        "row_tp": "ТЕЙК {n}", "row_entry": "ВХОД", "row_stop": "СТОП",
        "tag_tp": "TP{n}", "tag_entry": "ВХОД", "tag_stop": "СТОП",
        "zone": "зона", "market": "по рынку", "hit": "ЕСТЬ",
        "rr": "РИСК : ПРИБЫЛЬ", "stop_dist": "ДО СТОПА", "setup": "СЕТАП",
        "targets": "ЦЕЛИ", "n_targets": ru_targets,
        "no_chart": "график недоступен · уровни в масштабе",
        "tp_hit": "TP{n} ВЗЯТ", "stopped": "СТОП-ЛОСС", "closed": "ЗАКРЫТО",
        "cancelled": "ОТМЕНЕНО", "not_triggered": "вход не сработал",
    },
}


def stamp(when: datetime, lang: str, tz: str = "UTC") -> str:
    """Header date line: '09 OCT 2026 · 14:05 UTC' / '12 ОКТ 2026 · 17:51 АЛМАТЫ'."""
    if lang == "ru":
        local = when.astimezone(ZoneInfo(tz))
        label = TZ_LABELS.get(tz, tz)
        return f"{local:%d} {MONTHS_RU[local.month - 1]} {local:%Y · %H:%M} {label}"
    return when.strftime("%d %b %Y · %H:%M UTC").upper()
