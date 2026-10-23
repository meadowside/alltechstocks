"""Loads config/sources.yaml, config/rules.yaml and config/watchlist.yaml."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote_plus, urlparse

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


@dataclass
class Source:
    id: str
    name: str
    url: str
    tier: int
    weight: float
    sections: list[str] = field(default_factory=list)
    domain: str | None = None
    paywall: bool = False
    standalone: bool = False
    confirm: bool = False
    always_tech: bool = False
    google: bool = False
    ticker: str | None = None      # a company's own newsroom
    auto: bool = False             # built from the watchlist, not listed in sources.yaml

    @property
    def is_backfill(self) -> bool:
        return self.tier == 0

    @property
    def is_primary(self) -> bool:
        return self.tier == 4


@dataclass
class Stock:
    symbol: str
    exchange: str
    name: str
    group: str = ""
    aliases: list[str] = field(default_factory=list)
    exact: list[str] = field(default_factory=list)
    results: str | None = None     # next results date, YYYY-MM-DD, if known
    ignore: list[str] = field(default_factory=list)  # phrases that look like the name but aren't ("per capita")

    @property
    def market(self) -> str:
        return "UK" if self.exchange == "LSE" else "US"

    @property
    def slug(self) -> str:
        """File name for the stock's page. UK tickers get -l so they never clash with a US one."""
        s = re.sub(r"[^a-z0-9]+", "-", self.symbol.lower()).strip("-")
        return f"{s}-l" if self.market == "UK" else s

    @property
    def tv_symbol(self) -> str:
        """TradingView symbol, e.g. NASDAQ:AAPL or LSE:SGE."""
        return f"{self.exchange}:{self.symbol}"

    @property
    def google_finance(self) -> str:
        exch = "LON" if self.exchange == "LSE" else self.exchange
        return f"https://www.google.com/finance/quote/{self.symbol}:{exch}"

    @property
    def search_names(self) -> list[str]:
        """Names used in Google News queries: the display name, plus multi-word aliases."""
        out = [self.name]
        for a in self.aliases:
            if a.lower() != self.name.lower() and (" " in a or len(a) > 5) and a not in out:
                out.append(a)
        return out[:2]


def _host(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _google_url(terms: list[str], when: str, uk: bool) -> str:
    q = " OR ".join(f'"{t}"' for t in terms) + f" when:{when}"
    loc = "hl=en-GB&gl=GB&ceid=GB:en" if uk else "hl=en-US&gl=US&ceid=US:en"
    return f"https://news.google.com/rss/search?q={quote_plus(q)}&{loc}"


class Config:
    def __init__(self, config_dir: Path = CONFIG_DIR):
        with open(config_dir / "sources.yaml", encoding="utf-8") as f:
            raw_sources = yaml.safe_load(f)["sources"]
        with open(config_dir / "rules.yaml", encoding="utf-8") as f:
            self.rules = yaml.safe_load(f)
        with open(config_dir / "watchlist.yaml", encoding="utf-8") as f:
            raw_stocks = yaml.safe_load(f)["stocks"]

        for s in raw_stocks:
            if not isinstance(s.get("symbol"), str):
                raise ValueError(f"watchlist.yaml: put quotes round the ticker for {s.get('name')} (YAML read it as {s.get('symbol')!r})")
        self.stocks = [Stock(**{k: (str(v) if k == "results" and v else v) for k, v in s.items()}) for s in raw_stocks]
        self.by_symbol = {s.symbol: s for s in self.stocks}
        self.by_slug = {s.slug: s for s in self.stocks}
        if len(self.by_slug) != len(self.stocks):
            raise ValueError("Two stocks in watchlist.yaml share a symbol. Each must be unique.")

        self.sources = [Source(**s) for s in raw_sources] + self._auto_sources()
        self.by_id = {s.id: s for s in self.sources}
        self.sections = self.rules["sections"]
        self.section_ids = [s["id"] for s in self.sections]
        self.section_names = {s["id"]: s["name"] for s in self.sections}
        self.site = self.rules["site"]
        self.scoring = self.rules["scoring"]
        self.clustering = self.rules["clustering"]

    def _auto_sources(self) -> list[Source]:
        """One Google News query per few watched companies, US and UK searched separately."""
        ag = self.rules.get("auto_google", {})
        n = int(ag.get("per_query", 6))
        when = ag.get("when", "1d")
        out = []
        for market in ("US", "UK"):
            names = [s.search_names[0] for s in self.stocks if s.market == market]
            for i in range(0, len(names), n):
                batch = names[i:i + n]
                out.append(Source(
                    id=f"gn-{market.lower()}-{i // n + 1}", name="Google News",
                    url=_google_url(batch, when, market == "UK"),
                    tier=0, weight=1, google=True, auto=True,
                ))
        return out

    def source_for_url(self, url: str) -> Source | None:
        """Match an article URL (or a Google News <source> URL) to a known, non-backfill outlet."""
        if not url:
            return None
        host = _host(url)
        best = None
        for s in self.sources:
            if not s.domain or s.is_backfill:
                continue
            d = s.domain.lower()
            if host == d or host.endswith("." + d):
                if best is None or len(d) > len(best.domain):
                    best = s
        return best
