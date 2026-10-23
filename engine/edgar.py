"""US filings from SEC EDGAR: results, deals, insider trades and other official filings.

EDGAR is free public data. The SEC's fair access rules ask for:
  - a User-Agent naming who you are with a contact email (set sec.user_agent in rules.yaml)
  - no more than 10 requests a second (we pause between requests)
EDGAR stays off until sec.user_agent is changed from the example.

One Atom feed per company, about 75 requests an hour. Each filing is stored once.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser

from .config import ROOT, Config
from .db import DB, iso

COMPANY_FEED = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}"
                "&type=&dateb=&owner=include&count=40&output=atom")
TICKER_MAP = "https://www.sec.gov/files/company_tickers.json"
PLACEHOLDER = "you@example.com"

# 8-K item numbers, most important first. The first match names the filing.
ITEMS_8K = [
    ("2.02", "Results", True),
    ("1.01", "Major agreement", True),
    ("2.01", "Deal completed", True),
    ("1.05", "Cyber incident", True),
    ("4.02", "Restated accounts", True),
    ("2.05", "Restructuring costs", True),
    ("2.06", "Write-down", True),
    ("3.01", "Listing notice", True),
    ("5.02", "Board or executive change", True),
    ("1.02", "Agreement ended", False),
    ("5.07", "Shareholder vote results", False),
    ("7.01", "Investor update", False),
    ("8.01", "Other event", False),
]
FORMS = {
    "10-Q": ("Quarterly report", True),
    "10-K": ("Annual report", True),
    "20-F": ("Annual report (foreign issuer)", True),
    "6-K": ("Report (foreign issuer)", False),
    "4": ("Insider trade", False),
    "SC 13D": ("Major shareholder stake", True),
    "SCHEDULE 13D": ("Major shareholder stake", True),
    "SC 13G": ("Shareholder stake", False),
    "SCHEDULE 13G": ("Shareholder stake", False),
    "S-1": ("Share offering", True),
    "S-4": ("Merger registration", True),
    "DEF 14A": ("Proxy statement", False),
}
ITEM_RE = re.compile(r"Item\s+(\d\.\d\d)", re.IGNORECASE)
ACC_RE = re.compile(r"accession-number=([\d-]+)")


def enabled(cfg: Config) -> bool:
    ua = cfg.rules.get("sec", {}).get("user_agent", "")
    return bool(ua) and PLACEHOLDER not in ua and "@" in ua


def describe(form: str, summary: str) -> tuple[str, str, bool]:
    """(label, items, notable) for a filing."""
    form = form.strip().upper()
    if form in ("8-K", "8-K/A"):
        items = sorted(set(ITEM_RE.findall(summary or "")))
        for num, label, notable in ITEMS_8K:
            if num in items:
                return (label + (" (amended)" if form.endswith("/A") else ""), ",".join(items), notable)
        return ("Current report", ",".join(items), False)
    base = form[:-2] if form.endswith("/A") else form
    label, notable = FORMS.get(base, (form, False))
    if form.endswith("/A"):
        label += " (amended)"
        notable = False
    return label, "", notable


def _cik_map(cfg: Config, ua: str, now: datetime) -> dict[str, str]:
    """Ticker to CIK, from the SEC's own list. Cached in data/ for a week."""
    path = ROOT / "data" / "sec_tickers.json"
    fresh = path.exists() and (now.timestamp() - path.stat().st_mtime) < 7 * 86400
    if not fresh:
        try:
            req = urllib.request.Request(TICKER_MAP, headers={"User-Agent": ua, "Accept-Encoding": "identity"})
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = json.load(r)
            mapping = {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in raw.values()}
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(mapping, sort_keys=True), encoding="utf-8")
        except Exception as e:
            print(f"  EDGAR ticker list failed: {e}")
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def fetch_filings(cfg: Config, db: DB, now: datetime, fixtures: Path | None = None) -> dict:
    sec = cfg.rules.get("sec", {})
    stats = {"companies": 0, "ok": 0, "new": 0}
    if fixtures is None and not enabled(cfg):
        db.record_health("sec-edgar", now, False, 0, "Off: set sec.user_agent in rules.yaml to your name and email")
        return stats
    ua = sec["user_agent"]
    wanted = {f.upper() for f in sec.get("forms", [])}
    keep_days = int(sec.get("keep_days", 60))
    oldest = now - timedelta(days=keep_days)
    ciks = {} if fixtures is not None else _cik_map(cfg, ua, now)
    pause = float(sec.get("pause_seconds", 0.25))
    failures = []

    for stock in cfg.stocks:
        if stock.market != "US":
            continue
        stats["companies"] += 1
        if fixtures is not None:
            fx = fixtures / "edgar" / f"{stock.symbol}.xml"
            if not fx.exists():
                continue
            target = str(fx)
        else:
            # The SEC's list uses BRK-B style; EDGAR also accepts a plain ticker, so fall back to that.
            target = COMPANY_FEED.format(cik=ciks.get(stock.symbol.replace(".", "-"), stock.symbol))
            time.sleep(pause)
        try:
            parsed = feedparser.parse(target, agent=ua)
            status = parsed.get("status")
            if status and status >= 400:
                raise RuntimeError(f"HTTP {status}")
            if parsed.get("bozo") and not parsed.entries:
                raise RuntimeError(str(parsed.get("bozo_exception") or "unreadable feed"))
        except Exception as e:
            failures.append(f"{stock.symbol}: {e}")
            continue
        stats["ok"] += 1
        for e in parsed.entries:
            form = (e.get("tags") or [{}])[0].get("term") or e.get("title", "").split(" ")[0]
            form = (form or "").strip().upper()
            if form.removesuffix("/A") not in wanted:
                continue
            t = e.get("updated_parsed") or e.get("published_parsed")
            if not t:
                continue
            filed = datetime(*t[:6], tzinfo=timezone.utc)
            if filed < oldest:
                continue
            m = ACC_RE.search(e.get("id", ""))
            accession = m.group(1) if m else (e.get("link") or "")
            summary = e.get("summary", "")
            label, items, notable = describe(form, summary)
            if db.add_filing({
                "accession": accession, "symbol": stock.symbol, "form": form, "label": label, "items": items,
                "filed": iso(filed), "url": e.get("link") or "", "notable": int(notable), "fetched": iso(now),
            }):
                stats["new"] += 1

    db.prune_filings(now, keep_days)
    err = "; ".join(failures)[:300] if failures else None
    db.record_health("sec-edgar", now, stats["ok"] > 0, stats["ok"], err)
    return stats
