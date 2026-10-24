"""Coverage history and spikes.

record_daily() runs every hour after the site is built. It rolls the last month of stock stories
into one row per stock per UK day (stock_daily) and the last fortnight of articles into one row per
section per day (section_daily). Those two tables are never pruned, so the history grows forever
while the rest of the database stays small.

A day keeps changing while stories on it are still picking up outlets, so recent days are rebuilt
on every run. A day stops changing once it's older than the source data: 29 days for stocks
(stock_stories keeps 30) and 13 for sections (items keeps 14).

spikes() compares a day with the stock's own normal level. It describes attention only: how much
the press wrote, never what the shares might do.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date as Date, datetime, timedelta
from zoneinfo import ZoneInfo

from .config import Config
from .db import DB, iso, parse_iso

STOCK_DAYS = 29
SECTION_DAYS = 13


def _uk_date(s: str, tz: ZoneInfo) -> str:
    return parse_iso(s).astimezone(tz).strftime("%Y-%m-%d")


def record_daily(db: DB, cfg: Config, now: datetime) -> dict:
    tz = ZoneInfo(cfg.site["timezone"])
    c = db.conn

    # Stocks: a story counts on the UK day it was first seen.
    stock_from = (now - timedelta(days=STOCK_DAYS)).astimezone(tz).strftime("%Y-%m-%d")
    rows: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"stories": 0, "headline_stories": 0, "outlets": 0, "attention": 0.0})
    for r in c.execute("SELECT symbol, outlets, in_headline, first_seen FROM stock_stories").fetchall():
        d = _uk_date(r["first_seen"], tz)
        if d < stock_from:
            continue
        row = rows[(d, r["symbol"])]
        row["stories"] += 1
        row["headline_stories"] += r["in_headline"]
        row["outlets"] += r["outlets"]
        row["attention"] += r["outlets"] * (1.0 if r["in_headline"] else 0.5)
    c.execute("DELETE FROM stock_daily WHERE date >= ?", (stock_from,))
    c.executemany(
        "INSERT INTO stock_daily (date, symbol, stories, headline_stories, outlets, attention) VALUES (?, ?, ?, ?, ?, ?)",
        [(d, sym, v["stories"], v["headline_stories"], v["outlets"], v["attention"]) for (d, sym), v in rows.items()])

    # Sections: kept articles by UK publication day.
    section_from = (now - timedelta(days=SECTION_DAYS)).astimezone(tz).strftime("%Y-%m-%d")
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for r in c.execute("SELECT section, published FROM items WHERE excluded IS NULL AND section IS NOT NULL").fetchall():
        d = _uk_date(r["published"], tz)
        if d >= section_from:
            counts[(d, r["section"])] += 1
    c.execute("DELETE FROM section_daily WHERE date >= ?", (section_from,))
    c.executemany("INSERT INTO section_daily (date, section, items) VALUES (?, ?, ?)",
                  [(d, sec, n) for (d, sec), n in counts.items()])
    return {"stock_rows": len(rows), "section_rows": len(counts)}


def history_days(db: DB) -> int:
    row = db.conn.execute("SELECT MIN(date) AS a, MAX(date) AS b FROM stock_daily").fetchone()
    if not row["a"]:
        return 0
    return (Date.fromisoformat(row["b"]) - Date.fromisoformat(row["a"])).days + 1


def spikes(db: DB, day: str, baseline_days: int = 28, min_history: int = 14,
           min_attention: float = 6.0, min_ratio: float = 3.0) -> list[dict]:
    """Stocks with far more coverage on `day` than their own normal. The baseline is the mean daily
    attention over the previous `baseline_days` days that we have data for, with quiet days counted
    as zero. Returns [] until there are `min_history` days of history, because a baseline built on
    a few days is noise."""
    first = db.conn.execute("SELECT MIN(date) AS d FROM stock_daily").fetchone()["d"]
    if not first:
        return []
    d = Date.fromisoformat(day)
    start = max(Date.fromisoformat(first), d - timedelta(days=baseline_days))
    span = (d - start).days
    if span < min_history:
        return []
    base = {r["symbol"]: r["total"] for r in db.conn.execute(
        "SELECT symbol, SUM(attention) AS total FROM stock_daily WHERE date >= ? AND date < ? GROUP BY symbol",
        (start.isoformat(), day)).fetchall()}
    out = []
    for r in db.conn.execute("SELECT * FROM stock_daily WHERE date = ?", (day,)).fetchall():
        normal = base.get(r["symbol"], 0.0) / span
        ratio = (r["attention"] + 1) / (normal + 1)  # +1 so a stock that's usually silent can't hit x50 on one story
        if r["attention"] >= min_attention and r["stories"] >= 2 and ratio >= min_ratio:
            out.append({"symbol": r["symbol"], "attention": r["attention"], "stories": r["stories"],
                        "outlets": r["outlets"], "normal": round(normal, 1), "ratio": round(ratio, 1)})
    return sorted(out, key=lambda x: -x["ratio"])


def new_on_radar(db: DB, day: str, quiet_days: int = 60, min_attention: float = 3.0) -> list[dict]:
    """Stocks covered on `day` after no coverage at all for `quiet_days`. Empty until there's that
    much history, so a stock isn't called new just because tracking only started last week."""
    first = db.conn.execute("SELECT MIN(date) AS d FROM stock_daily").fetchone()["d"]
    d = Date.fromisoformat(day)
    start = d - timedelta(days=quiet_days)
    if not first or Date.fromisoformat(first) > start:
        return []
    rows = db.conn.execute(
        """SELECT t.* FROM stock_daily t WHERE t.date = ? AND t.attention >= ? AND NOT EXISTS
           (SELECT 1 FROM stock_daily p WHERE p.symbol = t.symbol AND p.date >= ? AND p.date < ?)""",
        (day, min_attention, start.isoformat(), day)).fetchall()
    return [dict(r) for r in sorted(rows, key=lambda r: -r["attention"])]


def section_share(db: DB, days: int = 7, until: str | None = None) -> list[dict]:
    """Each section's share of kept articles over the last `days` days, against the `days` before."""
    until_d = Date.fromisoformat(until) if until else Date.fromisoformat(
        db.conn.execute("SELECT MAX(date) AS d FROM section_daily").fetchone()["d"] or Date.today().isoformat())
    def window(a: Date, b: Date) -> dict[str, int]:
        return {r["section"]: r["n"] for r in db.conn.execute(
            "SELECT section, SUM(items) AS n FROM section_daily WHERE date > ? AND date <= ? GROUP BY section",
            (a.isoformat(), b.isoformat())).fetchall()}
    now_w = window(until_d - timedelta(days=days), until_d)
    prev_w = window(until_d - timedelta(days=2 * days), until_d - timedelta(days=days))
    tn, tp = sum(now_w.values()) or 1, sum(prev_w.values())
    out = []
    for sec in sorted(set(now_w) | set(prev_w)):
        share = now_w.get(sec, 0) / tn
        prev = prev_w.get(sec, 0) / tp if tp else None
        out.append({"section": sec, "items": now_w.get(sec, 0), "share": round(100 * share, 1),
                    "previous_share": round(100 * prev, 1) if prev is not None else None})
    return sorted(out, key=lambda x: -x["share"])


def report(db: DB, cfg: Config, now: datetime, day: str | None = None) -> str:
    tz = ZoneInfo(cfg.site["timezone"])
    day = day or (now.astimezone(tz) - timedelta(days=1)).strftime("%Y-%m-%d")  # yesterday is complete
    lines = [f"Coverage trends for {day} ({history_days(db)} days of history)"]
    sp = spikes(db, day)
    lines.append("\nCoverage spikes (attention vs the stock's normal day):")
    if not sp:
        lines.append("  none, or not enough history yet (needs 14 days)")
    for x in sp:
        name = cfg.by_symbol[x["symbol"]].name if x["symbol"] in cfg.by_symbol else x["symbol"]
        lines.append(f"  {name} ({x['symbol']}): {x['ratio']}x normal, {x['stories']} stories, "
                     f"{x['outlets']} outlet mentions (normal day {x['normal']})")
    nr = new_on_radar(db, day)
    lines.append("\nNew on the radar (first coverage in 60 days):")
    lines.append("  " + (", ".join(r["symbol"] for r in nr) if nr else "none, or not enough history yet (needs 60 days)"))
    lines.append("\nSection share, last 7 days (previous 7 days):")
    for x in section_share(db, until=day):
        prev = f"{x['previous_share']}%" if x["previous_share"] is not None else "no data"
        lines.append(f"  {x['section']}: {x['share']}% ({prev})")
    return "\n".join(lines)
