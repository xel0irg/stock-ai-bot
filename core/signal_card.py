"""
core/signal_card.py — Render a dashboard-style signal card as a PNG

Produces an image matching the dashboard's "Active Setups" card so
Discord alerts are readable at a glance, while the embed below keeps
the full text breakdown.

Rendered with Pillow only — no browser, works on the ephemeral
GitHub Actions runner. Renders at 2x scale for crispness on retina/
mobile Discord clients.

Fail-safe by design: render_signal_card() returns bytes on success or
None on ANY failure, and the Discord notifier falls back to the plain
text embed. A rendering bug can never block an alert.
"""
from __future__ import annotations

import io
from typing import Any, Dict, Optional

from core.logger import get_logger

log = get_logger("SignalCard")

# ── Palette (matches dashboard.html dark theme) ──────────────────────
BG          = (11, 11, 13)      # page background
CARD        = (19, 19, 22)      # card surface
BOX         = (26, 26, 30)      # stat box surface
BORDER      = (38, 38, 43)
TEXT        = (229, 231, 235)   # near-white
MUTED       = (107, 114, 128)   # gray labels
RED         = (239, 68, 68)
GREEN       = (34, 197, 94)
AMBER       = (245, 158, 11)
ORANGE      = (249, 115, 22)

QUALITY_COLORS = {
    "HIGH CONVICTION": GREEN,
    "MODERATE":        AMBER,
    "LOW CONVICTION":  MUTED,
}

S = 2  # supersample scale — everything below is in 1x units, multiplied by S


def _load_fonts():
    """Best-effort font loading across GitHub runner (Linux) and Windows."""
    from PIL import ImageFont

    candidates = {
        "bold": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/segoeuib.ttf",
        ],
        "regular": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/segoeui.ttf",
        ],
        "mono": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "C:/Windows/Fonts/consola.ttf",
        ],
    }

    def pick(kind: str, size: int):
        for path in candidates[kind]:
            try:
                return ImageFont.truetype(path, size * S)
            except (OSError, IOError):
                continue
        return ImageFont.load_default()

    return {
        "ticker":    pick("bold", 30),
        "price":     pick("regular", 18),
        "pill":      pick("bold", 13),
        "score":     pick("bold", 16),
        "label":     pick("bold", 10),
        "value":     pick("bold", 18),
        "body":      pick("regular", 12),
        "body_bold": pick("bold", 12),
    }


def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    """Greedy word-wrap to pixel width."""
    words = (text or "").split()
    lines, cur = [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _pill(draw, x: int, y: int, text: str, font, fg, right_align_to: Optional[int] = None) -> int:
    """Draw a rounded pill. Returns its left x. Coordinates in 1x units."""
    pad_x, pad_y = 12 * S, 6 * S
    tw = draw.textlength(text, font=font)
    w = int(tw + pad_x * 2)
    h = int(font.size + pad_y * 2)
    if right_align_to is not None:
        x = right_align_to * S - w
    else:
        x = x * S
    y = y * S
    bg = tuple(int(c * 0.22) for c in fg)
    draw.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=bg, outline=fg, width=S)
    draw.text((x + pad_x, y + pad_y - S), text, font=font, fill=fg)
    return x // S


def render_signal_card(
    ticker: str,
    tech: Dict[str, Any],
    ai: Dict[str, Any],
) -> Optional[bytes]:
    """
    Render the signal as a PNG card. Returns PNG bytes, or None on any
    failure (caller falls back to text-only alert).
    """
    try:
        from PIL import Image, ImageDraw

        ta = tech.get("technicals", {}) or {}
        ts = ai.get("trade_setup", {}) or {}
        fresh = ai.get("freshness", {}) or {}

        contract = ts.get("contract_type", "NONE")
        if contract not in ("CALL", "PUT"):
            return None  # cards only for actionable setups

        quality  = ts.get("setup_quality", "")
        score    = ai.get("confluence_score", 0) or 0
        is_stale = bool(fresh.get("is_stale"))

        last_price = ta.get("last_price")
        ret_1d     = ta.get("return_1d", 0) or 0

        con_color  = GREEN if contract == "CALL" else RED
        qual_color = QUALITY_COLORS.get(quality.replace("STALE — ", ""), MUTED)

        # ── Layout ────────────────────────────────────────────────
        W = 1100
        PAD = 28
        fonts = _load_fonts()

        # Pre-measure bottom text columns to size the canvas
        tmp = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        col_w = (W - PAD * 2 - 40) // 3
        cols = [
            ("ENTER WHEN", ts.get("entry_condition", "") or "—", GREEN),
            ("AVOID IF",   ts.get("avoid_if", "") or "—",        AMBER),
            ("KEY RISK",   ts.get("key_risk", "") or "—",        RED),
        ]
        wrapped = [_wrap(tmp, body, fonts["body"], col_w * S) for _, body, _ in cols]
        max_lines = min(max(len(w) for w in wrapped), 8)
        line_h = 18

        stale_h  = 44 if is_stale else 0
        header_h = 96
        stats_h  = 84
        bottom_h = 30 + max_lines * line_h + 10
        H = PAD + stale_h + header_h + stats_h + bottom_h + PAD

        img = Image.new("RGB", (W * S, H * S), BG)
        d = ImageDraw.Draw(img)

        # Card surface with left accent bar
        d.rounded_rectangle([8 * S, 8 * S, (W - 8) * S, (H - 8) * S],
                            radius=14 * S, fill=CARD, outline=BORDER, width=S)
        d.rounded_rectangle([8 * S, 8 * S, 13 * S, (H - 8) * S],
                            radius=2 * S, fill=con_color)

        y = PAD

        # ── STALE banner ──────────────────────────────────────────
        if is_stale:
            d.rounded_rectangle([PAD * S, y * S, (W - PAD) * S, (y + 34) * S],
                                radius=8 * S, fill=(60, 18, 18), outline=RED, width=S)
            d.text(((PAD + 14) * S, (y + 8) * S,),
                   "⚠ STALE SIGNAL — move already happened before this alert. Do not chase.",
                   font=fonts["body_bold"], fill=RED)
            y += stale_h

        # ── Header row ────────────────────────────────────────────
        hx = PAD + 8
        d.text((hx * S, y * S), ticker.upper(), font=fonts["ticker"], fill=TEXT)
        tick_w = d.textlength(ticker.upper(), font=fonts["ticker"]) / S

        px = hx + tick_w + 16
        if last_price is not None:
            d.text((px * S, (y + 10) * S), f"${last_price:,.2f}", font=fonts["price"], fill=TEXT)
            px += d.textlength(f"${last_price:,.2f}", font=fonts["price"]) / S + 12
        chg_color = GREEN if ret_1d >= 0 else RED
        d.text((px * S, (y + 12) * S), f"{ret_1d:+.2f}%", font=fonts["pill"], fill=chg_color)

        # Right side: pills (drawn right-to-left)
        pill_y = y + 6
        left = _pill(d, 0, pill_y, f"{contract} · {ts.get('expiry') or '?DTE'}",
                     fonts["pill"], con_color, right_align_to=W - PAD - 8)
        left = _pill(d, 0, pill_y, quality.replace("STALE — ", ""),
                     fonts["pill"], qual_color, right_align_to=left - 10)
        score_txt = f"{score}/100"
        d.text(((left - 10) * S - d.textlength(score_txt, font=fonts["score"]),
                (pill_y + 5) * S), score_txt, font=fonts["score"], fill=TEXT)

        # Score progress bar
        bar_y = y + 52
        bar_w = W - PAD * 2 - 16
        d.rounded_rectangle([(hx) * S, bar_y * S, (hx + bar_w) * S, (bar_y + 5) * S],
                            radius=2 * S, fill=BOX)
        fill_w = int(bar_w * min(max(score, 0), 100) / 100)
        if fill_w > 4:
            d.rounded_rectangle([(hx) * S, bar_y * S, (hx + fill_w) * S, (bar_y + 5) * S],
                                radius=2 * S, fill=AMBER if score < 70 else GREEN)
        y += header_h

        # ── Stat boxes ────────────────────────────────────────────
        def fmt_money(v):  return f"${v:g}" if v not in (None, "") else "—"
        def fmt_pct(v):    return f"{v}%" if v not in (None, "") else "—"

        def _pct_signed(v, fallback=None):
            # Premium-move exits: always signed so +55% / -30% read as
            # "premium up 55%" / "premium down 30%", not price levels.
            if v in (None, ""):
                v = fallback
            if v in (None, ""):
                return "—"
            try:
                n = float(v)
            except (TypeError, ValueError):
                return str(v)
            return f"{'+' if n > 0 else ''}{n:g}%"

        stats = [
            ("STRIKE",        fmt_money(ts.get("strike")),        con_color),
            ("MONEYNESS",     (ts.get("moneyness") or "—").replace("SLIGHTLY_OTM", "SL.OTM"), TEXT),
            ("STOCK TARGET",  fmt_money(ts.get("stock_target")),  con_color),
            ("PREMIUM",       fmt_money(ts.get("est_premium")),   TEXT),
            ("TAKE PROFIT",   _pct_signed(ts.get("premium_target_pct"),
                                            ts.get("profit_target")), GREEN),
            ("STOP",          _pct_signed(ts.get("premium_stop_pct")), RED),
        ]
        n = len(stats)
        gap = 10
        box_w = (W - PAD * 2 - 16 - gap * (n - 1)) // n
        bx = hx
        for label, value, vcolor in stats:
            d.rounded_rectangle([bx * S, y * S, (bx + box_w) * S, (y + 64) * S],
                                radius=8 * S, fill=BOX, outline=BORDER, width=S)
            d.text(((bx + 12) * S, (y + 10) * S), label, font=fonts["label"], fill=MUTED)
            d.text(((bx + 12) * S, (y + 30) * S), str(value), font=fonts["value"], fill=vcolor)
            bx += box_w + gap
        y += stats_h

        # ── Bottom text columns ───────────────────────────────────
        cx = hx
        for (label, _body, lcolor), lines in zip(cols, wrapped):
            d.text((cx * S, y * S), label, font=fonts["label"], fill=lcolor)
            ly = y + 20
            for line in lines[:8]:
                d.text((cx * S, ly * S), line, font=fonts["body"], fill=TEXT)
                ly += line_h
            cx += col_w + 20

        # Downscale for anti-aliasing
        img = img.resize((W, H), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        log.info(f"Signal card rendered for {ticker} ({len(buf.getvalue())//1024} KB)")
        return buf.getvalue()

    except Exception as e:
        log.warning(f"Signal card render failed for {ticker} — falling back to text embed: {e}")
        return None
