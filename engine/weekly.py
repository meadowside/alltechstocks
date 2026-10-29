"""The weekly draft: a Friday round-up of the week's coverage, results and filings, for Andy to edit,
add his own take to, and send on Substack.

Everything factual comes from the database: the stories saved against each stock (stock_stories),
SEC filings, and the daily coverage counts (stock_daily, section_daily). One AI call drafts the
prose (an intro, a short paragraph per company that reported, three angles Andy could write about,
title ideas). Every piece is checked with the same advice and prediction filters as the daily lines.
A piece that fails is left as a gap for Andy to write, so the worst case is a draft with holes,
never one that tips a stock.

Written to data/weekly/<date>.md (and latest.md), next to the daily emails. Not on the public site.
"""
from __future__ import annotations

import json
import re
from datetime import date as Date
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader

from . import llm, trends
from .config import ROOT, Config
from .db import DB, iso
from .rules import Rules
from .tickers import Tagger

DEFAULT_MODEL = "claude-sonnet-5-5"

# Headlines that are someone else's tip or a price call. The site's rules catch most of these,
# but the weekly draft is the piece Andy may charge for, so it gets a second, stricter look.
TIP_RE = re.compile(
    r"^(buy|sell|hold)\b|\b(buy|sell|hold) (rating|call|signal)\b|\bsays (buy|sell)\b|\brated (buy|sell)\b|"
    r"price target|\b(stock )?target (cut|raised|lowered|hiked)\b|\bcuts .{0,40} target\b|\bPT (raised|lowered|cut|hiked)\b|\bupgrades?\b|\bdowngrades?\b|\bupgraded\b|\bdowngraded\b|"
    r"\bupside\b|\bdownside\b|undervalued|overvalued|\bstock to (buy|own|watch)\b|\bhit \$\d", re.I)

# Curtain-raisers. A results paragraph must be about what was reported, not what was expected.
PREVIEW_RE = re.compile(
    r"what to expect|ahead of (its |their )?(earnings|results|q[1-4])|earnings (preview|loom|tomorrow|today:)|"
    r"\bpreview\b|investors eye|reports? (earnings|results) (tomorrow|today|next week|on (mon|tues|wednes|thurs|fri)day)|"
    r"\bbefore (earnings|results)\b|\bearnings watch\b|\bset to report\b|\bwill report\b|reports earnings \w+ \d",
    re.I)

# Words that show a headline is about results that came out, not some other story tagged "results".
# UK companies report as half-year, interim and full-year results or trading updates. Stricter than
# RESULTS_RE because there's no SEC filing to confirm a UK company actually reported.
UK_RESULTS_RE = re.compile(
    r"\b(half-year|half year|interim results|first-half|first half|h1|full-year|full year|annual results|"
    r"preliminary results|trading update|trading statement|pre-tax profit|pretax profit)\b", re.I)

RESULTS_RE = re.compile(
    r"\b(results|earnings|quarter|quarterly|q[1-4]|revenue|revenues|sales|profit|profits|loss|guidance|outlook|"
    r"forecast|beats?|miss(es)?|eps|half-year|full-year|trading update)\b", re.I)


# ---------------------------------------------------------------- facts

def _week(cfg: Config, now: datetime, days: int) -> tuple[Date, Date, datetime]:
    tz = ZoneInfo(cfg.site["timezone"])
    end_day = now.astimezone(tz).date()
    return end_day - timedelta(days=days - 1), end_day, now - timedelta(days=days)


def _clean(headline: str) -> bool:
    return not TIP_RE.search(headline or "")


def _story_groups(db: DB, rules: Rules, since: datetime) -> list[dict]:
    """The week's stories, one entry per story however many stocks it was saved against.
    Two saved rows are the same story if they share a link or any article."""
    rows = db.conn.execute(
        "SELECT * FROM stock_stories WHERE first_seen >= ? ORDER BY outlets DESC, last_seen DESC",
        (iso(since),)).fetchall()
    groups: list[dict] = []
    for r in rows:
        if rules.exclusion_reason(r["headline"], r["link"], "", r["outlet"]) or not _clean(r["headline"]):
            continue
        ids = set(json.loads(r["item_ids"]))
        g = next((g for g in groups if g["link"] == r["link"] or g["ids"] & ids), None)
        if g is None:
            g = {"headline": r["headline"], "link": r["link"], "outlet": r["outlet"], "paywall": bool(r["paywall"]),
                 "outlets": r["outlets"], "section": r["section"], "ids": set(), "symbols": [], "in_headline": set(),
                 "first_seen": r["first_seen"]}
            groups.append(g)
        g["ids"] |= ids
        if r["symbol"] not in g["symbols"]:
            g["symbols"].append(r["symbol"])
        if r["in_headline"]:
            g["in_headline"].add(r["symbol"])
    return groups


def _results(db: DB, cfg: Config, tagger: Tagger, since: datetime, max_companies: int) -> list[dict]:
    """Companies that reported this week: a US results filing (8-K item 2.02), or for UK companies,
    which don't file with the SEC, a week of results write-ups naming them in the headline.

    The facts are article headlines straight from the items table, not the saved stories. Results
    write-ups sometimes get folded into an older, bigger story about the same company, and the
    headlines are what the draft is built on, so each one has to name the company and be about results."""
    filings = db.conn.execute("SELECT * FROM filings WHERE filed >= ? ORDER BY filed", (iso(since),)).fetchall()
    filed: dict[str, list] = {}
    for f in filings:
        if f["form"] in ("10-Q", "10-K", "20-F") or "2.02" in (f["items"] or "").split(","):
            filed.setdefault(f["symbol"], []).append(f)
    # When each US company's results came out: write-ups before then are previews.
    out_at = {s: min(f["filed"] for f in fs if "2.02" in (f["items"] or "")) for s, fs in filed.items()
              if any("2.02" in (f["items"] or "") for f in fs)}

    weight = {src.id: src.weight for src in cfg.sources}
    per_symbol: dict[str, list] = {}
    for it in db.items_since(since):
        title = (it["title"] or "").strip()
        if not title or not RESULTS_RE.search(title) or PREVIEW_RE.search(title) or not _clean(title):
            continue
        for sym in tagger.find(title):
            per_symbol.setdefault(sym, []).append(it)

    reporters = dict(out_at)
    for st in cfg.stocks:
        if st.market == "UK" and st.symbol not in reporters:
            hits = [i for i in per_symbol.get(st.symbol, []) if UK_RESULTS_RE.search(i["title"])]
            if len({i["outlet_id"] or i["outlet_name"] for i in hits}) >= 3:
                reporters[st.symbol] = min(i["published"] for i in hits)

    out = []
    for sym, when in reporters.items():
        stock = cfg.by_symbol.get(sym)
        if not stock:
            continue
        # Allow a few hours before the filing: some outlets publish the moment the release is out.
        cutoff = iso(datetime.strptime(when, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=since.tzinfo) - timedelta(hours=3))
        items = [i for i in per_symbol.get(sym, []) if i["published"] >= cutoff]
        if stock.market == "UK":
            items = [i for i in items if UK_RESULTS_RE.search(i["title"]) or RESULTS_RE.search(i["title"])]
        facts, seen = [], set()
        for i in sorted(items, key=lambda i: -weight.get(i["outlet_id"] or i["source_id"], 1)):
            key = re.sub(r"\W+", " ", i["title"].lower()).strip()
            if key not in seen:
                seen.add(key)
                facts.append(i["title"].strip())
        reads, outlets = [], set()
        for i in sorted(items, key=lambda i: -weight.get(i["outlet_id"] or i["source_id"], 1)):
            o = i["outlet_id"] or i["outlet_name"]
            if o in outlets:
                continue
            outlets.add(o)
            if len(reads) < 3:
                reads.append({"outlet": i["outlet_name"], "link": i["url"]})
        seen_forms, links = set(), []
        for f in filed.get(sym, []):
            key = (f["label"].replace(" (amended)", ""), f["form"].replace("/A", ""))
            if key not in seen_forms:
                seen_forms.add(key)
                links.append({"label": f["label"], "form": f["form"], "url": f["url"]})
        out.append({"symbol": sym, "name": stock.name, "market": stock.market, "slug": stock.slug,
                    "reads": reads, "facts": facts[:10], "filings": links, "coverage": len(outlets)})
    out.sort(key=lambda r: -r["coverage"])
    return out[:max_companies]


def _most_covered(db: DB, cfg: Config, start: Date, end: Date, n: int) -> list[dict]:
    def window(a: Date, b: Date) -> dict[str, dict]:
        return {r["symbol"]: dict(r) for r in db.conn.execute(
            """SELECT symbol, SUM(stories) AS stories, SUM(outlets) AS outlets, SUM(attention) AS attention
               FROM stock_daily WHERE date >= ? AND date <= ? GROUP BY symbol""",
            (a.isoformat(), b.isoformat())).fetchall()}
    days = (end - start).days + 1
    first = db.conn.execute("SELECT MIN(date) AS d FROM stock_daily").fetchone()["d"]
    prev_start = start - timedelta(days=days)
    have_prev = bool(first) and Date.fromisoformat(first) <= prev_start
    now_w = window(start, end)
    prev_w = window(prev_start, start - timedelta(days=1)) if have_prev else {}
    rows = sorted(now_w.values(), key=lambda r: (-r["stories"], -r["attention"]))[:n]
    out = []
    for r in rows:
        stock = cfg.by_symbol.get(r["symbol"])
        if not stock:
            continue
        prev = prev_w.get(r["symbol"])
        out.append({"stock": stock, "stories": r["stories"], "outlets": r["outlets"],
                    "prev_stories": (prev["stories"] if prev else 0) if have_prev else None})
    return out


def gather(db: DB, cfg: Config, rules: Rules, now: datetime) -> dict:
    wcfg = cfg.rules.get("weekly", {})
    days = int(wcfg.get("days", 7))
    start, end, since = _week(cfg, now, days)
    tagger = Tagger(cfg)
    groups = _story_groups(db, rules, since)

    biggest = [g for g in groups if g["section"] != "results" and not PREVIEW_RE.search(g["headline"])]
    biggest = sorted(biggest, key=lambda g: -g["outlets"])[: int(wcfg.get("top_stories", 5))]
    ai = cfg.rules.get("ai_lines", {})
    for g in biggest:
        line = db.why_line(sorted(g["ids"]), ai.get("valid_from")) or ""
        g["so_what"] = "" if line in ("", "-") else llm.drop_padding(line)

    results = _results(db, cfg, tagger, since, int(wcfg.get("max_results", 8)))

    section_names = {s["id"]: s["name"] for s in cfg.sections}
    # The week before only counts once the history covers all of it. A part week would compare
    # seven days against two or three and make every share look like it moved.
    first = db.conn.execute("SELECT MIN(date) AS d FROM section_daily").fetchone()["d"]
    full_prev = bool(first) and Date.fromisoformat(first) <= start - timedelta(days=days)
    shares = [dict(x, name=section_names.get(x["section"], x["section"]),
                   previous_share=x["previous_share"] if full_prev else None)
              for x in trends.section_share(db, days=days, until=end.isoformat())]

    notable = db.conn.execute(
        "SELECT * FROM filings WHERE filed >= ? AND notable = 1 ORDER BY filed DESC", (iso(since),)).fetchall()
    other_filings = [dict(f) for f in notable
                     if not f["label"].startswith("Results") and f["label"] not in ("Quarterly report", "Annual report")]
    other_filings = other_filings[: int(wcfg.get("filings_max", 10))]

    coming = []
    for st in cfg.stocks:
        if st.results:
            try:
                d = Date.fromisoformat(st.results)
            except ValueError:
                continue
            if end < d <= end + timedelta(days=7):
                coming.append({"stock": st, "date": d})
    coming.sort(key=lambda x: x["date"])

    return {
        "start": start, "end": end, "days": days,
        "history_days": trends.history_days(db),
        "biggest": biggest, "results": results,
        "most_covered": _most_covered(db, cfg, start, end, int(wcfg.get("most_covered", 8))),
        "shares": shares, "filings": other_filings, "coming": coming,
    }


# ---------------------------------------------------------------- AI draft

PROMPT = """You draft the weekly round-up for All Tech Stocks, a research digest of the most covered US and
UK listed tech companies. Andy edits it, adds his own view and signs it. It is never an advice service.

The week: {week}.

Biggest stories of the week (outlets that covered each, most first):
{biggest}

Companies that reported results this week, with the headlines about their results:
{results}

Most covered stocks this week (stories naming them):
{covered}

Reply with JSON only, in exactly this shape:
{{"titles": ["...", "...", "..."], "intro": "...", "results": {{"TICKER": "...", ...}}, "angles": ["...", "...", "..."]}}

titles: three subject lines, 70 characters or fewer, each naming two companies from the week.
intro: four to six short sentences, 110 words or fewer, on what defined the week for these companies'
  businesses. Pick a thread that ties two or more stories together if there is one. Name the companies.
results: one entry per ticker listed under results, 55 words or fewer, two or three sentences on what the
  company reported and what it said about the coming quarters (guidance, spending plans, demand),
  using only the headlines given for it. If a headline says results beat or missed estimates, you may say so.
  If the headlines give nothing concrete, use the value "NONE".
angles: three questions Andy could answer in his own take, each one sentence, about the businesses
  (strategy, customers, costs, rivals, regulation), never about the shares.

Strict rules for every piece of text:
- Never say or hint whether to buy, sell or hold anything.
- Never mention share prices, share moves, valuations, returns, investors' reactions or what the shares might do,
  even if a headline does. Report the business news inside the headline and leave the share move out.
- Never predict. Describe what happened and what the companies said. Company guidance is fine when reported
  as the company's own ("Microsoft said it expects...").
- Use only the facts given. No figures that aren't in the headlines.
- Plain British English. Vary sentence length. Contractions are fine. A sharp, plain-speaking editor's voice.
- No dashes of any kind, no semicolons, no comma before "and" in a list, no hype words.
- Never mention outlets, coverage counts, headlines, sources or this email. Don't greet or sign off."""


def _facts_text(facts: dict, cfg: Config) -> dict[str, str]:
    biggest = "\n".join(
        f"- {g['headline']} ({g['outlets']} outlets) [{', '.join(g['symbols'][:3])}]" for g in facts["biggest"]) or "- none"
    results = "\n".join(
        f"{r['symbol']} ({r['name']}):\n" + "\n".join(f"  - {t}" for t in r["facts"][:8])
        for r in facts["results"] if r["facts"]) or "none"
    covered = "\n".join(f"- {m['stock'].name} ({m['stock'].symbol}): {m['stories']} stories"
                        for m in facts["most_covered"]) or "- none"
    week = f"{facts['start']:%-d %B} to {facts['end']:%A %-d %B %Y}"
    return {"biggest": biggest, "results": results, "covered": covered, "week": week}


def _check(text: str, max_words: int) -> str | None:
    """Why a drafted piece can't be used, or None if it's fine."""
    if not text or not text.strip():
        return "empty"
    words = len(text.split())
    if words > max_words:
        return f"{words} words"
    low = text.lower()
    hit = next((b for b in llm.BANNED if b in low), None)
    if hit:
        return f"banned: {hit}"
    m = llm.ADVICE_RE.search(text)
    if m:
        return f"reads like advice or a prediction: {m.group(0)}"
    m = llm.META.search(text)
    if m:
        return f"talks about itself or its sources: {m.group(0)}"
    if re.search(r"\b(stock|shares)\b", text, re.I):
        return "mentions the shares"
    return None


def parse_draft(raw: str | None, facts: dict) -> tuple[dict, list[str]]:
    """Turn the model's JSON into checked pieces. Anything that fails a rule is dropped and logged."""
    out = {"titles": [], "intro": None, "results": {}, "angles": []}
    log: list[str] = []
    if not raw:
        return out, log
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError as e:
        log.append(f"draft wasn't valid JSON: {e}")
        return out, log
    for t in data.get("titles", [])[:3]:
        t = str(t).strip().strip('"')
        why = _check(t, 14) or ("too long" if len(t) > 80 else None) or ("question" if "?" in t else None)
        (log.append(f"title dropped ({why}): {t!r}") if why else out["titles"].append(t))
    intro = str(data.get("intro", "")).strip()
    why = _check(intro, 125)
    if why:
        log.append(f"intro dropped ({why}): {intro[:120]!r}")
    else:
        out["intro"] = intro
    wanted = {r["symbol"] for r in facts["results"]}
    for sym, text in (data.get("results") or {}).items():
        text = str(text).strip()
        if sym not in wanted or llm.says_none(text):
            continue
        why = _check(text, 65)
        if why:
            log.append(f"{sym} paragraph dropped ({why}): {text[:120]!r}")
        else:
            out["results"][sym] = text
    for a in data.get("angles", [])[:3]:
        a = str(a).strip()
        why = _check(a, 35)
        (log.append(f"angle dropped ({why}): {a!r}") if why else out["angles"].append(a))
    return out, log


def draft(facts: dict, cfg: Config) -> tuple[dict, list[str]]:
    if not llm.enabled():
        return {"titles": [], "intro": None, "results": {}, "angles": []}, ["no ANTHROPIC_API_KEY, AI parts left blank"]
    wcfg = cfg.rules.get("weekly", {})
    raw = llm._ask(PROMPT.format(**_facts_text(facts, cfg)), max_tokens=2500,
                   model=wcfg.get("model", DEFAULT_MODEL), timeout=120, effort="medium")
    if raw is None:
        return {"titles": [], "intro": None, "results": {}, "angles": []}, ["AI call failed, AI parts left blank"]
    return parse_draft(raw, facts)


# ---------------------------------------------------------------- build

def fallback_titles(facts: dict) -> list[str]:
    names = [m["stock"].name for m in facts["most_covered"][:4]]
    if len(names) < 2:
        return []
    return [f"{names[0]} and {names[1]} led the week", f"{names[0]}, {names[2]} and a busy results week"
            if len(names) > 2 else f"{names[0]} and {names[1]}: the week in tech stocks"]


def build(db: DB, cfg: Config, rules: Rules, now: datetime, archive: Path) -> dict:
    facts = gather(db, cfg, rules, now)
    pieces, log = draft(facts, cfg)
    titles = pieces["titles"] or fallback_titles(facts)
    env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=False,
                      trim_blocks=False, lstrip_blocks=False)
    env.filters["md"] = lambda t: (t or "").replace("[", "(").replace("]", ")").replace("*", "")
    tz = ZoneInfo(cfg.site["timezone"])
    md = env.get_template("weekly.md").render(
        site=cfg.site, facts=facts, pieces=pieces, titles=titles, stocks=cfg.by_symbol,
        week_label=f"Week to {facts['end']:%A %-d %B %Y}",
        disclaimer=cfg.rules.get("email_disclaimer", cfg.rules.get("disclaimer", "")),
    ).strip() + "\n"
    md = re.sub(r"\n{3,}", "\n\n", md)
    archive.mkdir(parents=True, exist_ok=True)
    day = facts["end"].isoformat()
    for name in (f"{day}.md", "latest.md"):
        (archive / name).write_text(md, encoding="utf-8")
    (archive / f"{day}-log.txt").write_text(
        f"Built {now.astimezone(tz):%a %d %b %Y %H:%M} UK time, {facts['history_days']} days of history\n"
        + ("\n".join(log) if log else "All AI pieces passed the checks.") + "\n", encoding="utf-8")
    return {"date": day, "results": len(facts["results"]), "stories": len(facts["biggest"]),
            "ai_intro": bool(pieces["intro"]), "ai_results": len(pieces["results"]), "log": log}


def built(archive: Path, day: str) -> bool:
    return (archive / f"{day}.md").exists()
