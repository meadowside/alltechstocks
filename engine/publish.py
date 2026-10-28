"""Builds the static site and the morning email."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import llm
from .config import ROOT, Config
from .db import DB, parse_iso
from .stories import Story, rank_stocks, why_template

TEMPLATES = ROOT / "templates"
PAGES_UPDATED = "23 October 2026"  # change when you edit the Privacy or Terms pages


def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"], default_for_string=False, default=False))
    env.filters["ago"] = _ago
    env.filters["md"] = _md
    env.filters["day"] = lambda d: d.strftime("%-d %b")
    env.filters["iso"] = parse_iso
    return env


_NOW: datetime | None = None


def _md(text: str) -> str:
    """Keep headline text from breaking the Markdown: square brackets and asterisks."""
    return (text or "").replace("[", "(").replace("]", ")").replace("*", "")


def _ago(dt: datetime | str) -> str:
    if isinstance(dt, str):
        dt = parse_iso(dt)
    mins = int(((_NOW or dt) - dt).total_seconds() // 60)
    if mins < 60:
        return f"{max(mins, 1)}m ago"
    if mins < 60 * 24:
        return f"{mins // 60}h ago"
    return f"{mins // (60 * 24)}d ago"


def _with_why(stories: list[Story], cfg: Config) -> None:
    seen: Counter = Counter()
    for overall, s in enumerate(stories, start=1):
        seen[s.section] += 1
        bigger = None
        if overall == 1:
            more = [o for o in stories[1:] if o.source_count > s.source_count]
            bigger = max(more, key=lambda o: o.source_count) if more else None
        s.why = why_template(s, seen[s.section], overall, cfg, bigger)


# ---------------------------------------------------------------- site

def trending(stories: list[Story], cfg: Config, now: datetime) -> list[dict]:
    """Stories picking up new outlets fastest right now."""
    t = cfg.rules.get("trending", {})
    since = now - timedelta(hours=t.get("window_hours", 6))
    rows = []
    for s in stories:
        new = sum(1 for o in s.outlets.values() if min(parse_iso(i["published"]) for i in o.items) >= since)
        if new >= t.get("min_new_outlets", 2):
            rows.append({"story": s, "new": new})
    rows.sort(key=lambda r: (r["new"], r["story"].coverage), reverse=True)
    return rows[: t.get("max", 6)]


def just_in(stories: list[Story], cfg: Config) -> list[Story]:
    """The newest stories, by when we first saw them, whatever their coverage."""
    n = cfg.rules.get("just_in", {}).get("max", 6)
    return sorted(stories, key=lambda s: s.first_seen, reverse=True)[:n]


def add_so_what(wanted: list[Story], everything: list[Story], db: DB, cfg: Config, now: datetime) -> int:
    """Attach saved AI lines to every story, and write new ones for the stories that need them.
    Each story is written once: the line is saved against all its items and reused from then on."""
    skipped = set()
    ai = cfg.rules.get("ai_lines", {})
    no_line = set(cfg.rules.get("email", {}).get("no_so_what", []))
    for s in everything:
        if s.section in no_line:
            s.so_what = ""
            skipped.add(id(s))
            continue
        saved = db.why_line(s.core_ids or s.item_ids, ai.get("valid_from")) or ""
        if saved and saved != "-" and llm.NON_ANSWER.search(saved):
            saved = "-"  # an older "nothing here" line written as a sentence
        elif saved and saved != "-" and not llm.drop_padding(saved):
            saved = ""  # mostly "the coverage doesn't say" filler: write it again
        if saved == "-":  # the model had nothing useful to say; don't ask again
            skipped.add(id(s))
            saved = ""
        s.so_what = saved
    ai = cfg.rules.get("ai_lines", {})
    local = now.astimezone(ZoneInfo(cfg.site["timezone"]))
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    budget = min(ai.get("max_new_per_run", 25), ai.get("max_new_per_day", 150) - db.calls_today(day_start))
    made, seen = 0, set()
    for s in wanted:
        if made >= budget:
            break
        if s.so_what or id(s) in seen or id(s) in skipped:
            continue
        seen.add(id(s))
        core = [i for i in s.items if i["id"] in set(s.core_ids)] or s.items
        snippets = [i["summary"] or i["title"] for i in core if "news.google.com" not in i["url"]] or [s.headline]
        companies = ", ".join(f"{cfg.by_symbol[t].name} ({t})" for t in s.tickers[:3])
        line = llm.why_it_matters(s.headline, snippets, companies)
        if line is None and not llm.enabled():
            break  # no key: don't loop through every story
        if line == "":
            db.save_why_line(s.core_ids or s.item_ids, "-", now)
        elif line:
            s.so_what = line
            db.save_why_line(s.core_ids or s.item_ids, line, now)
            made += 1
    return made


def _record_history(live: list[Story], db: DB) -> None:
    """Save each stock's stories so its page can show the last month, not just today."""
    for s in live:
        for sym in s.tickers:
            db.save_stock_story(sym, s.item_ids, {
                "headline": s.headline, "link": s.link, "outlet": s.link_outlet.name, "paywall": s.paywall,
                "outlets": s.source_count, "in_headline": sym in s.headline_tickers, "section": s.section,
                "first_seen": s.first_seen.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "last_seen": s.last_seen.strftime("%Y-%m-%dT%H:%M:%SZ"),
            })


def _stock_summary(cfg: Config, db: DB, now: datetime) -> dict[str, dict]:
    """Per stock: stories and outlets over the last day and week, from the saved history."""
    out = {s.symbol: {"day": 0, "week": 0, "outlets_week": 0} for s in cfg.stocks}
    for r in db.stock_stories(now - timedelta(days=7)):
        row = out.get(r["symbol"])
        if row is None:
            continue
        row["week"] += 1
        row["outlets_week"] = max(row["outlets_week"], r["outlets"])
        if parse_iso(r["last_seen"]) >= now - timedelta(hours=24):
            row["day"] += 1
    return out


def build_site(stories: list[Story], db: DB, cfg: Config, now: datetime, out: Path,
               email_archive: Path | None = None) -> dict:
    global _NOW
    _NOW = now
    live = [s for s in stories if s.eligible and now - s.last_seen <= timedelta(hours=48)]
    _with_why(live, cfg)
    from .rules import Rules
    rules = Rules(cfg)
    db.purge_stock_stories(lambda r: not rules.exclusion_reason(r["headline"], r["link"], "", r["outlet"]))
    _record_history(live, db)
    top = next((s for s in live if not s.is_global), None)

    by_section = {sid: [] for sid in cfg.section_ids}
    for s in live:
        if s is top:
            continue
        by_section[s.section].append(s)
    sections = [
        {"id": sec["id"], "name": sec["name"], "stories": by_section[sec["id"]][: sec["site_max"]]}
        for sec in cfg.sections
    ]

    st = cfg.rules.get("stocks_table", {})
    ranked = rank_stocks(live, cfg, now, st.get("hours", 24))
    trend = trending([s for s in live if s is not top], cfg, now)
    ai = cfg.rules.get("ai_lines", {})
    wanted = ([top] if top else []) + [s for sec in sections for s in sec["stories"][: ai.get("per_section", 6)]]
    if ai.get("trending", True):
        wanted += [t["story"] for t in trend]
    new_lines = add_so_what(wanted, live, db, cfg, now)

    local = now.astimezone(ZoneInfo(cfg.site["timezone"]))
    filings_recent = db.filings_since(now - timedelta(days=2), notable_only=True)
    ctx = {
        "site": cfg.site,
        "disclaimer": cfg.rules.get("disclaimer", ""),
        "nav": [(s["id"], s["name"]) for s in cfg.sections],
        "updated": local.strftime("%-d %b %Y, %H:%M"),
        "year": local.year,
        "trending": trend,
        "just_in": just_in(live, cfg),
        "promos": cfg.rules.get("promos", []),
        "filings_box": filings_recent[:8],
        "stocks": cfg.by_symbol,
        "root": "",
        "prices": cfg.rules.get("prices", {}),
    }
    env = _env()
    out.mkdir(parents=True, exist_ok=True)
    (out / "stocks").mkdir(exist_ok=True)

    def write(path: str, template: str, **kw):
        (out / path).write_text(env.get_template(template).render(**{**ctx, **kw}), encoding="utf-8")

    write("index.html", "index.html", top=top, sections=sections, page="home", sidebar=True,
          ranked=ranked[: st.get("max", 15)], ranked_hours=st.get("hours", 24))
    sec_max = {sec["id"]: sec["site_max"] for sec in cfg.sections}
    for sec in sections:
        latest = sorted(by_section[sec["id"]], key=lambda s: s.first_seen, reverse=True)[: sec_max[sec["id"]]]
        write(f"{sec['id']}.html", "section.html", section=sec, stories=sec["stories"], page=sec["id"],
              sidebar=True, latest=latest)

    # US and UK pages: every live story tagged with a stock from that market.
    for market in ("US", "UK"):
        ms = [s for s in live if any(cfg.by_symbol[t].market == market for t in s.tickers)]
        mranked = [r for r in ranked if r["stock"].market == market]
        write(f"{market.lower()}.html", "market.html", market=market, stories=ms[:40], page=market.lower(),
              sidebar=True, ranked=mranked[:10], ranked_hours=st.get("hours", 24),
              latest=sorted(ms, key=lambda s: s.first_seen, reverse=True)[:40])

    # The Stocks directory and one page per stock.
    summary = _stock_summary(cfg, db, now)
    groups = sorted({s.group for s in cfg.stocks if s.group})
    write("stocks/index.html", "stocks_index.html", page="stocks", sidebar=False, root="../",
          summary=summary, groups=groups, rows=sorted(cfg.stocks, key=lambda s: (s.market != "US", s.name.lower())))
    live_by_symbol: dict[str, list[Story]] = {}
    for s in live:
        for sym in s.tickers:
            live_by_symbol.setdefault(sym, []).append(s)
    month = now - timedelta(days=30)
    for stock in cfg.stocks:
        history = db.stock_stories(month, stock.symbol)
        live_keys = set()
        for s in live_by_symbol.get(stock.symbol, []):
            live_keys |= set(s.item_ids)
        older = [h for h in history if not (set(json.loads(h["item_ids"])) & live_keys)]
        peers = [p for p in cfg.stocks if p.group == stock.group and p.symbol != stock.symbol][:8]
        write(f"stocks/{stock.slug}.html", "stock.html", page="stocks", sidebar=False, root="../",
              stock=stock, live=sorted(live_by_symbol.get(stock.symbol, []), key=lambda s: -s.score),
              older=older[:40], filings=db.filings_since(now - timedelta(days=60), stock.symbol),
              summary=summary[stock.symbol], peers=peers,
              results_date=(datetime.strptime(stock.results, "%Y-%m-%d") if stock.results else None),
              today=local.date())

    pages = {"about": "About", "privacy": "Privacy", "terms": "Terms", "contact": "Contact"}
    for slug, title in pages.items():
        write(f"{slug}.html", f"pages/{slug}.html", page=slug, page_title=title, sidebar=False,
              updated_date=PAGES_UPDATED)

    health = db.health()
    rows = []
    for src in cfg.sources + [_edgar_row()]:
        h = health.get(src.id)
        rows.append({
            "name": src.name, "id": src.id, "tier": src.tier, "auto": getattr(src, "auto", False),
            "ok": bool(h and h["last_error"] is None and h["last_attempt"]),
            "count": h["last_count"] if h else None,
            "last_ok": parse_iso(h["last_ok"]) if h and h["last_ok"] else None,
            "error": h["last_error"] if h else "not tried yet",
        })
    quiet = [s for s in cfg.stocks if summary[s.symbol]["week"] == 0]
    write("status.html", "status.html", rows=rows, page="status", stories=stories[:40], total=len(stories),
          quiet=quiet, sidebar=False)

    host = cfg.site["url"].split("://", 1)[-1].strip("/")
    urls = ["", "us.html", "uk.html", "stocks/"] + [f"{s}.html" for s in cfg.section_ids] + \
           [f"stocks/{s.slug}.html" for s in cfg.stocks] + ["about.html"]
    (out / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"  <url><loc>{cfg.site['url'].rstrip('/')}/{u}</loc></url>\n" for u in urls) + "</urlset>\n",
        encoding="utf-8")
    (out / "robots.txt").write_text(
        f"User-agent: *\nDisallow: /status.html\nSitemap: {cfg.site['url'].rstrip('/')}/sitemap.xml\n", encoding="utf-8")
    (out / "CNAME").write_text(host + "\n", encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    static = ROOT / "static"
    if static.is_dir():
        for f in static.iterdir():
            if f.is_file():
                (out / f.name).write_bytes(f.read_bytes())
    return {"live": len(live), "top": top.headline if top else None, "new_ai_lines": new_lines,
            "top_stock": ranked[0]["stock"].symbol if ranked else None}


def _edgar_row():
    from .config import Source
    return Source(id="sec-edgar", name="SEC EDGAR filings", url="", tier=4, weight=0)


# ---------------------------------------------------------------- email

def select_email(stories: list[Story], db: DB, cfg: Config, now: datetime) -> dict:
    sc = cfg.scoring
    local = now.astimezone(ZoneInfo(cfg.site["timezone"]))
    lookback = timedelta(hours=72 if local.weekday() == 0 else 26)  # Monday picks up the weekend
    sent = db.sent_records(except_date=local.strftime("%Y-%m-%d"))

    candidates = []
    for s in stories:
        if not s.eligible or now - s.last_seen > lookback:
            continue
        s.is_update = False
        overlap = [cov for ids, cov in sent if ids & set(s.item_ids)]
        if overlap:
            if s.coverage < max(overlap) * (1 + sc["repeat_growth_needed"]):
                continue
            s.is_update = True
        candidates.append(s)

    _with_why(candidates, cfg)
    top = next((s for s in candidates if not s.is_global), None)
    outlet_count: Counter = Counter()
    cap = sc["max_links_per_outlet_email"]

    def take_link(s: Story) -> bool:
        """Link the best outlet that hasn't hit its cap. A story is only dropped if every outlet has."""
        for outlet, url in s.link_options:
            if outlet_count[outlet.key] < cap:
                s.use_link(outlet, url)
                outlet_count[outlet.key] += 1
                return True
        return False

    if top:
        take_link(top)

    global_count = 0
    sections = []
    order = cfg.rules.get("email", {}).get("order") or [sec["id"] for sec in cfg.sections]
    by_id = {sec["id"]: sec for sec in cfg.sections}
    email_sections = [by_id[i] for i in order if i in by_id] + [sec for sec in cfg.sections if sec["id"] not in order]
    for sec in email_sections:
        picked = []
        for s in candidates:
            if s is top or s.section != sec["id"]:
                continue
            if len(picked) >= sec["email_max"]:
                break
            if s.is_global and global_count >= sc["global_max_per_email"]:
                continue
            if not take_link(s):
                continue
            picked.append(s)
            global_count += s.is_global
        sections.append({"id": sec["id"], "name": sec["name"], "stories": picked,
                         "short": len(picked) < sec["email_min"]})
    return {"top": top, "sections": sections, "candidates": candidates, "lookback": lookback}


def build_email(stories: list[Story], db: DB, cfg: Config, now: datetime, out: Path,
                email_archive: Path) -> dict:
    global _NOW
    _NOW = now
    picked = select_email(stories, db, cfg, now)
    top = picked["top"]

    picks = ([top] if top else []) + [sec["stories"][0] for sec in picked["sections"] if sec["stories"]]
    llm_used = add_so_what(picks, picks, db, cfg, now)

    lead = top or (picks[0] if picks else None)
    local = now.astimezone(ZoneInfo(cfg.site["timezone"]))
    # Lead stories for the titles and the opening note: the top story first, then the
    # section picks by how many outlets covered them.
    leads = [(p.headline, p.source_count) for p in
             ([lead] if lead else []) + sorted((p for p in picks if p is not lead), key=lambda p: -p.source_count)]
    titles = llm.email_titles(leads)
    if len(titles) < 3:
        titles += [t for t in llm.fallback_titles(leads) if t not in titles][: 3 - len(titles)]
    opening = llm.opening_note(leads, local.strftime("%A %-d %B")) if leads else None
    top_why = None
    if top:
        core = [i for i in top.items if i["id"] in set(top.core_ids)] or top.items
        snippets = [i["summary"] or i["title"] for i in core if "news.google.com" not in i["url"]] or [top.headline]
        companies = ", ".join(f"{cfg.by_symbol[t].name} ({t})" for t in top.tickers[:3])
        top_why = llm.top_why(top.headline, snippets, companies)

    date = local.strftime("%Y-%m-%d")
    ranked = rank_stocks([s for s in stories if s.eligible], cfg, now, int(picked["lookback"].total_seconds() // 3600))
    filings = db.filings_since(now - picked["lookback"], notable_only=True)[: cfg.rules["email"].get("filings_max", 8)]
    env = _env()
    args = dict(site=cfg.site, top=top, sections=picked["sections"], titles=titles, opening=opening, top_why=top_why, stocks=cfg.by_symbol,
                date_long=local.strftime("%A %-d %B %Y"), date=date, ranked=ranked[:5], filings=filings,
                disclaimer=cfg.rules.get("email_disclaimer", cfg.rules.get("disclaimer", "")))
    html = env.get_template("email.html").render(**args)
    md = env.get_template("email.md").render(**args).strip() + "\n"
    # Saved with the data in the repo, not on the public site. Andy's Desktop button
    # downloads the .md each morning.
    email_archive.mkdir(parents=True, exist_ok=True)
    for name, text in ((f"{date}.html", html), (f"{date}.md", md), ("latest.md", md), ("latest.html", html)):
        (email_archive / name).write_text(text, encoding="utf-8")

    chosen = ([top] if top else []) + [s for sec in picked["sections"] for s in sec["stories"]]
    if lead:
        # One cover per title idea, so whichever title Andy picks has a cover that matches it.
        # Chips show the stocks each title names, the number is the day's story count.
        from . import cover
        from .tickers import Tagger
        tagger = Tagger(cfg)
        for i, words in enumerate(titles[:3] or [lead.headline], start=1):
            try:
                found = tagger.find(words)
                tks = sorted(found, key=lambda t: min(
                    [words.lower().find(a.lower()) for a in [t] + cfg.by_symbol[t].aliases + cfg.by_symbol[t].exact
                     if a and words.lower().find(a.lower()) >= 0] or [999])) or lead.tickers
                name = f"{date}-cover.png" if i == 1 else f"{date}-cover-{i}.png"
                png = cover.render(words if len(words) <= 90 else lead.headline, len(chosen), tks[:3], local,
                                   email_archive / name)
                if i == 1:
                    (email_archive / "latest-cover.png").write_bytes(png.read_bytes())
            except Exception as e:  # a missing cover must never stop the email
                print(f"  cover {i} failed: {e}")

    db.clear_sent(date)  # a rebuild replaces the day's record rather than adding to it
    for s in chosen:
        db.record_sent(date, s.item_ids, s.coverage)
    db.record_email(date, now)
    return {"date": date, "stories": len(chosen), "llm_lines": llm_used, "opening": bool(opening), "top_why": bool(top_why),
            "short_sections": [sec["name"] for sec in picked["sections"] if sec["short"]]}
