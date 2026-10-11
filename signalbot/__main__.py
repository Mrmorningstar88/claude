"""signalbot CLI.

  python -m signalbot relay            watch SOURCE_CHAT_ID, post cards to TARGET_CHAT_ID
  python -m signalbot webhook          HTTP endpoint for TradingView alerts
  python -m signalbot chats            list chat ids the bot has seen (setup helper)
  python -m signalbot preview FILE...  render cards from JSON/text files, no posting
  python -m signalbot recap [--post]   render (or post) this week's results recap
"""
from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import queue
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from .daily import Daily, post_daily, start_daily, validate
from .telegram import redact
from .recap import Schedule, post_recap, start_scheduler
from .service import Config, Service
from .signal import SignalError
from .telegram import Telegram, TelegramError
from .textparse import parse_text

log = logging.getLogger("signalbot")


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def load_dotenv(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def recap_schedule() -> Schedule | None:
    e = os.environ.get
    if e("RECAP_ENABLED", "1") == "0":
        return None
    sched = Schedule(day=e("RECAP_DAY", Schedule.day), at=e("RECAP_TIME", Schedule.at),
                     tz=e("RECAP_TZ", Schedule.tz),
                     sl_after_tp=e("RECAP_SL_AFTER_TP", Schedule.sl_after_tp))
    if sched.sl_after_tp not in ("breakeven", "best_tp", "loss"):
        sys.exit("RECAP_SL_AFTER_TP must be breakeven, best_tp or loss")
    return sched


def daily_enabled() -> bool:
    return os.environ.get("DAILY_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")


def daily_config(cfg: Config) -> Daily:
    """Build the daily-post settings from DAILY_*; raises ValueError if invalid."""
    e = os.environ.get
    start = e("DAILY_START", "").strip()
    try:
        start_date = date.fromisoformat(start) if start else None
    except ValueError:
        raise ValueError(f"DAILY_START must be YYYY-MM-DD, got {start!r}") from None
    d = Daily(chat=e("DAILY_CHAT_ID", "").strip(), token=e("DAILY_BOT_TOKEN") or cfg.token,
              at=e("DAILY_TIME", Daily.at).strip(), latest=e("DAILY_LATEST", Daily.latest).strip(),
              tz=e("DAILY_TZ", Daily.tz).strip(), start=start_date,
              cta=e("DAILY_CTA", ""), handle=e("DAILY_HANDLE", ""),
              disclaimer=e("DAILY_DISCLAIMER", Daily.disclaimer))
    validate(d)
    return d


def _run(svc: Service, payload: dict) -> None:
    try:
        log.info(svc.handle(payload))
    except SignalError as e:
        log.warning("rejected: %s", e)
    except Exception:
        log.exception("failed to process payload")


# --- health: lets an outside watchdog see the relay is alive ----------------
HEALTH = {"started": time.time(), "last_poll": 0.0}


def start_health(port: int, svc: Service) -> None:
    """GET /health -> 200 {"ok": true, ...} while Telegram was polled in the last
    5 minutes, else 503. Exposes no secrets and no signal content."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            last = HEALTH["last_poll"]
            age = time.time() - last if last else None
            done = svc.store.get_meta("daily_done", {}) or {}
            body = json.dumps({
                "ok": age is not None and age < 300,
                "seconds_since_poll": round(age) if age is not None else None,
                "uptime_s": round(time.time() - HEALTH["started"]),
                "daily_last": max(done) if done else None,
            }).encode()
            self.send_response(200 if age is not None and age < 300 else 503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            pass

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="health").start()
    log.info("health check on :%d/health", port)


# --- relay: source channel -> private group -------------------------------
def cmd_relay(cfg: Config) -> None:
    if not (cfg.source_chat and cfg.target_chat):
        sys.exit("set SOURCE_CHAT_ID and TARGET_CHAT_ID (run `python -m signalbot chats`)")
    svc = Service(cfg)
    open_ = sum(1 for r in svc.store.data["signals"].values() if r["state"] == "open")
    log.info("state %s: next signal #%04d, %d open", cfg.state_file,
             svc.store.data["next"], open_)
    if sched := recap_schedule():
        start_scheduler(svc, sched)
    if daily_enabled():
        try:
            start_daily(svc, daily_config(cfg))
        except Exception as e:  # never let the daily post take the relay down
            log.error("daily post DISABLED, fix the DAILY_* settings: %s", e)
    else:
        log.info("daily post off (DAILY_ENABLED is not set)")
    if port := os.environ.get("HEALTH_PORT") or os.environ.get("PORT"):
        start_health(int(port), svc)
    tg = Telegram(cfg.token)
    log.info("bot @%s relaying %s -> %s", tg.me()["username"], cfg.source_chat, cfg.target_chat)
    # Remember the last handled update so a restart never re-posts a signal.
    last = svc.store.get_meta("last_update_id")
    offset = last + 1 if last is not None else None
    while True:
        try:
            updates = tg.get_updates(offset)
            HEALTH["last_poll"] = time.time()
        except Exception as e:
            log.warning("getUpdates failed: %s; retrying", e)
            time.sleep(5)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            svc.store.set_meta("last_update_id", u["update_id"])
            post = u.get("channel_post") or u.get("message")
            if not post:
                continue
            chat = post["chat"]
            if str(chat["id"]) != cfg.source_chat and \
                    f"@{chat.get('username', '')}".lower() != cfg.source_chat.lower():
                continue
            try:
                payload = parse_text(post.get("text") or post.get("caption") or "")
            except Exception:  # one odd post must never stop the relay
                log.exception("could not read source post %s", post.get("message_id"))
                continue
            if payload is None:
                log.info("source post %s is not a signal, ignored", post["message_id"])
                continue
            _run(svc, payload)


# --- webhook: TradingView ---------------------------------------------------
def cmd_webhook(cfg: Config, port: int) -> None:
    if not cfg.webhook_secret:
        sys.exit("set WEBHOOK_SECRET so strangers can't post to your group")
    svc = Service(cfg)
    if sched := recap_schedule():
        start_scheduler(svc, sched)
    jobs: queue.Queue = queue.Queue()

    def worker():  # one at a time: keeps numbering and replies in order
        while True:
            _run(svc, jobs.get())

    threading.Thread(target=worker, daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
            payload = parse_text(body.decode("utf-8", "replace"))
            key = parse_qs(urlparse(self.path).query).get("key", [""])[0]
            if payload is not None:
                key = str(payload.pop("secret", "")) or key
            if not hmac.compare_digest(key, cfg.webhook_secret):
                self.send_response(403)
                self.end_headers()
                return
            if payload is None:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"not a signal")
                return
            jobs.put(payload)  # answer fast: TradingView times out after a few seconds
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, fmt, *args):
            log.debug(fmt, *args)

    log.info("webhook listening on :%d/webhook", port)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


def cmd_chats(cfg: Config) -> None:
    tg = Telegram(cfg.token)
    print(f"bot: @{tg.me()['username']}")
    print("Post something in the channel and the group, then run this again.\n")
    seen = {}
    for u in tg.get_updates(None, timeout=0):
        for k in ("channel_post", "message", "my_chat_member"):
            if k in u:
                ch = u[k]["chat"]
                seen[ch["id"]] = (ch["type"], ch.get("title") or ch.get("username"))
    for cid, (typ, title) in seen.items():
        print(f"{cid:>16}  {typ:<11} {title}")
    if not seen:
        print("(no chats yet)")


def cmd_preview(cfg: Config, files: list[str]) -> None:
    cfg.dry_run = True
    svc = Service(cfg)
    for f in files:
        raw = Path(f).read_text()
        payloads = json.loads(raw) if raw.lstrip().startswith("[") else [parse_text(raw)]
        for p in payloads:
            if p is None:
                print(f"{f}: not recognised as a signal")
                continue
            try:
                print(svc.handle(p))
            except SignalError as e:
                print(f"{f}: rejected: {e}")


def cmd_recap(cfg: Config, post: bool) -> None:
    sched = recap_schedule() or Schedule()
    cfg.dry_run = not post
    svc = Service(cfg)
    now = datetime.now(timezone.utc).astimezone(ZoneInfo(sched.tz))
    print(post_recap(svc, now, sched.sl_after_tp, send=post))


def cmd_daily(cfg: Config, post: bool, at: str | None, force: bool) -> None:
    if post and not force:
        sys.exit("--post sends to the channel now. If the relay is running it may post the\n"
                 "same day again, because each process keeps its own state. Stop the relay\n"
                 "or add --force if you are sure.")
    os.environ.setdefault("DAILY_CHAT_ID", "@preview")
    try:
        d = daily_config(cfg)
    except ValueError as e:
        sys.exit(str(e))
    cfg.dry_run = not post
    svc = Service(cfg)
    tz = ZoneInfo(d.tz)
    now = (datetime.fromisoformat(at).replace(tzinfo=tz) if at
           else datetime.now(timezone.utc).astimezone(tz))
    print(post_daily(svc, d, now, send=post))


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(message)s")
    for h in logging.getLogger().handlers:  # backstop: never print a bot token
        h.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(message)s"))
    ap = argparse.ArgumentParser(prog="signalbot")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("relay")
    w = sub.add_parser("webhook")
    w.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8080)))
    sub.add_parser("chats")
    pv = sub.add_parser("preview")
    pv.add_argument("files", nargs="+")
    dp = sub.add_parser("daily")
    dp.add_argument("--post", action="store_true", help="send to DAILY_CHAT_ID now")
    dp.add_argument("--at", help="pretend local time, e.g. '2026-10-12 19:20'")
    dp.add_argument("--force", action="store_true", help="allow --post while a relay may run")
    rp = sub.add_parser("recap")
    rp.add_argument("--post", action="store_true", help="send to TARGET_CHAT_ID")
    a = ap.parse_args()
    cfg = Config.from_env()
    try:
        if a.cmd == "relay":
            cmd_relay(cfg)
        elif a.cmd == "webhook":
            cmd_webhook(cfg, a.port)
        elif a.cmd == "chats":
            cmd_chats(cfg)
        elif a.cmd == "daily":
            cmd_daily(cfg, a.post, a.at, a.force)
        elif a.cmd == "recap":
            cmd_recap(cfg, a.post)
        else:
            cmd_preview(cfg, a.files)
    except TelegramError as e:
        sys.exit(f"telegram: {e}")


if __name__ == "__main__":
    main()
