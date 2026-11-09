"""Exclusions and section assignment. Pure keyword rules, no AI."""
from __future__ import annotations

import re

from .config import Config
from .tickers import Tagger


def _phrase_regex(phrases: list[str]) -> re.Pattern | None:
    words = [p for p in phrases if p]
    if not words:
        return None
    alts = sorted((re.escape(p.lower()) for p in words), key=len, reverse=True)
    return re.compile(r"(?<![\w])(" + "|".join(alts) + r")(?![\w])", re.IGNORECASE)


# Product reviews carry the year: "Pixel 11 2026 review", "OLED TV Review (2026)".
REVIEW_TITLE = re.compile(r"(\b20\d\d\s+review|\breview\s*\(20\d\d\))\s*$|\breview\s+[-–—]\s", re.IGNORECASE)
# Analyst calls and tips that the fixed phrases in rules.yaml miss because the words are spread out:
# "Buy Palantir ahead of earnings, says Rosenblatt", "TD Cowen cuts NXP stock target to $275",
# "Coinbase Target Cut to $250", "led one analyst to downgrade the stock". This site never runs
# anyone's buy or sell call or price target. Company deals ("agrees to buy Braze") don't match.
TIP_TITLE = re.compile(
    r"^(buy|sell|hold)\b(?! now, pay later)|"
    r"\b(buy|sell|hold)\b.{0,40}\b(ahead of|before|into|after) (its |the )?(earnings|results|report|print|q[1-4])\b|"
    r"\b(price |stock |share )?targets? (cut|raised|lowered|hiked|boosted|trimmed|slashed|lifted|increased|reduced)\b|"
    r"\b(cuts?|raises?|lowers?|lifts?|trims?|boosts?|hikes?|slashes?) .{0,50}\b(price|stock|share) targets?\b|"
    r"\bPT (raised|cut|lowered|to \$)|"
    r"\b(upgrades?|downgrades?|upgraded|downgraded|upgrading|downgrading)\b.{0,40}\b(the stock|the shares|its stock|"
    r"to (buy|sell|hold|neutral|outperform|underperform|overweight|underweight|equal.weight|market perform|accumulate|reduce))\b|"
    r"\b(stock|shares) (upgraded|downgraded)\b|\b(upgrade|downgrade)s? (\w+ )?(stock|shares)\b|"
    r"\b(initiates?|starts?|resumes?|reiterates?) (coverage|rating)\b|"
    r"\b(stock|shares)\b.{0,30}\b(could|will|set to|poised to) (see|drop|fall|plunge|soar|surge|rise|double|triple|crash|tank)\b",
    re.IGNORECASE)
# Quote and profile pages: "SHOP | Shopify Inc. Cl A Profile", "Marvell Stock Grades | MRVL".
# A stock quote page title: "BIOA.OQ", "1598stat.TW", "WKHS.MX".
TICKER_TITLE = re.compile(r"^[A-Za-z0-9^]{1,12}\.[A-Z]{1,3}$")
QUOTE_PAGE = re.compile(r"^[A-Z][A-Z.]{0,5}\s+\||\|\s*[A-Z][A-Z.]{0,5}\s*$")
# Pirate live-stream spam planted on hijacked sites: "𝚆𝙰𝚃𝙲𝙷 ... Live Streams", "【LIVESTREAMS】",
# "Ｒｅｄｄｉｔ Ｓｔｒｅａｍｓ". Fancy Unicode letters or the usual stream words give it away.
SPAM_TITLE = re.compile(
    r"[\U0001D400-\U0001D7FF\uFF21-\uFF3A\uFF41-\uFF5A\u3010\u3011]|"
    r"\b(buffstreams|crackstreams|methstreams|streameast|reddit (tv|streams?)|streams? free|free (live ?)?streams?|"
    r"watch free|live ?streams? (free|reddit|hd|online)|watch\b.{0,60}\blive ?streams?)\b",
    re.IGNORECASE)


class Rules:
    def __init__(self, cfg: Config):
        r = cfg.rules
        ex = r["exclude"]
        self.url_patterns = [p.lower() for p in ex["url_patterns"]]
        self.title_keywords = [k.lower() for k in ex["title_keywords"]] + [k.lower() for k in ex.get("law_firm_adverts", [])]
        self.title_exceptions = [k.lower() for k in ex.get("title_keyword_exceptions", [])]
        self.summary_keywords = [k.lower() for k in ex.get("summary_keywords", [])]
        self.junk_titles = {k.lower().strip() for k in ex.get("junk_titles", [])}
        self.google_min_words = int(ex.get("google_min_title_words", 3))
        self.wraps = [k.lower() for k in ex.get("wrap_patterns", [])]
        self.outlets = [k.lower() for k in ex.get("outlets", [])]
        self.tagger = Tagger(cfg)
        self.business_only = {s.symbol for s in cfg.stocks if s.business_only}
        self.business_re = _phrase_regex(ex.get("business_words", []))

        self.section_order = cfg.section_ids
        self.section_res = {sid: _phrase_regex(words) for sid, words in r["section_keywords"].items()}
        self.section_support_res = {sid: _phrase_regex(words) for sid, words in r.get("section_supporting_keywords", {}).items()}
        self.tech_re = _phrase_regex(r.get("tech_keywords", []))
        self.tech_sections = r.get("tech_sections", list(self.section_res))

    def is_tech(self, text: str) -> bool:
        if self.tech_re and self.tech_re.search(text):
            return True
        return any(self.section_res.get(sid) and self.section_res[sid].search(text) for sid in self.tech_sections)

    def is_consumer_only(self, title: str) -> bool:
        """A headline that names only business_only stocks and has no business word: a cast profile,
        a what-to-stream list. Naming another watched stock keeps it, so that stock's news isn't lost."""
        if not self.business_only or not self.business_re:
            return False
        found = self.tagger.find(title or "")
        return bool(found) and found <= self.business_only and not self.business_re.search(title)

    # ---------- exclusions ----------
    def junk_reason(self, title: str, url: str = "") -> str | None:
        """Google News searches pick up pages that aren't articles: section fronts ("Markets", "FX Center"),
        author pages ("Mark Zuckerberg", "Alex Stamos"), print pages ("Page A11"), stock quote pages
        ("WKHS.MX") and items whose headline is only the outlet's name ("- Arm Newsroom")."""
        t = (title or "").strip().lstrip("-–| ").strip()
        if not re.search(r"[A-Za-z]{2}", t) and not re.search(r"[^\x00-\x7f]{4}", t):
            return "title:no headline"
        if TICKER_TITLE.match(t):
            return "title:stock quote page"
        if t.lower() in self.junk_titles or re.match(r"^page [a-z]?_?\d+$", t.lower()):
            return "title:not an article"
        if "news.google.com" in (url or "") and len(t.split()) < self.google_min_words:
            return "title:too short for a headline"
        return None

    def exclusion_reason(self, title: str, url: str, summary: str = "", outlet: str = "") -> str | None:
        o = (outlet or "").lower()
        for k in self.outlets:
            if k in o:
                return f"outlet:{k}"
        u = (url or "").lower()
        for p in self.url_patterns:
            if p in u:
                return f"url:{p}"
        sm = (summary or "").lower()
        for k in self.summary_keywords:
            if k in sm:
                return f"summary:{k}"
        junk = self.junk_reason(title, u)
        if junk:
            return junk
        if SPAM_TITLE.search(title or ""):
            return "title:stream spam"
        if REVIEW_TITLE.search(title or ""):
            return "title:review"
        if QUOTE_PAGE.search(title or ""):
            return "title:quote page"
        if TIP_TITLE.search(title or ""):
            return "title:analyst call or tip"
        t = f" {(title or '').lower()} "
        for k in self.wraps:
            if k in t:
                return f"market wrap:{k}"
        for k in self.title_keywords:
            if k in t:
                if any(e in t for e in self.title_exceptions) and k in ("review:", "hands-on", "hands on:"):
                    continue
                return f"title:{k.strip()}"
        if self.is_consumer_only(title):
            return "title:consumer content, no business angle"
        return None

    # ---------- sections ----------
    def section_hits(self, text: str, title: str = "") -> dict[str, int]:
        hits = {}
        for sid in self.section_order:
            rx = self.section_res.get(sid)
            hits[sid] = len(rx.findall(text)) if rx else 0
            support = self.section_support_res.get(sid)
            if support and (hits[sid] or support.search(title)):
                hits[sid] += len(support.findall(text))
        return hits

    def pick_section(self, hits: dict[str, int], hint: list[str], primary: bool = False) -> str:
        best = max(hits.values()) if hits else 0
        if best == 0:
            # No keyword evidence, so all we have is the outlet's usual beat. That only counts
            # when the source is a company's own newsroom or the RNS.
            if primary:
                for h in hint:
                    if h in self.section_order:
                        return h
            return self.section_order[-1]  # the catch-all, Companies & Markets
        tied = [s for s in self.section_order if hits[s] == best]
        for h in hint:
            if h in tied:
                return h
        return tied[0]
