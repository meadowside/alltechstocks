"""Groups items into stories (clusters) and scores them."""
from __future__ import annotations

from collections import Counter
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from .config import Config, Source
from .db import DB, parse_iso
from .fetch import outlet_label
from .rules import Rules
from .tickers import Tagger

# Which outlet's headline and link we prefer: national, then UK specialist, global, primary, backfill.
TIER_RANK = {1: 0, 2: 1, 3: 2, 4: 3, 0: 4}
EXCLUSIVE_RE = re.compile(r"\bexclusive\b", re.IGNORECASE)
# Curtain-raisers: fine as coverage, never as the headline once the real thing has happened.
PREVIEW_RE = re.compile(
    r"\bwhat to (expect|watch|look for)\b|\bwhat (you|we) (need to |should )?know\b|\bhow to watch\b|"
    r"\bpreview\b|\blive (updates|blog|coverage|stream)\b|\b(expected|set|poised) to (announce|unveil|reveal|launch)\b|"
    r"\bwhat .{0,40} (could|might|will) (announce|unveil|reveal)\b", re.IGNORECASE)


@dataclass
class Outlet:
    key: str
    name: str
    source: Source | None
    items: list = field(default_factory=list)

    @property
    def tier(self) -> int:
        return self.source.tier if self.source else 3

    @property
    def weight(self) -> float:
        return self.source.weight if self.source else 1.0

    @property
    def domain(self) -> str | None:
        return self.source.domain if self.source else None


@dataclass
class Story:
    items: list
    outlets: dict[str, Outlet]
    first_seen: datetime
    last_seen: datetime
    coverage: float = 0.0
    relevance: int = 1
    score: float = 0.0
    section: str = ""
    is_global: bool = False
    eligible: bool = True
    why_not: str | None = None
    headline: str = ""
    teaser: str = ""
    link: str = ""
    link_outlet: Outlet | None = None
    paywall: bool = False
    link_options: list = field(default_factory=list)
    exclusive: bool = False
    regulator: bool = False          # an official source (company, exchange) is in the story
    why: str = ""
    so_what: str = ""   # AI line, when there is one
    core_ids: list = field(default_factory=list)  # items that match the headline
    tickers: list = field(default_factory=list)          # watched symbols, most mentioned first
    headline_tickers: set = field(default_factory=set)   # those named in a headline
    is_update: bool = False

    @property
    def key(self) -> int:
        return min(i["id"] for i in self.items)

    def symbol_rank(self, symbol: str) -> int:
        """0 if the stock is named in a headline here, 1 if only in the text."""
        return 0 if symbol in self.headline_tickers else 1

    @property
    def item_ids(self) -> list[int]:
        return sorted(i["id"] for i in self.items)

    @property
    def source_count(self) -> int:
        return len(self.outlets)

    def use_link(self, outlet: Outlet, url: str) -> None:
        self.link_outlet, self.link = outlet, url
        self.paywall = bool(outlet.source and outlet.source.paywall)

    def sources(self) -> list[tuple[Outlet, dict]]:
        """Every outlet with a link to its own article, national first. Prefers real article
        links over Google News redirects."""
        out = []
        for o in sorted(self.outlets.values(), key=lambda o: (TIER_RANK.get(o.tier, 9), o.name)):
            item = next((i for i in o.items if "news.google.com" not in i["url"]), o.items[0])
            out.append((o, item))
        return out

    def other_coverage(self, limit: int = 6) -> list[tuple[Outlet, dict]]:
        """One link per other outlet, best outlets first, for the 'more coverage' row."""
        out = []
        for o in sorted(self.outlets.values(), key=lambda o: (TIER_RANK.get(o.tier, 9), o.name)):
            if o is self.link_outlet:
                continue
            item = next((i for i in o.items if "news.google.com" not in i["url"]), o.items[0])
            out.append((o, item))
        return out[:limit]


def _outlet_key(item, cfg: Config | None = None) -> str:
    """One key per outlet, whatever feed it came from: the FT's technology and companies feeds
    are one outlet, so it counts once."""
    src = cfg.by_id.get(item["outlet_id"]) if (cfg and item["outlet_id"]) else None
    name = src.name if src else outlet_label(item["outlet_name"])
    return "name:" + name.lower()


def cluster(items: list, cfg: Config) -> list[list]:
    """Greedy clustering on TF-IDF vectors. Each item joins the closest open story or starts a new one."""
    if not items:
        return []
    c = cfg.clustering
    window = timedelta(hours=c["window_hours"])
    close_after = timedelta(hours=c["close_after_hours"])
    t_link = float(c["min_link_similarity"])
    t_centroid = float(c["min_centroid_similarity"])

    # "REG - Company PLC - " is boilerplate on every UK announcement. Left in, it makes unrelated
    # announcements look alike.
    def strip_reg(t: str) -> str:
        return re.sub(r"^REG\s*-\s*", "", t or "")
    texts = [f"{strip_reg(i['title'])}. {strip_reg(i['title'])}. {i['summary'] or ''}" for i in items]  # title counts double
    tagger = Tagger(cfg)
    heads = [tagger.find(i["title"]) for i in items]
    vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True)
    X = vec.fit_transform(texts)  # sparse, each row has unit length
    # Every article-to-article similarity in one go. A story's centroid is never stored as a vector:
    # its similarity to an article is the sum of that article's similarities to the members, divided
    # by the length of the summed vector, and that length comes from the same table. Same answers as
    # comparing full vectors, without holding thousands of 40,000-wide vectors in memory.
    S = (X @ X.T).toarray()

    # An item joins the story whose closest member is most like it, provided it's also
    # reasonably close to the story as a whole (stops one loose match chaining two stories).
    # Two articles whose headlines name different watched stocks, and none in common, are never
    # the same story however alike their wording.
    groups: list[dict] = []  # {"idx": [...], "n2": squared length of the summed vector, "first", "last", "tk"}
    for idx, item in enumerate(items):
        t = parse_iso(item["published"])
        row = S[idx]
        best, best_sim, best_dot = None, 0.0, 0.0
        for g in groups:
            if t - g["last"] > window or t - g["first"] > close_after:
                continue
            if heads[idx] and g["tk"] and not (heads[idx] & g["tk"]):
                continue
            sims = row[g["idx"]]
            link = float(sims.max())
            dot = float(sims.sum())
            centroid_sim = dot / (np.sqrt(g["n2"]) or 1.0)
            if link >= t_link and centroid_sim >= t_centroid and link > best_sim:
                best, best_sim, best_dot = g, link, dot
        if best is not None:
            best["idx"].append(idx)
            best["n2"] += 2 * best_dot + float(S[idx, idx])
            best["last"] = max(best["last"], t)
            best["tk"] |= heads[idx]
        else:
            groups.append({"idx": [idx], "n2": float(S[idx, idx]), "first": t, "last": t, "tk": set(heads[idx])})

    # Second pass: merge stories that ended up split (one outlet worded it very differently).
    # P[a, b] is the dot product of story a's summed vector with story b's.
    t_merge = float(c["merge_similarity"])
    if len(groups) > 1:
        M = np.zeros((len(groups), len(items)))
        for gi, g in enumerate(groups):
            M[gi, g["idx"]] = 1.0
        P = (M @ S) @ M.T
    merged = True
    while merged and len(groups) > 1:
        merged = False
        norms = np.sqrt(np.clip(np.diag(P), 0, None))
        norms[norms == 0] = 1.0
        sims = P / np.outer(norms, norms)
        np.fill_diagonal(sims, 0)
        pairs = np.argwhere(np.triu(sims) >= t_merge)
        for a, b in sorted(pairs.tolist(), key=lambda p: -sims[p[0], p[1]]):
            ga, gb = groups[a], groups[b]
            if max(ga["last"], gb["last"]) - min(ga["first"], gb["first"]) > close_after:
                continue
            if ga["tk"] and gb["tk"] and not (ga["tk"] & gb["tk"]):
                continue
            ga["idx"] += gb["idx"]
            ga["first"], ga["last"] = min(ga["first"], gb["first"]), max(ga["last"], gb["last"])
            ga["tk"] |= gb["tk"]
            P[a, :] += P[b, :]
            P[:, a] += P[:, b]
            P = np.delete(np.delete(P, b, axis=0), b, axis=1)
            groups.pop(b)
            merged = True
            break
    return [[items[i] for i in g["idx"]] for g in groups]


def _keep(item, cfg: Config, rules: Rules, tagger: Tagger) -> bool:
    """Every article must name a watched stock or be about tech on its own, before grouping.
    Otherwise a general business item (oil prices, a job advert) can join a story and take over
    its headline."""
    if rules.exclusion_reason(item["title"], item["url"], item["summary"] or "", item["outlet_name"]):  # rules edited since fetch
        return False
    src = cfg.by_id.get(item["source_id"])
    if src and src.ticker:
        return True
    text = f"{item['title']}. {item['summary'] or ''}"
    if tagger.find(text):
        return True
    if src and src.is_primary:
        return False  # an exchange or press wire item about a company we don't watch
    return rules.is_tech(text)


def _own_ticker(cfg: Config):
    def f(item):
        src = cfg.by_id.get(item["source_id"])
        return src.ticker if src else None
    return f


def build_stories(db: DB, cfg: Config, rules: Rules, now: datetime) -> list[Story]:
    since = now - timedelta(hours=cfg.clustering["close_after_hours"])
    tagger = Tagger(cfg)
    items = [i for i in db.items_since(since) if _keep(i, cfg, rules, tagger)]
    stories = [_make_story(group, cfg, rules, tagger, now) for group in cluster(items, cfg)]
    stories.sort(key=lambda s: s.score, reverse=True)
    return stories


def rank_stocks(stories: list[Story], cfg: Config, now: datetime, hours: int = 24) -> list[dict]:
    """Watched stocks ranked by news coverage: the score of every live story they're in,
    full weight when named in a headline and half when only in the text. Coverage, not merit."""
    since = now - timedelta(hours=hours)
    rows: dict[str, dict] = {}
    for s in stories:
        if not s.eligible or s.last_seen < since:
            continue
        for sym in s.tickers:
            r = rows.setdefault(sym, {"stock": cfg.by_symbol[sym], "score": 0.0, "stories": [], "outlets": set()})
            r["score"] += s.score * (1.0 if sym in s.headline_tickers else 0.5)
            r["stories"].append(s)
            r["outlets"] |= set(s.outlets)
    out = sorted(rows.values(), key=lambda r: (-r["score"], r["stock"].symbol))
    for r in out:
        r["stories"].sort(key=lambda s: (s.symbol_rank(r["stock"].symbol), -s.score))
        r["top"] = r["stories"][0]
        r["outlet_count"] = len(r["outlets"])
        r["story_count"] = len(r["stories"])
    return out


def _make_story(group: list, cfg: Config, rules: Rules, tagger: Tagger, now: datetime) -> Story:
    sc = cfg.scoring
    outlets: dict[str, Outlet] = {}
    for item in group:
        k = _outlet_key(item, cfg)
        if k not in outlets:
            src = cfg.by_id.get(item["outlet_id"]) if item["outlet_id"] else None
            outlets[k] = Outlet(key=k, name=outlet_label(item["outlet_name"]), source=src)
        outlets[k].items.append(item)

    times = [parse_iso(i["published"]) for i in group]
    s = Story(items=group, outlets=outlets, first_seen=min(times), last_seen=max(times))

    # Coverage: each outlet counts once, at its weight.
    s.coverage = sum(o.weight for o in outlets.values())
    # Tickers and relevance are worked out every run (not stored at fetch time) so watchlist edits apply at once.
    s.tickers, s.relevance, s.headline_tickers = tagger.tag_story(group, _own_ticker(cfg))
    s.regulator = any(o.source and o.source.is_primary for o in outlets.values())
    s.exclusive = (
        len(outlets) == 1
        and next(iter(outlets.values())).tier in (1, 2)
        and any(EXCLUSIVE_RE.search(i["title"]) for i in group)
    )
    coverage = max(s.coverage, sc["exclusive_floor"]) if s.exclusive else s.coverage

    # Eligibility. is_global marks a market-wide story: no single watched stock, just the sector.
    if all(o.source and o.source.confirm for o in outlets.values()):
        s.eligible, s.why_not = False, "official source with no outlet coverage yet"
    elif s.tickers:
        s.eligible = True
    elif s.relevance == 2:
        min_cov = cfg.rules.get("market", {}).get("wide_min_coverage", sc["global_min_coverage"])
        if s.coverage >= min_cov:
            s.eligible, s.is_global = True, True
        else:
            s.eligible, s.why_not = False, f"market-wide story below {min_cov} coverage"
    else:
        s.eligible, s.why_not = False, "no watched stock named"

    # Score.
    u = 1.0 if s.relevance >= 3 else 0.7 if s.relevance == 2 else 0.5
    # Age from the story's middle article, not its latest. A big story keeps collecting a few
    # late write-ups for days; using the latest one would keep it looking fresh far too long.
    times_sorted = sorted(times)
    centre = times_sorted[len(times_sorted) // 2]
    hours = max(0.0, (now - centre).total_seconds() / 3600)
    fresh = 0.5 ** (hours / sc["freshness_half_life_hours"])
    s.score = min(coverage, sc["coverage_cap"]) * u * fresh + (sc["regulator_bonus"] if s.regulator else 0)

    # Section: keyword hits across every item, tie broken by the lead outlet's hint.
    hits = {sid: 0 for sid in cfg.section_ids}
    for i in group:
        for sid, n in rules.section_hits(f"{i['title']}. {i['summary'] or ''}").items():
            hits[sid] += n

    # Headline from the best outlet; link to the best free, non-redirect copy.
    # An outlet whose headline names one of the story's stocks beats one that doesn't, so a loosely
    # grouped piece (a Zoom blog post in an IBM story) never takes the headline.
    named = set(s.headline_tickers)
    def names_it(i) -> bool:
        return bool(named & tagger.find(i["title"]))

    # The headline must be about what happened, not a curtain-raiser. A story that runs for days
    # (a keynote trailed on Thursday, held on Tuesday) otherwise keeps its first article, a "what
    # you need to know" piece, as the headline and link. Previews only lead when they're all there is.
    def current(i) -> bool:
        return not PREVIEW_RE.search(i["title"])

    # Each outlet's link is its most typical article in the group: the one whose title words
    # turn up most across the other titles. A group can hold loosely related pieces from one
    # outlet (a Sky story on iPhone buying mistakes beside the foldable iPhone launch), and
    # "the outlet's first article" can be the odd one out. Same approach as All UK Tech.
    def words(t: str) -> set:
        return {w for w in WORD_RE.findall((t or "").lower()) if len(w) > 3}
    df = Counter(w for i in group for w in words(i["title"]))

    def typical(item) -> float:
        ws = words(item["title"])
        return sum(df[w] for w in ws) / len(ws) if ws else 0.0

    def first_time(o: Outlet):
        cur = [parse_iso(i["published"]) for i in o.items if current(i)]
        return min(cur or [parse_iso(i["published"]) for i in o.items])
    # When the story began as a preview, the real news came later and in many parts (seven chip
    # partners at one keynote). Then the article most typical of the whole group leads, so one side
    # angle doesn't stand for the day. Stories that never had a preview keep the earliest article.
    old_lead = min(sorted(outlets.values(), key=lambda o: (0 if not named or any(names_it(i) for i in o.items) else 1,
                                                           TIER_RANK.get(o.tier, 9),
                                                           min(parse_iso(i["published"]) for i in o.items)))[0].items,
                   key=lambda i: (0 if not named or names_it(i) else 1, i["published"]))
    trailed = not current(old_lead)  # the earliest best article is a preview

    def fit(i) -> int:
        return -round(typical(i)) if trailed and current(i) else 0
    ranked = sorted(outlets.values(), key=lambda o: (0 if not named or any(names_it(i) for i in o.items) else 1,
                                                     0 if any(current(i) for i in o.items) else 1,
                                                     TIER_RANK.get(o.tier, 9), min([fit(i) for i in o.items]),
                                                     first_time(o)))
    lead = ranked[0]
    lead_item = min(lead.items, key=lambda i: (0 if not named or names_it(i) else 1,
                                               0 if current(i) else 1, fit(i), i["published"]))
    s.headline = lead_item["title"]
    s.section = rules.pick_section(hits, lead.source.sections if lead.source else [])
    if s.is_global and "companies" in cfg.section_ids:
        s.section = "companies"  # market-wide stories sit with the general news

    # Another outlet's article only stands in for the headline when its title shares most of
    # the top outlet's headline words. Otherwise a loosely related piece in the group (a Sky
    # story on iPhone buying mistakes beside the foldable iPhone launch) takes over the story.
    lead_words = _title_words(lead_item["title"])

    def same_story(item) -> bool:
        if item is lead_item:
            return True
        w = _title_words(item["title"])
        if not lead_words or not w:
            return False
        return len(lead_words & w) / min(len(lead_words), len(w)) >= cfg.rules["clustering"].get("link_title_overlap", 0.5)

    def linkable(o: Outlet, free: bool):
        if free and o.source and o.source.paywall:
            return None
        real = [i for i in o.items if "news.google.com" not in i["url"] and same_story(i)]
        return max(real, key=lambda i: (current(i), typical(i))) if real else None

    # Every outlet we could link, best first: free before paywalled, real URLs before Google redirects.
    options, chosen = [], []
    for free in (True, False):
        for o in ranked:
            item = linkable(o, free)
            if item and all(opt[0] is not o for opt in options):
                options.append((o, item["url"]))
                chosen.append(item)
    if all(opt[0] is not lead for opt in options):
        options.append((lead, lead_item["url"]))  # the headline's own article, via Google if need be
        chosen.append(lead_item)
    # The headline is the linked article's own title, so the two always agree.
    s.headline = chosen[0]["title"]
    # The articles this story is really about: AI lines are saved against and read from these only,
    # so an article that drifts into a different group can't bring its old line with it.
    s.core_ids = [i["id"] for i in group if same_story(i)]
    s.link_options = options
    s.link_outlet, s.link = options[0]
    s.paywall = bool(s.link_outlet.source and s.link_outlet.source.paywall)

    s.teaser = best_teaser(group, s.headline)
    return s


WORD_RE = re.compile(r"[a-z0-9£$]+")
TITLE_STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "as", "at", "by", "from",
              "is", "are", "its", "it", "after", "over", "new", "says", "say", "s"}


def _title_words(title: str) -> set[str]:
    t = re.sub(r"\s[-|–]\s[^-|–]+$", "", (title or "").lower())  # drop a trailing " - Outlet name"
    return {w for w in WORD_RE.findall(t) if w not in TITLE_STOP}
def best_teaser(group: list, headline: str) -> str:
    """The summary that shares most words with the headline. Roundup posts often carry a summary
    about a different story, so the link's own summary can't be trusted."""
    head = {w for w in WORD_RE.findall(headline.lower()) if len(w) > 3}
    best, best_overlap = "", 0
    for i in group:
        if "news.google.com" in i["url"]:  # Google's "summary" is just the headline again
            continue
        text = first_sentence(i["summary"] or "", headline)
        overlap = len(head & set(WORD_RE.findall(text.lower())))
        if overlap > best_overlap:
            best, best_overlap = text, overlap
    return best if best_overlap >= 2 else ""


def clean_text(text: str | None) -> str:
    """Plain text only, even for summaries stored before the fetch cleaner was fixed."""
    from .fetch import clean
    return clean(text)


def first_sentence(text: str, headline: str, limit: int = 180) -> str:
    text = clean_text(text)
    if not text or text.lower().startswith(headline.lower()[:40]):
        return ""
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    out = m.group(1) if m else text
    if len(out) > limit:
        out = out[:limit].rsplit(" ", 1)[0] + "..."
    return out


def why_template(story: Story, rank_in_section: int, overall_rank: int, cfg: Config) -> str:
    """The free 'why it's here' line, built from the data. Coverage only, never a view on the shares."""
    big = [o.name for o in sorted(story.outlets.values(), key=lambda o: TIER_RANK.get(o.tier, 9)) if o.tier == 1][:2]
    n = story.source_count
    parts = []
    if n == 1:
        parts.append(f"Only {story.link_outlet.name} has this so far." if not story.exclusive
                     else f"A {story.link_outlet.name} exclusive.")
    else:
        lead = f"Covered by {n} outlets"
        if big:
            lead += " including " + " and ".join(big)
        parts.append(lead + ".")
    if story.regulator:
        off = next(o.name for o in story.outlets.values() if o.source and o.source.is_primary)
        parts.append(f"Includes the official word from {off}.")
    if overall_rank == 1:
        parts.append("The most covered tech stock story right now.")
    elif rank_in_section == 1:
        parts.append(f"Top {cfg.section_names[story.section]} story right now.")
    return " ".join(parts)
