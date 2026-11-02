"""The weekly LinkedIn cards: the 10 most-covered tech stocks and the 10 most-covered UK tech stocks,
each with a post to go with it.

Built from the same headline counts as the weekly draft's "Who got the attention" (coverage.attention):
each article the site would use that names a stock in its headline counts once for it.

Coverage only. The cards and posts never say a stock is good or bad, never predict a price and carry
the not-advice line, same as every page on the site.

Output in data/linkedin/: <end date>-all.png and .txt, <end date>-uk.png and .txt, plus latest-all.*
and latest-uk.*. 1080 x 1350 is LinkedIn's tallest feed image (4:5).
"""
from __future__ import annotations

from datetime import date as Date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw

from . import coverage
from .config import ROOT, Config
from .cover import BRIGHT, DARK, LIGHT, PANEL, WHITE, _sans, _serif, _wrap
from .db import DB

W, H = 1080, 1350
PAD = 72
TRACK = "#1b2c44"
CARDS = {
    "all": {"title": "The {n} most-covered tech stocks last week", "who": "tech stock",
            "hashtags": ["#techstocks", "#stockmarket", "#technews"]},
    "uk": {"title": "The {n} most-covered UK tech stocks last week", "who": "UK tech stock",
           "hashtags": ["#ukstocks", "#ftse", "#uktech"]},
}


def _settings(cfg: Config) -> dict:
    li = cfg.rules.get("linkedin", {}) or {}
    return {
        "enabled": li.get("enabled", True),
        "day": li.get("day", 0),
        "build_hour": li.get("build_hour", 7),
        "late_window_hours": li.get("late_window_hours", 6),
        "count": li.get("count", 10),
        "subscribe_url": li.get("subscribe_url") or cfg.site.get("newsletter_url", "https://alltechstocks.substack.com"),
    }


def week(now: datetime, tz: ZoneInfo) -> tuple[Date, Date]:
    """The last seven full UK days, ending yesterday. Built on a Monday, that's Monday to Sunday."""
    end = now.astimezone(tz).date() - timedelta(days=1)
    return end - timedelta(days=6), end


def gather(db: DB, cfg: Config, now: datetime) -> dict:
    s = _settings(cfg)
    start, end = week(now, ZoneInfo(cfg.site["timezone"]))
    every = coverage.attention(db, cfg, start, end, [], n=len(cfg.stocks))["top"]
    return {"start": start, "end": end, "settings": s, "every": every,
            "lists": {"all": every[:s["count"]], "uk": [r for r in every if r["stock"].market == "UK"][:s["count"]]}}


def _when(start: Date, end: Date) -> str:
    nb = "\u00a0"  # non-breaking space, so a date never splits across two lines
    left = f"{start:%-d %b}" if start.year == end.year else f"{start:%-d %b %Y}"
    return f"{left} to {end:%-d %b %Y}".replace(" ", nb).replace(f"{nb}to{nb}", " to ")


def _ticker(stock) -> str:
    return f"{stock.exchange}: {stock.symbol}"


def render(facts: dict, which: str, out: Path) -> Path:
    rows = facts["lists"][which]
    img = Image.new("RGB", (W, H), DARK)
    d = ImageDraw.Draw(img)

    # Header: logo tile, name, week.
    logo = Image.open(ROOT / "static" / "logo-512.png").convert("RGBA").resize((68, 68), Image.LANCZOS)
    img.paste(logo, (PAD, PAD - 6), logo)
    d.text((PAD + 88, PAD - 2), "ALL TECH STOCKS", font=_sans(30, 800), fill=WHITE)
    d.text((PAD + 88, PAD + 34), f"WEEK TO {facts['end']:%A %-d %B}".upper(), font=_sans(22, 600), fill=BRIGHT)

    # Title, as big as it will go on two lines.
    title = CARDS[which]["title"].format(n=len(rows))
    size = 74
    while True:
        tf = _serif(size)
        lines = _wrap(d, title, tf, W - 2 * PAD)
        if len(lines) <= 2 or size <= 48:
            break
        size -= 2
    y = 172
    for line in lines:
        d.text((PAD, y), line, font=tf, fill=WHITE)
        y += int(size * 1.1)

    # Rows: rank, name and ticker on the left, headline count and a bar on the right.
    top = y + 34
    row_h = min(88, (H - 232 - top) // max(len(rows), 1))
    num_right, bar_x0, bar_x1 = 780, 812, W - PAD
    most = max((r["articles"] for r in rows), default=1)
    rank_f, tag_f, num_f = _sans(28, 800), _sans(20, 500), _sans(36, 900)
    for i, r in enumerate(rows):
        ry = top + i * row_h
        if i:
            d.line([PAD, ry - 9, W - PAD, ry - 9], fill=TRACK, width=2)
        d.text((PAD, ry + 8), f"{i + 1:>2}", font=rank_f, fill=BRIGHT)
        name, size = r["stock"].name, 32
        nf = _sans(size, 800)
        while d.textlength(name, font=nf) > num_right - 170 - (PAD + 62) and size > 24:
            size -= 2
            nf = _sans(size, 800)
        d.text((PAD + 62, ry), name, font=nf, fill=WHITE)
        d.text((PAD + 62, ry + 38), f"{_ticker(r['stock'])} · {r['outlets']} outlet{'' if r['outlets'] == 1 else 's'}", font=tag_f, fill=LIGHT)
        num = f"{r['articles']:,}"
        d.text((num_right - d.textlength(num, font=num_f), ry + 12), num, font=num_f, fill=WHITE)
        by = ry + 26
        d.rounded_rectangle([bar_x0, by, bar_x1, by + 16], radius=8, fill=TRACK)
        fill_w = max(16, int((bar_x1 - bar_x0) * r["articles"] / most))
        d.rounded_rectangle([bar_x0, by, bar_x0 + fill_w, by + 16], radius=8, fill=BRIGHT if i == 0 else PANEL)

    # What the number means, the not-advice line, then the footer.
    note_f = _sans(22, 500)
    note = (f"Headlines naming each company, across the US and UK financial and tech press we track, {_when(facts['start'], facts['end'])}. "
            "News coverage only. Not investment advice.")
    ny = H - 208
    for line in _wrap(d, note, note_f, W - 2 * PAD):
        d.text((PAD, ny), line, font=note_f, fill=LIGHT)
        ny += 30
    d.text((PAD, H - PAD - 52), "alltechstocks.com", font=_sans(32, 700), fill=LIGHT)
    tag = "Ranked by who's covering it"
    tf2 = _sans(24, 600)
    d.text((W - PAD - d.textlength(tag, font=tf2), H - PAD - 46), tag, font=tf2, fill=WHITE)

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, optimize=True)
    return out


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _words(n: int) -> str:
    return ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"][n] if n < 10 else str(n)


def _ahead(rows: list) -> int:
    """How many of the following names the leader outscored put together. 0 if not even two."""
    if len(rows) < 3:
        return 0
    lead, total, k = rows[0]["articles"], 0, 0
    for r in rows[1:]:
        if total + r["articles"] >= lead:
            break
        total += r["articles"]
        k += 1
    return k if k >= 2 else 0


def teaser(facts: dict, which: str) -> str:
    """The LinkedIn post text. The first line is a suggestion: rewrite it in your own voice.
    Facts about coverage only: no verdicts on the shares, no predictions."""
    s, rows, card = facts["settings"], facts["lists"][which], CARDS[which]
    if not rows:
        return ""
    first, k = rows[0], _ahead(rows)
    if k:
        lead = (f"{first['stock'].name} was named in {first['articles']:,} headlines last week, more than the "
                f"next {_words(k)} most-covered {card['who']}s put together.")
    else:
        lead = f"{first['stock'].name} was the most-covered {card['who']} last week, named in {first['articles']:,} headlines."
    if which == "all":
        uk = facts["lists"]["uk"]
        # Say plainly whether any UK name made this top 10, so the line can't read as if it's on the card.
        in_top = [r for r in rows if r["stock"].market == "UK"]
        if in_top:
            names_uk = " and ".join(r["stock"].name for r in in_top[:2])
            lead += (f" {names_uk} {'was the only UK name' if len(in_top) == 1 else 'were the UK names'} "
                     f"in the top {len(rows)}.")
        elif uk:
            place = next(i for i, r in enumerate(facts["every"], 1) if r["symbol"] == uk[0]["symbol"])
            lead += (f" No UK tech stock made the top {len(rows)}. The closest was {uk[0]['stock'].name}, "
                     f"in {_ordinal(place)} place.")
    names = [f"{r['stock'].name} ({r['symbol']})" for r in rows]
    listed = ", ".join(names[:-1]) + f" and {names[-1]}" if len(names) > 1 else names[0]
    return "\n".join([
        "[Suggested opening line. Rewrite it, then delete this line.]",
        lead,
        "",
        f"The {len(rows)} most-covered {card['who']}s last week, ranked by how many headlines named them across the US and UK financial and tech press we track:",
        "",
        listed + ".",
        "",
        "News coverage only, not investment advice.",
        "",
        "US and UK tech stock news, ranked by who's covering it: https://alltechstocks.com",
        f"Get the weekday email: {s['subscribe_url']}",
        "",
        " ".join(card["hashtags"]),
    ]) + "\n"


def build(db: DB, cfg: Config, now: datetime, folder: Path) -> dict:
    facts = gather(db, cfg, now)
    day = facts["end"].isoformat()
    made = []
    for which, rows in facts["lists"].items():
        if len(rows) < 3:  # a thin week (or a fresh database) makes a card not worth posting
            continue
        png = render(facts, which, folder / f"{day}-{which}.png")
        (folder / f"latest-{which}.png").write_bytes(png.read_bytes())
        text = teaser(facts, which)
        for name in (f"{day}-{which}.txt", f"latest-{which}.txt"):
            (folder / name).write_text(text, encoding="utf-8")
        made.append(which)
    return {"date": day, "cards": made}


def built(folder: Path, now: datetime, cfg: Config) -> bool:
    _, end = week(now, ZoneInfo(cfg.site["timezone"]))
    return (folder / f"{end.isoformat()}-all.png").exists()


def due(local: datetime, cfg: Config) -> bool:
    s = _settings(cfg)
    return (s["enabled"] and local.weekday() == s["day"]
            and s["build_hour"] <= local.hour < s["build_hour"] + s["late_window_hours"])
