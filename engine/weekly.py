"""The weekly draft: a Saturday morning read on how the press covered the companies we follow that week,
for Andy to edit, add his own take to, and send on Substack.

It is about the coverage, not a rerun of the week's stories: the three stories the most outlets wrote
up and how each played out, which stories spread fastest and which faded, who got the attention and
who got none, and which of the outlets we track were on the big stories. Measures live in coverage.py.
Everything is counted across the outlets we track. We never say who broke a story.

One AI call drafts the intro, title ideas and three angles for Andy's take. Each piece is checked
against the advice, prediction and "who was first" filters. A piece that fails is left as a gap.

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

from . import coverage, llm, trends
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
    # Picked by when their articles came out, not the saved first_seen: a saved story can inherit an
    # older date when it's merged with an earlier one, which would hide this week's coverage.
    rows = db.conn.execute(
        "SELECT * FROM stock_stories WHERE last_seen >= ? ORDER BY outlets DESC, last_seen DESC",
        (iso(since),)).fetchall()
    published: dict[int, str] = {}
    want = sorted({i for r in rows for i in json.loads(r["item_ids"])})
    for k in range(0, len(want), 500):
        chunk = want[k:k + 500]
        published.update(db.conn.execute(
            f"SELECT id, published FROM items WHERE id IN ({','.join('?' * len(chunk))})", chunk).fetchall())
    cut = iso(since)
    rows = [r for r in rows if (lambda t: t and sum(x >= cut for x in t) * 2 >= len(t))(
        [published.get(i) for i in json.loads(r["item_ids"]) if published.get(i)])]  # mostly this week
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
    """The week as the press covered it. Counts are across the outlets we track."""
    wcfg = cfg.rules.get("weekly", {})
    days = int(wcfg.get("days", 7))
    start, end, since = _week(cfg, now, days)
    tz = ZoneInfo(cfg.site["timezone"])
    tagger = Tagger(cfg)
    groups = _story_groups(db, rules, since)
    skip = lambda g: bool(PREVIEW_RE.search(g["headline"]))  # a curtain-raiser isn't the story

    big = int(wcfg.get("big_story_outlets", coverage.BIG))
    out = coverage.outlets(db, groups, skip, big=big)
    arts = [a for a in out.pop("arts") if start <= coverage._when(a["published"]).astimezone(tz).date() <= end]

    results = _results(db, cfg, tagger, since, int(wcfg.get("max_results", 8)))

    section_names = {s["id"]: s["name"] for s in cfg.sections}
    # The week before only counts once the history covers all of it. A part week would compare
    # seven days against two or three and make every share look like it moved.
    first = db.conn.execute("SELECT MIN(date) AS d FROM section_daily").fetchone()["d"]
    full_prev = bool(first) and Date.fromisoformat(first) <= start - timedelta(days=days)
    shares = [dict(x, name=section_names.get(x["section"], x["section"]),
                   previous_share=x["previous_share"] if full_prev else None)
              for x in trends.section_share(db, days=days, until=end.isoformat())]

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
        "story_count": sum(1 for g in groups if g["outlets"] >= 2),  # one-outlet items aren't stories yet
        "big3": coverage.big_three(db, groups, tz, skip, big),
        "pace": coverage.pace(db, groups, tz, skip, big),
        "attention": coverage.attention(db, cfg, start, end, groups, int(wcfg.get("most_covered", 8))),
        "movers": coverage.movers(db, cfg, start, end),
        "outlets": out,
        "days_load": coverage.busiest_day(arts, tz),
        "results": results, "shares": shares, "coming": coming,
    }


# ---------------------------------------------------------------- AI draft

PROMPT = """You draft the weekly round-up for All Tech Stocks. It is a weekly read on how the press covered
US and UK listed tech companies, measured across the outlets the site tracks. Andy edits it, adds his own
view and signs it. It is never an advice service.

The week: {week}.

The three stories the most outlets wrote up:
{big3}

Pace among the week's {big_count} big stories ({big} or more outlets):
{pace}

Share of the week's coverage by company:
{attention}

Results that drew the press this week (outlets):
{results}

Reply with JSON only, in exactly this shape:
{{"titles": ["...", "...", "..."], "intro": "...", "angles": ["...", "...", "..."]}}

titles: three subject lines, 70 characters or fewer, about where the press attention went this week.
  Each names at least two companies.
intro: four to six short sentences, 110 words or fewer, on where the press attention went and how it
  behaved: what dominated, what spread fast, what faded, who was ignored. Use the numbers given.
angles: three questions Andy could answer in his own take, each one sentence, about what the pattern of
  coverage says about the companies or the press (why one story ran for days, why UK names drew so
  little attention). Never about the shares.

Strict rules for every piece of text:
- Never say or suggest which outlet broke a story or was first. We can't know that.
- Timings are across the outlets we track. Don't present them as the whole press.
- Never say or hint whether to buy, sell or hold anything. Never mention shares, share prices, valuations,
  returns or investors, even if a headline does.
- Never predict, and don't explain why coverage behaved as it did unless the facts say so. Questions in
  the angles are fine.
- Use only the facts given. No figures that aren't here.
- Plain British English. Vary sentence length. Contractions are fine. A sharp, plain-speaking editor's voice.
- No dashes of any kind, no semicolons, no comma before "and" in a list, no hype words. Don't greet or sign off."""


def _hours(h: float | None) -> str:
    if h is None:
        return "n/a"
    if h < 1:
        m = max(1, round(h * 60))
        return f"{m} minute" if m == 1 else f"{m} minutes"
    if h < 48:
        return f"{h:.0f} hours" if h >= 1.5 else "about an hour"
    return f"{h / 24:.1f} days".replace(".0 days", " days")


def _facts_text(facts: dict, cfg: Config) -> dict[str, str]:
    big3 = "\n".join(
        f"- {g['headline']} [{', '.join(g['symbols'][:3])}]: {g['outlets']} outlets, {g['articles']} articles; "
        f"reached {facts['pace']['big']} outlets {_hours(g['to_big'])} after the first article we saw; 90% of its articles out within "
        f"{_hours(g['most_within'])}; peak day {g['peak_day']:%A %-d %B}" for g in facts["big3"]) or "- none"
    p = facts["pace"]
    pace = "\n".join(x for x in [
        f"- Fastest to {p['big']} outlets: {p['fastest']['headline']} ({_hours(p['fastest']['to_big'])})" if p["fastest"] else "",
        f"- Longest running: {p['longest']['headline']} (90% out within {_hours(p['longest']['most_within'])})" if p["longest"] else "",
        f"- Quickest to fade: {p['shortest']['headline']} (90% out within {_hours(p['shortest']['most_within'])})" if p["shortest"] else "",
    ] if x) or "- none"
    a = facts["attention"]
    attention = "\n".join(f"- {r['stock'].name}: {r['share']:.1f}%" for r in a["top"])
    attention += (f"\n- The top three took {a['top3_share']:.0f}% between them."
                  f"\n- US companies {a['markets'].get('US', 0)}%, UK companies {a['markets'].get('UK', 0)}%. "
                  f"{a['uk_covered']} of the {a['uk_count']} UK companies we follow got any coverage."
                  f"\n- {len(a['quiet'])} of the {len(cfg.stocks)} companies we follow got none.")
    results = "\n".join(f"- {r['name']}: {r['coverage']}" for r in facts["results"]) or "- none"
    week = f"{facts['start']:%-d %B} to {_last_weekday(facts['end']):%A %-d %B %Y}"
    return {"big3": big3, "pace": pace, "attention": attention, "results": results, "week": week,
            "big_count": str(p["count"]), "big": str(p["big"])}


# Self-talk and guessing. Unlike the daily lines, the weekly is about coverage, so a plain sentence
# about outlets or coverage is fine here.
SELF_TALK = re.compile(r"\bI\b(?!-\d)|\bI'm\b|\bI've\b|\bwe (can't|cannot)\b|speculat\w*|"
                       r"\b(the|source|this) material\b|\bmaterial provided\b|\bnothing in the\b", re.I)
FIRST_RE = re.compile(r"\b(broke|breaking|first to|was first|scoop\w*|exclusive\w*|beat (everyone|rivals))\b", re.I)


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
    m = SELF_TALK.search(text)
    if m:
        return f"talks about itself: {m.group(0)}"
    m = FIRST_RE.search(text)
    if m:
        return f"says who had it first: {m.group(0)}"
    if re.search(r"\b(stock|stocks|shares|investors?)\b", text, re.I):
        return "mentions the shares"
    return None


def parse_draft(raw: str | None, facts: dict) -> tuple[dict, list[str]]:
    """Turn the model's JSON into checked pieces. Anything that fails a rule is dropped and logged."""
    out = {"titles": [], "intro": None, "angles": []}
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
    for a in data.get("angles", [])[:3]:
        a = str(a).strip()
        why = _check(a, 35)
        (log.append(f"angle dropped ({why}): {a!r}") if why else out["angles"].append(a))
    return out, log


def draft(facts: dict, cfg: Config) -> tuple[dict, list[str]]:
    if not llm.enabled():
        return {"titles": [], "intro": None, "angles": []}, ["no ANTHROPIC_API_KEY, AI parts left blank"]
    wcfg = cfg.rules.get("weekly", {})
    prompt = PROMPT.format(**_facts_text(facts, cfg))
    model = wcfg.get("model", DEFAULT_MODEL)
    raw = llm._ask(prompt, max_tokens=2500, model=model, timeout=180, effort="medium")
    log: list[str] = []
    if raw is None:
        # One retry on the cheaper daily model, so a bad week on one model still gets an intro.
        log.append(f"AI call on {model} failed: {llm.LAST_ERROR}")
        raw = llm._ask(prompt, max_tokens=2500, model=llm.DEFAULT_MODEL, timeout=180, effort="medium")
        if raw is None:
            log.append(f"retry on {llm.DEFAULT_MODEL} failed too: {llm.LAST_ERROR}")
            return {"titles": [], "intro": None, "angles": []}, log + ["AI parts left blank"]
        log.append(f"drafted on {llm.DEFAULT_MODEL} instead")
    pieces, checks = parse_draft(raw, facts)
    return pieces, log + checks


# ---------------------------------------------------------------- build

def fallback_titles(facts: dict) -> list[str]:
    names = [r["stock"].name for r in facts["attention"]["top"][:3]]
    if len(names) < 2:
        return []
    return [f"{names[0]} and {names[1]} took the week's headlines",
            f"Where the press looked: {', '.join(names[:-1])} and {names[-1]}"]


def _last_weekday(d: Date) -> Date:
    """Built on a Saturday, the week it covers ended on Friday."""
    while d.weekday() > 4:
        d -= timedelta(days=1)
    return d


def build(db: DB, cfg: Config, rules: Rules, now: datetime, archive: Path) -> dict:
    facts = gather(db, cfg, rules, now)
    pieces, log = draft(facts, cfg)
    titles = pieces["titles"] or fallback_titles(facts)
    env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=False,
                      trim_blocks=False, lstrip_blocks=False)
    env.filters["md"] = lambda t: (t or "").replace("[", "(").replace("]", ")").replace("*", "")
    env.filters["hours"] = _hours
    tz = ZoneInfo(cfg.site["timezone"])
    md = env.get_template("weekly.md").render(
        site=cfg.site, facts=facts, pieces=pieces, titles=titles, stocks=cfg.by_symbol,
        week_label=f"Week to {_last_weekday(facts['end']):%A %-d %B %Y}",
        disclaimer=cfg.rules.get("email_disclaimer", cfg.rules.get("disclaimer", "")),
    ).strip() + "\n"
    md = re.sub(r"\n{3,}", "\n\n", md)
    archive.mkdir(parents=True, exist_ok=True)
    day = facts["end"].isoformat()
    for name in (f"{day}.md", "latest.md"):
        (archive / name).write_text(md, encoding="utf-8")
    _cover(facts, cfg, titles, archive, day, log)
    (archive / f"{day}-log.txt").write_text(
        f"Built {now.astimezone(tz):%a %d %b %Y %H:%M} UK time, {facts['history_days']} days of history\n"
        + ("\n".join(log) if log else "All AI pieces passed the checks.") + "\n", encoding="utf-8")
    return {"date": day, "results": len(facts["results"]), "big_stories": facts["pace"]["count"],
            "ai_intro": bool(pieces["intro"]), "ai_angles": len(pieces["angles"]), "log": log}


def _cover(facts: dict, cfg: Config, titles: list[str], archive: Path, day: str, log: list[str]) -> None:
    """The Substack cover for the weekly: the first title idea, the week's story count on the side.
    Saved as <date>-cover.png and latest-cover.png. A failed cover never stops the draft."""
    try:
        from . import cover
        words = next((t for t in titles if len(t) <= 90), None) or facts["big3"][0]["headline"]
        found = Tagger(cfg).find(words)
        low = words.lower()

        def pos(t: str) -> int:  # where the title first names the stock, so chips read in title order
            st = cfg.by_symbol[t]
            hits = [low.find(a.lower()) for a in [t, st.name] + st.aliases + st.exact if a and a.lower() in low]
            return min(hits or [999])
        tks = sorted(found, key=pos) or [r["symbol"] for r in facts["attention"]["top"][:3]]
        when = datetime.combine(facts["end"], datetime.min.time())
        png = cover.render(words, facts["story_count"], tks[:3], when, archive / f"{day}-cover.png",
                           labels=("STORIES", "COVERED", "THIS WEEK"),
                           dateline=f"Week to {_last_weekday(facts['end']):%A %-d %B}", brand="ALL TECH STOCKS WEEKLY")
        (archive / "latest-cover.png").write_bytes(png.read_bytes())
    except Exception as e:
        log.append(f"cover failed: {e}")


def built(archive: Path, day: str) -> bool:
    return (archive / f"{day}.md").exists()
