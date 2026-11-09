"""The weekly ranking pages: the most-covered tech stocks each week, at a permanent address.

top-10/<end date>.html for each week (Monday to Sunday, UK time), with two tables: US and UK tech
stocks together, and UK tech stocks on their own. Plus top-10/index.html listing every week.
Same ranking as the Monday LinkedIn cards (engine/linkedin.py), so the two always agree.

Articles are only kept for a limited time, so a week can't be worked out again later. Each finished
week is saved once to data/rankings/<end date>.json and the pages are built from those files.

Coverage only: how much was written, never whether the shares are worth buying or selling.
"""
from __future__ import annotations

import json
from datetime import date as Date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import linkedin
from .config import Config
from .db import DB, parse_iso

FOLDER = "top-10"
SLACK = timedelta(hours=8)  # overnight is quiet, so articles starting a few hours into the week still give a fair count
LISTS = {"all": "US and UK tech stocks", "uk": "UK tech stocks"}


def _saved_dir(db: DB) -> Path:
    return Path(db.path).parent / "rankings"


def _utc(d: Date, tz: ZoneInfo) -> str:
    return datetime.combine(d, time(0), tz).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _covers(db: DB, start: Date, tz: ZoneInfo) -> bool:
    first = db.conn.execute("SELECT MIN(published) AS p FROM items").fetchone()["p"]
    return bool(first) and parse_iso(first) <= parse_iso(_utc(start, tz)) + SLACK


def _biggest(db: DB, symbol: str, start: Date, end: Date, tz: ZoneInfo, tagger) -> dict | None:
    """The company's most widely covered story that week whose headline names it, for context in the table.
    A story grouped with others can carry another company's headline, so those are skipped."""
    for r in db.conn.execute(
            """SELECT headline, link, outlet, outlets FROM stock_stories
               WHERE symbol = ? AND first_seen >= ? AND first_seen < ? ORDER BY outlets DESC, first_seen""",
            (symbol, _utc(start, tz), _utc(end + timedelta(days=1), tz))):
        if symbol in tagger.find(r["headline"]):
            return dict(r)
    return None


def save(db: DB, cfg: Config, now: datetime) -> str | None:
    """Save the last finished week if it isn't saved yet. Returns its end date, or None."""
    tz = ZoneInfo(cfg.site["timezone"])
    start, end = linkedin.week(now, tz)
    folder = _saved_dir(db)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{end.isoformat()}.json"
    if path.exists() or not _covers(db, start, tz):
        return None
    from .tickers import Tagger
    tagger = Tagger(cfg)
    facts = linkedin.gather(db, cfg, datetime.combine(end + timedelta(days=1), time(12), tz))
    lists = {}
    for which, rows in facts["lists"].items():
        if len(rows) < 3:
            continue
        lists[which] = [{"symbol": r["symbol"], "name": r["stock"].name, "slug": r["stock"].slug,
                         "exchange": r["stock"].exchange, "market": r["stock"].market,
                         "headlines": r["articles"], "outlets": r["outlets"],
                         "biggest": _biggest(db, r["symbol"], start, end, tz, tagger)} for r in rows]
    if "all" not in lists:
        return None
    path.write_text(json.dumps({"start": start.isoformat(), "end": end.isoformat(), "lists": lists},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    return end.isoformat()


def _ranked(rows: list[dict]) -> list[dict]:
    counts = [r["headlines"] for r in rows]
    for r in rows:
        r["rank"] = 1 + sum(1 for n in counts if n > r["headlines"])
        r["joint"] = counts.count(r["headlines"]) > 1
    return rows


def _lead(rows: list[dict], who: str, when: str) -> str:
    top = [r for r in rows if r["rank"] == 1]
    if len(top) == 1:
        a = top[0]
        out = (f"{a['name']} ({a['symbol']}) was the most covered {who} in {when}, named in {a['headlines']} "
               f"{'headline' if a['headlines'] == 1 else 'headlines'} from {a['outlets']} "
               f"{'outlet' if a['outlets'] == 1 else 'outlets'}.")
    else:
        names = ", ".join(r["name"] for r in top[:-1]) + f" and {top[-1]['name']}"
        out = f"{names} were the joint most covered {who}s in {when}, named in {top[0]['headlines']} headlines each."
    rest = [r for r in rows if r["rank"] > 1][:2]
    if rest:
        out += " " + " and ".join(f"{r['name']} ({r['headlines']})" for r in rest) + " came next."
    return out


def summary(w: dict) -> str:
    when = f"the week to {w['end']:%A %-d %B %Y}"
    out = _lead(w["lists"]["all"], "tech stock", when)
    uk = w["lists"].get("uk")
    if uk:
        lead = uk[0]
        if any(r["market"] == "UK" for r in w["lists"]["all"]):
            out += f" The most covered UK tech stock was {lead['name']}, with {lead['headlines']} headlines."
        else:
            out += f" No UK tech stock made the top 10. The most covered was {lead['name']}, with {lead['headlines']} headlines."
    return out


def _load(db: DB) -> list[dict]:
    weeks = []
    for f in sorted(_saved_dir(db).glob("*.json")):
        w = json.loads(f.read_text(encoding="utf-8"))
        w["start"], w["end"] = Date.fromisoformat(w["start"]), Date.fromisoformat(w["end"])
        w["lists"] = {k: _ranked(v) for k, v in w["lists"].items()}
        weeks.append(w)
    return weeks


def build(env, ctx: dict, db: DB, out: Path) -> dict:
    """Write the week pages and the archive. Returns the URLs for the sitemap and the latest week."""
    weeks = _load(db)
    if not weeks:
        return {"urls": [], "latest": None}
    folder = out / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    ctx = dict(ctx, root="../")
    base = ctx["site"]["url"].rstrip("/")
    for i, w in enumerate(weeks):
        w["summary"] = summary(w)
        w["slug"] = f"{w['end'].isoformat()}.html"
        w["prev"] = weeks[i - 1] if i else None
    urls = []
    for i, w in enumerate(weeks):
        w["next"] = weeks[i + 1] if i + 1 < len(weeks) else None
        when = f"{w['end']:%-d %B %Y}"
        published = (w["end"] + timedelta(days=1)).isoformat()
        org = {"@type": "Organization", "name": ctx["site"]["name"], "url": base + "/"}
        w["jsonld"] = {
            "@context": "https://schema.org", "@type": "Article",
            "headline": f"The most covered tech stocks, week to {when}",
            "description": w["summary"], "url": f"{base}/{FOLDER}/{w['slug']}", "inLanguage": "en-GB",
            "datePublished": published, "dateModified": published,
            "author": org, "publisher": dict(org, logo=base + "/logo-512.png"),
            "mainEntity": [{"@type": "ItemList", "name": f"Most covered {LISTS[k]}",
                            "itemListOrder": "https://schema.org/ItemListOrderDescending", "numberOfItems": len(rows),
                            "itemListElement": [{"@type": "ListItem", "position": r["rank"],
                                                 "name": f"{r['name']} ({r['exchange']}: {r['symbol']})",
                                                 "url": f"{base}/stocks/{r['slug']}.html"} for r in rows]}
                           for k, rows in w["lists"].items()]}
        (folder / w["slug"]).write_text(
            env.get_template("top10.html").render(**ctx, w=w, lists=LISTS, page="top-10", sidebar=False),
            encoding="utf-8")
        urls.append(f"{FOLDER}/{w['slug']}")
    (folder / "index.html").write_text(
        env.get_template("top10_index.html").render(**ctx, weeks=list(reversed(weeks)), page="top-10", sidebar=False),
        encoding="utf-8")
    return {"urls": [f"{FOLDER}/", *urls], "latest": weeks[-1]}
