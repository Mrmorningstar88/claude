"""Signal card renderer: 1080x1350 (4:5) PNG.

Look: dark terminal grid, neon glow in the trade's colour, live candles with
a position-tool overlay (profit/risk boxes projected to the right of the last
candle), level tags, a price-ordered level ladder and R:R tiles.
Drawn at 2x and downsampled so lines and text stay smooth.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .market import Candle
from .signal import Signal

W, H, K = 1080, 1350, 2
FONTS = Path(__file__).resolve().parent.parent / "assets" / "fonts"

BG = (7, 10, 18)
DOT = (22, 28, 46)
PANEL = (12, 17, 32)
BORDER = (30, 38, 60)
TEXT = (238, 242, 255)
MUTED = (124, 135, 166)
DIM = (74, 83, 112)
UP = (25, 230, 161)
DOWN = (255, 77, 109)
ENTRY = (238, 242, 255)
AMBER = (255, 190, 60)


@dataclass
class Brand:
    name: str = "SIGNAL DESK"
    handle: str = "t.me/yourchannel"
    disclaimer: str = "Not financial advice · risk only what you can afford to lose"


@dataclass
class Status:
    """An update on an open signal: tp hit, stopped out, closed or cancelled."""
    kind: str                 # tp | sl | close | cancel
    tp_index: int = 0         # 1-based, for kind == tp
    price: float | None = None


def _font(name: str, size: float) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), int(size * K))


F = {
    "bold": lambda s: _font("Inter-Bold.otf", s),
    "semi": lambda s: _font("Inter-SemiBold.otf", s),
    "med": lambda s: _font("Inter-Medium.otf", s),
    "reg": lambda s: _font("Inter-Regular.otf", s),
    "mono": lambda s: _font("DejaVuSansMono.ttf", s),
    "monob": lambda s: _font("DejaVuSansMono-Bold.ttf", s),
}


class Canvas:
    def __init__(self):
        self.img = Image.new("RGBA", (W * K, H * K), BG + (255,))
        self.d = ImageDraw.Draw(self.img)

    @staticmethod
    def s(*v):
        return [x * K for x in v]

    def rect(self, box, fill=None, outline=None, r=0, width=1):
        self.d.rounded_rectangle(self.s(*box), radius=r * K, fill=fill,
                                 outline=outline, width=width * K)

    def alpha_rect(self, box, rgba, r=0):
        layer = Image.new("RGBA", self.img.size, (0, 0, 0, 0))
        ImageDraw.Draw(layer).rounded_rectangle(self.s(*box), radius=r * K, fill=rgba)
        self.img.alpha_composite(layer)
        self.d = ImageDraw.Draw(self.img)

    def line(self, pts, fill, width=1):
        self.d.line([(x * K, y * K) for x, y in pts], fill=fill, width=int(width * K))

    def dashed(self, x0, x1, y, fill, width=2, dash=10, gap=7):
        x = x0
        while x < x1:
            self.line([(x, y), (min(x + dash, x1), y)], fill, width)
            x += dash + gap

    def poly(self, pts, fill):
        self.d.polygon([(x * K, y * K) for x, y in pts], fill=fill)

    def text(self, xy, s, font, fill, anchor="la"):
        self.d.text((xy[0] * K, xy[1] * K), s, font=font, fill=fill, anchor=anchor)

    def tlen(self, s, font) -> float:
        return self.d.textlength(s, font=font) / K

    def glow(self, center, radius, rgb, alpha=70):
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        cx, cy = center
        ImageDraw.Draw(layer).ellipse(
            (cx - radius, cy - radius * 0.6, cx + radius, cy + radius * 0.6),
            fill=rgb + (alpha,))
        layer = layer.filter(ImageFilter.GaussianBlur(radius * 0.45))
        self.img.alpha_composite(layer.resize(self.img.size, Image.BILINEAR))
        self.d = ImageDraw.Draw(self.img)

    def png(self, path: str | Path):
        self.img.resize((W, H), Image.LANCZOS).convert("RGB").save(path, optimize=True)


def _chip(c: Canvas, x, y, label, fill, color, font, outline=None, tri=None):
    """Pill at (x, y); returns the x where the next chip may start."""
    pad, h = 18, 46
    tw = c.tlen(label, font) + (24 if tri else 0)
    c.rect((x, y, x + tw + pad * 2, y + h), fill=fill, outline=outline, r=23, width=2)
    tx = x + pad
    if tri:  # direction triangle
        cy = y + h / 2
        pts = ([(tx, cy + 7), (tx + 16, cy + 7), (tx + 8, cy - 8)] if tri == "up"
               else [(tx, cy - 7), (tx + 16, cy - 7), (tx + 8, cy + 8)])
        c.poly(pts, color)
        tx += 24
    c.text((tx, y + h / 2), label, font, color, anchor="lm")
    return x + tw + pad * 2 + 12


def _check(c: Canvas, x, y, color):
    c.line([(x, y), (x + 6, y + 7), (x + 18, y - 7)], color, 3.5)


def render(sig: Signal, data: list[Candle], brand: Brand, out: str | Path,
           status: Status | None = None, when: datetime | None = None) -> Path:
    c = Canvas()
    side_col = UP if sig.is_long else DOWN
    when = when or sig.created

    # --- background: dot grid + glow --------------------------------------
    for gy in range(20, H, 36):
        for gx in range(20, W, 36):
            c.rect((gx - 1, gy - 1, gx + 1, gy + 1), fill=DOT)
    c.glow((W - 120, 60), 520, side_col, 60)
    c.glow((80, H - 40), 380, side_col, 22)

    # --- header -------------------------------------------------------------
    c.rect((56, 48, 94, 86), fill=side_col, r=10)
    c.text((75, 67), brand.name[:1].upper(), F["bold"](22), BG, anchor="mm")
    c.text((108, 67), brand.name.upper(), F["bold"](24), TEXT, anchor="lm")
    c.text((W - 56, 56), f"SIGNAL #{sig.number:04d}", F["monob"](22), TEXT, anchor="ra")
    c.text((W - 56, 88), when.strftime("%d %b %Y · %H:%M UTC").upper(),
           F["mono"](17), MUTED, anchor="ra")

    # --- ticker + chips -----------------------------------------------------
    fb = F["bold"](104)
    c.text((52, 118), sig.base, fb, TEXT)
    c.text((52 + c.tlen(sig.base, fb) + 6, 118), "/" + sig.quote, F["med"](104), DIM)

    fchip = F["semi"](21)
    x = _chip(c, 56, 252, "LONG" if sig.is_long else "SHORT", side_col, BG, fchip,
              tri="up" if sig.is_long else "down")
    for label in (sig.interval, sig.leverage, sig.exchange):
        if label:
            x = _chip(c, x, 252, label, PANEL, TEXT, fchip, outline=BORDER)
    if status:
        x = _chip(c, x, 252, "UPDATE", None, AMBER, fchip, outline=AMBER)

    # --- chart --------------------------------------------------------------
    _chart(c, sig, data, status, box=(56, 326, W - 56, 830))

    # --- ladder -------------------------------------------------------------
    hit = set()
    if status and status.kind == "tp":
        hit = set(range(1, status.tp_index + 1))
    rows = [(f"TP{i}", tp, UP, i in hit) for i, tp in enumerate(sig.tps, 1)]
    rows.append(("ENTRY", sig.entry, ENTRY, False))
    rows.append(("STOP", sig.sl, DOWN, status is not None and status.kind == "sl"))
    rows.sort(key=lambda r: -r[1])  # same order as on the chart

    top, bottom, gap = 856, 1178, 8
    rh = min(60, (bottom - top) / len(rows) - gap)
    top += ((bottom - top) - len(rows) * (rh + gap)) / 2
    fname, fprice, fpct, fr = F["semi"](21), F["monob"](25), F["monob"](21), F["mono"](19)
    for i, (name, price, col, done) in enumerate(rows):
        y0 = top + i * (rh + gap)
        y1, ym = y0 + rh, y0 + rh / 2
        c.rect((56, y0, W - 56, y1), fill=PANEL, outline=BORDER, r=12)
        c.rect((56, y0, 62, y1), fill=col, r=3)
        c.text((84, ym), name, fname, col if name != "ENTRY" else TEXT, anchor="lm")
        if name == "ENTRY":
            txt = (f"{sig.fmt(sig.entry_low)} – {sig.fmt(sig.entry_high)}"
                   if sig.is_zone else sig.fmt(price))
            c.text((210, ym), txt, fprice, TEXT, anchor="lm")
            c.text((W - 80, ym), "zone" if sig.is_zone else "market", fr, MUTED, anchor="rm")
            continue
        c.text((210, ym), sig.fmt(price), fprice, TEXT, anchor="lm")
        pips = sig.pips(price)
        c.text((660, ym), sig.pip_text(price), fpct,
               UP if pips > 0 else DOWN, anchor="rm")
        r = sig.r_multiple(price)
        c.text((790, ym), f"{'+' if name != 'STOP' else '−'}{r:.1f}R", fr, MUTED, anchor="rm")
        if done:
            c.rect((W - 200, ym - 17, W - 72, ym + 17), fill=col, r=17)
            _check(c, W - 182, ym, BG)
            c.text((W - 154, ym), "HIT", F["bold"](18), BG, anchor="lm")

    # --- stat tiles ---------------------------------------------------------
    tiles = [
        ("RISK : REWARD", f"1 : {sig.rr:.1f}"),
        ("STOP DISTANCE", sig.pip_text(sig.sl, sign=False)),
        ("SETUP", sig.setup) if sig.setup else ("TARGETS", f"{len(sig.tps)} TP"),
    ]
    tw = (W - 112 - 24) / 3
    for i, (label, val) in enumerate(tiles):
        x0 = 56 + i * (tw + 12)
        c.rect((x0, 1196, x0 + tw, 1282), fill=PANEL, outline=BORDER, r=14)
        c.text((x0 + 20, 1214), label, F["semi"](15), MUTED)
        fv = F["bold"](30)
        while c.tlen(val, fv) > tw - 40 and fv.size > 16 * K:
            fv = F["bold"](fv.size / K - 2)
        c.text((x0 + 20, 1262), val, fv, side_col if i == 0 else TEXT, anchor="ls")

    # --- footer -------------------------------------------------------------
    c.text((56, 1314), brand.disclaimer, F["reg"](16), DIM, anchor="lm")
    c.text((W - 56, 1314), brand.handle, F["semi"](17), MUTED, anchor="rm")

    out = Path(out)
    c.png(out)
    return out


def _chart(c: Canvas, sig: Signal, data: list[Candle], status: Status | None, box):
    bx0, by0, bx1, by1 = box
    c.rect(box, fill=PANEL + (235,), outline=BORDER, r=20)
    x0, x1, y0, y1 = bx0 + 24, bx1 - 24, by0 + 28, by1 - 28
    tag_w = 172
    lines_end = x1 - tag_w - 10
    cand_end = x0 + (lines_end - x0) * (0.70 if data else 0.0)

    levels = [sig.entry_low, sig.entry_high, sig.sl, *sig.tps]
    llo, lhi = min(levels), max(levels)
    span = lhi - llo
    # include price history, but never let it squash the levels to a sliver
    lo = max(min([llo] + [d[3] for d in data]), llo - span * 0.6)
    hi = min(max([lhi] + [d[2] for d in data]), lhi + span * 0.6)
    pad = (hi - lo) * 0.07
    lo, hi = lo - pad, hi + pad

    def Y(p):
        return min(y1, max(y0, y1 - (p - lo) / (hi - lo) * (y1 - y0)))

    # horizontal grid
    for i in range(1, 6):
        gy = y0 + (y1 - y0) * i / 6
        c.line([(x0, gy), (x1, gy)], (20, 26, 44), 1)

    # position-tool boxes, projected forward from the last candle
    zx0 = cand_end + 8 if data else x0
    c.alpha_rect((zx0, min(Y(sig.entry), Y(sig.tps[-1])), lines_end,
                  max(Y(sig.entry), Y(sig.tps[-1]))), UP + (40,))
    c.alpha_rect((zx0, min(Y(sig.entry), Y(sig.sl)), lines_end,
                  max(Y(sig.entry), Y(sig.sl))), DOWN + (40,))
    if sig.is_zone:
        c.alpha_rect((x0, Y(sig.entry_high), lines_end, Y(sig.entry_low)), ENTRY + (26,))

    # candles
    if data:
        step = (cand_end - x0) / len(data)
        bw = max(2.0, step * 0.62)
        for i, (_, o, h, l, cl) in enumerate(data):
            if l > hi or h < lo:  # entirely off-scale: leave the slot empty
                continue
            cx = x0 + step * (i + 0.5)
            col = UP if cl >= o else DOWN
            dim = tuple(int(v * 0.78 + b * 0.22) for v, b in zip(col, PANEL))
            c.line([(cx, Y(h)), (cx, Y(l))], dim, 1.6)
            top, bot = Y(max(o, cl)), Y(min(o, cl))
            c.rect((cx - bw / 2, top, cx + bw / 2, max(bot, top + 1.5)), fill=dim, r=1)
        last = data[-1][4]
        lx, ly = x0 + step * (len(data) - 0.5), Y(last)
        c.alpha_rect((lx - 13, ly - 13, lx + 13, ly + 13), TEXT + (40,), r=13)
        c.rect((lx - 5, ly - 5, lx + 5, ly + 5), fill=TEXT, r=5)
    else:
        c.text(((x0 + lines_end) / 2, y1 - 6), "live chart unavailable · levels to scale",
               F["mono"](15), DIM, anchor="ms")

    # level lines + tags (tags pushed apart so they never overlap)
    tags = [(f"TP{i}", tp, UP) for i, tp in enumerate(sig.tps, 1)]
    tags += [("ENTRY", sig.entry, ENTRY), ("SL", sig.sl, DOWN)]
    for _, p, col in tags:
        c.dashed(x0, lines_end, Y(p), col, 2)
    tags.sort(key=lambda t: Y(t[1]))
    th, min_gap = 32, 36
    ys = [Y(t[1]) for t in tags]
    for i in range(1, len(ys)):
        ys[i] = max(ys[i], ys[i - 1] + min_gap)
    over = ys[-1] + th / 2 - y1
    if over > 0:
        ys = [y - over for y in ys]
    for i in range(len(ys) - 2, -1, -1):
        ys[i] = min(ys[i], ys[i + 1] - min_gap)
    ft = F["monob"](16)
    for (name, p, col), ty in zip(tags, ys):
        ly = Y(p)
        tx0 = lines_end + 10
        if abs(ty - ly) > 1:
            c.line([(lines_end, ly), (tx0, ty)], col, 1.5)
        c.rect((tx0, ty - th / 2, x1, ty + th / 2), fill=col, r=8)
        c.text((tx0 + 10, ty), name, ft, BG, anchor="lm")
        c.text((x1 - 10, ty), sig.fmt(p), ft, BG, anchor="rm")

    if status:
        _stamp(c, sig, status, ((x0 + lines_end) / 2, (y0 + y1) / 2))


def _stamp(c: Canvas, sig: Signal, st: Status, center):
    if st.kind == "tp":
        title, col = f"TP{st.tp_index} HIT", UP
        price = sig.tps[st.tp_index - 1]
    elif st.kind == "sl":
        title, col, price = "STOPPED OUT", DOWN, sig.sl
    elif st.kind == "close":
        title, price = "CLOSED", st.price
        col = UP if price is not None and sig.pips(price) >= 0 else DOWN
    else:
        title, col, price = "CANCELLED", AMBER, None
    sub = ""
    if price is not None:
        r = sig.r_multiple(price) * (1 if sig.pips(price) >= 0 else -1)
        sub = f"{sig.pip_text(price)}  ·  {r:+.1f}R".replace("-", "−")
    elif st.kind == "cancel":
        sub = "entry not triggered"
    ft, fs = F["bold"](58), F["monob"](26)
    w = max(c.tlen(title, ft), c.tlen(sub, fs)) + 80
    cx, cy = center
    h = 150 if sub else 104
    box = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
    c.alpha_rect(box, BG + (225,), r=22)
    c.rect(box, outline=col, r=22, width=3)
    c.text((cx, cy - (22 if sub else 0)), title, ft, col, anchor="mm")
    if sub:
        c.text((cx, cy + 40), sub, fs, TEXT, anchor="mm")
