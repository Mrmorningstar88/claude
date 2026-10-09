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

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .recap import Schedule, post_recap, start_scheduler
from .service import Config, Service
from .signal import SignalError
from .telegram import Telegram, TelegramError
from .textparse import parse_text

log = logging.getLogger("signalbot")


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


def _run(svc: Service, payload: dict) -> None:
    try:
        log.info(svc.handle(payload))
    except SignalError as e:
        log.warning("rejected: %s", e)
    except Exception:
        log.exception("failed to process payload")


# --- relay: source channel -> private group -------------------------------
def cmd_relay(cfg: Config) -> None:
    if not (cfg.source_chat and cfg.target_chat):
        sys.exit("set SOURCE_CHAT_ID and TARGET_CHAT_ID (run `python -m signalbot chats`)")
    svc = Service(cfg)
    if sched := recap_schedule():
        start_scheduler(svc, sched)
    tg = Telegram(cfg.token)
    log.info("bot @%s relaying %s -> %s", tg.me()["username"], cfg.source_chat, cfg.target_chat)
    # Remember the last handled update so a restart never re-posts a signal.
    last = svc.store.get_meta("last_update_id")
    offset = last + 1 if last is not None else None
    while True:
        try:
            updates = tg.get_updates(offset)
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
            payload = parse_text(post.get("text") or post.get("caption") or "")
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


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(prog="signalbot")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("relay")
    w = sub.add_parser("webhook")
    w.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8080)))
    sub.add_parser("chats")
    pv = sub.add_parser("preview")
    pv.add_argument("files", nargs="+")
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
        elif a.cmd == "recap":
            cmd_recap(cfg, a.post)
        else:
            cmd_preview(cfg, a.files)
    except TelegramError as e:
        sys.exit(f"telegram: {e}")


if __name__ == "__main__":
    main()
