"""Tags text with the watched stocks it mentions. Pure pattern matching, no AI.

A stock is found by any of:
  - its aliases from watchlist.yaml, whole words, any case ("Nvidia", "nvidia's")
  - its exact aliases, whole words, exact capitals ("Arm", "Meta")
  - a cashtag ($NVDA) or an exchange-and-ticker mention ("(NASDAQ: NVDA)", "LON:SGE")
"""
from __future__ import annotations

import re

from .config import Config

EXCHANGE_TICKER = re.compile(
    r"\b(NASDAQ|Nasdaq|NYSE|LSE|LON|AIM)\s*:\s*([A-Z][A-Z0-9]{0,5}(?:\.[A-Z])?)\b")
CASHTAG = re.compile(r"(?<![\w$])\$([A-Z][A-Z0-9]{0,5})\b")


def _alternation(words: list[str]) -> str:
    return "|".join(sorted((re.escape(w) for w in words), key=len, reverse=True))


class Tagger:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        loose: dict[str, str] = {}
        exact: dict[str, str] = {}
        for s in cfg.stocks:
            for a in s.aliases:
                loose[a.lower()] = s.symbol
            for a in s.exact:
                exact[a] = s.symbol
        self.loose_map, self.exact_map = loose, exact
        ignore = [p for st in cfg.stocks for p in st.ignore]
        self.ignore_re = re.compile(r"(?<![\w])(" + _alternation(ignore) + r")(?![\w])", re.IGNORECASE) if ignore else None
        self.loose_re = re.compile(r"(?<![\w])(" + _alternation(list(loose)) + r")(?![\w])", re.IGNORECASE) if loose else None
        self.exact_re = re.compile(r"(?<![\w])(" + _alternation(list(exact)) + r")(?![\w])") if exact else None
        self.us_symbols = {s.symbol for s in cfg.stocks if s.market == "US"}
        self.uk_symbols = {s.symbol for s in cfg.stocks if s.market == "UK"}
        wide = cfg.rules.get("market", {}).get("wide", [])
        self.wide_re = re.compile(r"(?<![\w])(" + _alternation(wide) + r")(?![\w])", re.IGNORECASE) if wide else None

    def find(self, text: str) -> set[str]:
        """Every watched symbol mentioned in the text."""
        if not text:
            return set()
        if self.ignore_re:
            text = self.ignore_re.sub(" ", text)
        found: set[str] = set()
        if self.loose_re:
            found |= {self.loose_map[m.lower()] for m in self.loose_re.findall(text)}
        if self.exact_re:
            found |= {self.exact_map[m] for m in self.exact_re.findall(text)}
        for exch, sym in EXCHANGE_TICKER.findall(text):
            pool = self.uk_symbols if exch.upper() in ("LSE", "LON", "AIM") else self.us_symbols
            if sym in pool:
                found.add(sym)
        for sym in CASHTAG.findall(text):
            if sym in self.us_symbols:  # cashtags are a US habit; $SGE etc. would be guesswork
                found.add(sym)
        return found

    def is_market_wide(self, text: str) -> bool:
        return bool(self.wide_re and self.wide_re.search(text or ""))

    def tag_story(self, items: list, source_ticker=lambda item: None) -> tuple[list[str], int, set[str]]:
        """Returns (symbols, relevance, symbols named in a headline) for a group of items.

        A stock belongs to the story only if enough of the coverage is about it: named in at least a
        quarter of the headlines (and in two or more once a story has three or more articles), or in the
        summaries of half the articles. One stray mention in one article doesn't count. Symbols come back
        most-mentioned first.
        relevance: 3 a stock in the headlines, 2 summaries only or a market-wide story, 1 neither.
        """
        n = len(items)
        title_hits: dict[str, int] = {}
        text_hits: dict[str, int] = {}
        wide = False
        for i in items:
            title, summary = i["title"] or "", i["summary"] or ""
            t = self.find(title)
            own = source_ticker(i)
            if own:
                t.add(own)
            for sym in t:
                title_hits[sym] = title_hits.get(sym, 0) + 1
            for sym in t | self.find(summary):
                text_hits[sym] = text_hits.get(sym, 0) + 1
            wide = wide or self.is_market_wide(f"{title}. {summary}")
        need_title = max(1 if n < 3 else 2, -(-n // 4))
        in_title = {sym for sym, k in title_hits.items() if k >= need_title}
        in_text = {sym for sym, k in text_hits.items() if k >= max(2, -(-n // 2))}
        keep = sorted(in_title | in_text, key=lambda sym: (-title_hits.get(sym, 0), -text_hits[sym], sym))
        if in_title:
            rel = 3
        elif keep or wide:
            rel = 2
        else:
            rel = 1
        return keep, rel, in_title
