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

        A stock belongs to the story if it's in any headline, or in the summaries of at least
        two articles. Symbols come back most-mentioned first.
        relevance: 3 a stock in a headline, 2 a stock in summaries only or a market-wide story, 1 neither.
        """
        weight: dict[str, int] = {}
        in_title: set[str] = set()
        summary_hits: dict[str, int] = {}
        wide = False
        for i in items:
            title, summary = i["title"] or "", i["summary"] or ""
            t = self.find(title)
            own = source_ticker(i)
            if own:
                t.add(own)
            s = self.find(summary) - t
            in_title |= t
            for sym in t:
                weight[sym] = weight.get(sym, 0) + 2
            for sym in s:
                weight[sym] = weight.get(sym, 0) + 1
                summary_hits[sym] = summary_hits.get(sym, 0) + 1
            wide = wide or self.is_market_wide(f"{title}. {summary}")
        keep = [sym for sym in weight if sym in in_title or summary_hits.get(sym, 0) >= 2]
        keep.sort(key=lambda sym: (-weight[sym], sym))
        if in_title:
            rel = 3
        elif keep or wide:
            rel = 2
        else:
            rel = 1
        return keep, rel, in_title
