"""What the press did this week, measured from the archive. Used by the weekly draft.

Everything here is counted across the outlets we track, never the whole press. That limit decides
what we can say: we never claim who broke a story, because with thousands of outlets publishing we
can't know who was first. Timings start from the first article we saw, and are described that way.

Inputs are the week's story groups (weekly._story_groups) and the saved articles behind them.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date as Date
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import Config
from .db import DB

BIG = 10  # default: a story with this many outlets is a big story (weekly.big_story_outlets overrides)


def _when(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _key(title: str) -> str:
    """A headline reduced to its words, without the " - Outlet" tail aggregators add."""
    t = re.sub(r"\s+[-|–]\s+[^-|–]{2,40}$", "", title or "")
    return re.sub(r"\W+", " ", t.lower()).strip()


def _own(arts: list[dict]) -> list[dict]:
    """Each headline once, at its earliest. A wire story republished word for word by other outlets
    isn't more outlets picking it up, so it mustn't make a story look fast or short."""
    seen, out = set(), []
    for a in arts:
        k = _key(a.get("title", ""))
        if k and k not in seen:
            seen.add(k)
            out.append(a)
    return out


def _articles(db: DB, ids) -> list[dict]:
    ids = list(ids)
    if not ids:
        return []
    out = []
    for i in range(0, len(ids), 500):  # SQLite caps the number of ? in one query
        chunk = ids[i:i + 500]
        out += [dict(r) for r in db.conn.execute(
            f"SELECT id, outlet_id, outlet_name, published, title FROM items WHERE id IN ({','.join('?' * len(chunk))})",
            chunk)]
    return sorted(out, key=lambda r: r["published"])


def _profile(db: DB, g: dict, tz: ZoneInfo, big: int = BIG) -> dict:
    """How one story played out across the outlets we track."""
    arts = _articles(db, g["ids"])
    own = _own(arts)  # timings use each headline once, counts use every article
    seen, outlet_times = set(), []
    for a in own:
        o = a["outlet_id"] or a["outlet_name"]
        if o and o not in seen:
            seen.add(o)
            outlet_times.append(_when(a["published"]))
    t0 = _when(arts[0]["published"]) if arts else None
    hours = lambda t: round((t - t0).total_seconds() / 3600, 1)
    # Most of the coverage: the time by which 90% of the articles were out. The last 10% is
    # stragglers and round-ups, which would make every story look like it ran for days.
    k = max(0, int(len(own) * 0.9) - 1)
    by_day = Counter(_when(a["published"]).astimezone(tz).date() for a in arts)
    return {
        "articles": len(arts),
        "outlets_seen": len(outlet_times),
        "to_big": hours(outlet_times[big - 1]) if len(outlet_times) >= big else None,
        "most_within": hours(_when(own[k]["published"])) if own else None,
        "own": len(own),
        "peak_day": max(by_day, key=by_day.get) if by_day else None,
        "days_running": len(by_day),
    }


NAME = re.compile(r"\b[A-Z][A-Za-z0-9&'.-]+")
COMMON = {"The", "A", "An", "In", "On", "To", "For", "Of", "And", "As", "At", "By", "With", "Is", "Its", "It", "Says",
          "Said", "After", "Exclusive", "Report", "Reports", "New", "Inc", "Corp", "Shares", "Stock", "Deal", "Q1", "Q2",
          "Q3", "Q4", "AI", "US", "UK", "CEO", "Over", "Up", "Down", "From", "Into", "Will", "Be", "Are", "Has", "Have"}


def _names(headline: str) -> set[str]:
    return {w.strip(".'") for w in NAME.findall(headline or "")} - COMMON


def same_story(a: dict, b: dict) -> bool:
    """Two saved stories about one event: the talks and the deal, say. They share two or more names."""
    return len(_names(a["headline"]) & _names(b["headline"])) >= 2


def big_three(db: DB, groups: list[dict], tz: ZoneInfo, skip, big: int = BIG) -> list[dict]:
    """The three stories the most outlets wrote up, with how each one played out. One slot per event."""
    top: list[dict] = []
    for g in sorted((g for g in groups if not skip(g)), key=lambda g: -g["outlets"]):
        if not any(same_story(g, t) for t in top):
            top.append(g)
        if len(top) == 3:
            break
    return [dict(g, **_profile(db, g, tz, big)) for g in top]


def pace(db: DB, groups: list[dict], tz: ZoneInfo, skip, big: int = BIG) -> dict:
    """Among the week's big stories: the quickest to spread, the longest running, the quickest to fade."""
    rows = [dict(g, **_profile(db, g, tz, big)) for g in groups if g["outlets"] >= big and not skip(g)]
    timed = [r for r in rows if r["to_big"] is not None]
    lasting = [r for r in rows if r["most_within"] is not None and r["own"] >= 3]  # too few to judge otherwise
    return {
        "count": len(rows), "big": big,
        "fastest": min(timed, key=lambda r: r["to_big"]) if timed else None,
        "longest": max(lasting, key=lambda r: r["most_within"]) if lasting else None,
        "shortest": min(lasting, key=lambda r: r["most_within"]) if len(lasting) > 1 else None,
    }


def attention(db: DB, cfg: Config, start: Date, end: Date, groups: list[dict], n: int = 8) -> dict:
    """Share of the week's coverage by company, how concentrated it was and the US/UK split.
    Counted from the articles themselves: each kept article that names a company counts once for it."""
    from .tickers import Tagger
    tagger = Tagger(cfg)
    arts, outlets = Counter(), {}
    for r in db.conn.execute("SELECT title, outlet_name FROM items WHERE excluded IS NULL AND published >= ? "
                             "AND published < ?", _bounds(start, end)):
        for sym in tagger.find(r["title"]):
            arts[sym] += 1
            outlets.setdefault(sym, set()).add(r["outlet_name"])
    stories = Counter(sym for g in groups if g["outlets"] >= 2 for sym in g["symbols"])
    total = sum(arts.values()) or 1
    rows = [{"symbol": sym, "stock": cfg.by_symbol[sym], "articles": c, "outlets": len(outlets[sym]),
             "stories": stories.get(sym, 0), "share": 100 * c / total}
            for sym, c in arts.most_common() if sym in cfg.by_symbol]
    markets = Counter()
    for r in rows:
        markets[r["stock"].market] += r["share"]
    uk = [s for s in cfg.stocks if s.market == "UK"]
    named = set(arts)
    return {
        "top": rows[:n],
        "top3_share": sum(r["share"] for r in rows[:3]),
        "companies_covered": len(named),
        "markets": {m: round(v) for m, v in markets.items()},
        "uk_count": len(uk),
        "uk_covered": sum(1 for s in uk if s.symbol in named),
        "quiet": quiet(cfg, named),
    }


def _bounds(start: Date, end: Date) -> tuple[str, str]:
    return f"{start.isoformat()}T00:00:00Z", f"{(end + timedelta(days=1)).isoformat()}T00:00:00Z"


def quiet(cfg: Config, named: set[str]) -> list:
    """Companies no kept article named all week, so the press really did ignore them (as far as the
    outlets we track go). A company whose plain name is an ordinary word (Wise, say) is only matched in
    stricter forms, so we can't tell it was ignored. Only call a company quiet when we'd have spotted it."""
    matchable = lambda s: s.name.lower() in {a.lower() for a in s.aliases} or s.name in s.exact
    return sorted((s for s in cfg.stocks if s.symbol not in named and matchable(s)), key=lambda s: s.name)


def _shares(db: DB, cfg: Config, start: Date, end: Date) -> dict[str, float]:
    from .tickers import Tagger
    tagger = Tagger(cfg)
    c = Counter()
    for r in db.conn.execute("SELECT title FROM items WHERE excluded IS NULL AND published >= ? AND published < ?",
                             _bounds(start, end)):
        c.update(s for s in tagger.find(r["title"]) if s in cfg.by_symbol)
    tot = sum(c.values()) or 1
    return {s: 100 * v / tot for s, v in c.items()}


def movers(db: DB, cfg: Config, start: Date, end: Date, n: int = 4) -> dict | None:
    """Who gained and lost the most share of articles against the week before.
    None until the archive covers the whole week before: a part week would make everyone look like a mover."""
    days = (end - start).days + 1
    prev_start, prev_end = start - timedelta(days=days), start - timedelta(days=1)
    first = db.conn.execute("SELECT MIN(published) AS p FROM items").fetchone()["p"]
    if not first or first[:10] > prev_start.isoformat():
        return None
    now_s, prev_s = _shares(db, cfg, start, end), _shares(db, cfg, prev_start, prev_end)
    diff = [{"stock": cfg.by_symbol[s], "now": now_s.get(s, 0), "before": prev_s.get(s, 0),
             "change": now_s.get(s, 0) - prev_s.get(s, 0)} for s in set(now_s) | set(prev_s)]
    diff.sort(key=lambda d: -d["change"])
    return {"up": [d for d in diff[:n] if d["change"] >= 0.5],
            "down": [d for d in reversed(diff[-n:]) if d["change"] <= -0.5]}


def outlets(db: DB, groups: list[dict], skip, n: int = 6, big: int = BIG) -> dict:
    """Which of the outlets we track turned up on the most big stories, and how many outlets we saw."""
    bigs = [g for g in groups if g["outlets"] >= big and not skip(g)]
    on_big = Counter()
    for g in bigs:
        on_big.update({a["outlet_name"] for a in _articles(db, g["ids"]) if a["outlet_name"]})
    all_ids = set().union(*(g["ids"] for g in groups)) if groups else set()
    arts = _articles(db, all_ids)
    return {
        "big_count": len(bigs),
        "on_big": on_big.most_common(n),
        "outlet_count": len({a["outlet_name"] for a in arts if a["outlet_name"]}),
        "article_count": len(arts),
        "arts": arts,
    }


def busiest_day(arts: list[dict], tz: ZoneInfo) -> tuple[Date, int, Date, int] | None:
    """The weekday with the most articles on our companies, and the quietest weekday."""
    days = Counter(_when(a["published"]).astimezone(tz).date() for a in arts)
    weekdays = {d: c for d, c in days.items() if d.weekday() < 5}
    if len(weekdays) < 2:
        return None
    hi = max(weekdays, key=weekdays.get)
    lo = min(weekdays, key=weekdays.get)
    return hi, weekdays[hi], lo, weekdays[lo]
