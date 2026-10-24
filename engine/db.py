"""SQLite storage. One small file, committed back to the repo after each run."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    guid TEXT UNIQUE NOT NULL,
    source_id TEXT NOT NULL,        -- the feed it came from
    outlet_id TEXT,                 -- the real outlet (differs for Google News items)
    outlet_name TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    summary TEXT,
    published TEXT NOT NULL,        -- ISO 8601, UTC
    fetched TEXT NOT NULL,
    excluded TEXT,                  -- reason, or NULL if kept
    relevance INTEGER,
    section TEXT
);
CREATE INDEX IF NOT EXISTS items_published ON items(published);

CREATE TABLE IF NOT EXISTS sent (
    id INTEGER PRIMARY KEY,
    sent_date TEXT NOT NULL,
    item_ids TEXT NOT NULL,         -- JSON list
    coverage REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS filings (
    accession TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    form TEXT NOT NULL,
    label TEXT NOT NULL,
    items TEXT,                     -- 8-K item numbers, e.g. "2.02,9.01"
    filed TEXT NOT NULL,            -- ISO 8601, UTC
    url TEXT NOT NULL,
    notable INTEGER NOT NULL DEFAULT 0,
    fetched TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS filings_symbol ON filings(symbol, filed);

-- Every story a stock has appeared in, kept for a month so stock pages show more than today.
CREATE TABLE IF NOT EXISTS stock_stories (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    item_ids TEXT NOT NULL,         -- JSON list, used to recognise the same story next run
    headline TEXT NOT NULL,
    link TEXT NOT NULL,
    outlet TEXT NOT NULL,
    paywall INTEGER NOT NULL DEFAULT 0,
    outlets INTEGER NOT NULL,
    in_headline INTEGER NOT NULL,   -- 1 if the stock is named in a headline
    section TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS stock_stories_symbol ON stock_stories(symbol, last_seen);

CREATE TABLE IF NOT EXISTS emails (
    date TEXT PRIMARY KEY,
    built_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS why_lines (
    item_id INTEGER PRIMARY KEY,   -- every item in the story points at the same line
    line TEXT NOT NULL,
    created TEXT NOT NULL
);

-- Trend history. One row per stock per UK day, kept forever (about 100 rows a day, a few MB a year).
-- Rebuilt from stock_stories for the last 29 days on every run, then frozen. engine/trends.py
CREATE TABLE IF NOT EXISTS stock_daily (
    date TEXT NOT NULL,             -- UK date, YYYY-MM-DD, of the story's first sighting
    symbol TEXT NOT NULL,
    stories INTEGER NOT NULL,       -- stories naming the stock
    headline_stories INTEGER NOT NULL,
    outlets INTEGER NOT NULL,       -- outlets summed across those stories
    attention REAL NOT NULL,        -- outlets, full weight in a headline, half in the text only
    PRIMARY KEY (date, symbol)
);

-- Kept articles per section per UK day, kept forever. Rebuilt for the last 13 days on every run.
CREATE TABLE IF NOT EXISTS section_daily (
    date TEXT NOT NULL,
    section TEXT NOT NULL,
    items INTEGER NOT NULL,
    PRIMARY KEY (date, section)
);

CREATE TABLE IF NOT EXISTS source_health (
    source_id TEXT PRIMARY KEY,
    last_attempt TEXT,
    last_ok TEXT,
    last_error TEXT,
    last_count INTEGER
);
"""


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self):
        self.conn.commit()
        self.conn.close()

    # ----- items -----
    def add_item(self, row: dict) -> bool:
        cur = self.conn.execute(
            """INSERT OR IGNORE INTO items
               (guid, source_id, outlet_id, outlet_name, title, url, summary, published, fetched,
                excluded, relevance, section)
               VALUES (:guid, :source_id, :outlet_id, :outlet_name, :title, :url, :summary, :published,
                       :fetched, :excluded, :relevance, :section)""",
            row,
        )
        return cur.rowcount == 1

    def items_since(self, since: datetime, include_excluded: bool = False) -> list[sqlite3.Row]:
        q = "SELECT * FROM items WHERE published >= ?"
        if not include_excluded:
            q += " AND excluded IS NULL"
        return self.conn.execute(q + " ORDER BY published", (iso(since),)).fetchall()

    def prune(self, now: datetime, keep_days: int = 14):
        cutoff = iso(now - timedelta(days=keep_days))
        self.conn.execute("DELETE FROM items WHERE published < ?", (cutoff,))
        self.conn.execute("DELETE FROM sent WHERE sent_date < ?", (cutoff[:10],))
        self.conn.execute("DELETE FROM stock_stories WHERE last_seen < ?", (iso(now - timedelta(days=30)),))
        self.conn.execute("DELETE FROM why_lines WHERE created < ?", (cutoff,))

    # ----- sent stories -----
    def sent_records(self, except_date: str | None = None) -> list[tuple[set[int], float]]:
        """Stories already emailed. except_date leaves out that day's own email, so a rebuild
        of today's email isn't emptied by today's first build."""
        rows = self.conn.execute("SELECT item_ids, coverage FROM sent WHERE sent_date != ?",
                                 (except_date or "",)).fetchall()
        return [(set(json.loads(r["item_ids"])), r["coverage"]) for r in rows]

    def clear_sent(self, date: str):
        self.conn.execute("DELETE FROM sent WHERE sent_date = ?", (date,))

    def record_sent(self, date: str, item_ids: list[int], coverage: float):
        self.conn.execute(
            "INSERT INTO sent (sent_date, item_ids, coverage) VALUES (?, ?, ?)",
            (date, json.dumps(item_ids), coverage),
        )

    # ----- emails -----
    def email_built(self, date: str) -> bool:
        return self.conn.execute("SELECT 1 FROM emails WHERE date = ?", (date,)).fetchone() is not None

    def record_email(self, date: str, now: datetime):
        self.conn.execute("INSERT OR REPLACE INTO emails (date, built_at) VALUES (?, ?)", (date, iso(now)))

    # ----- filings -----
    def add_filing(self, row: dict) -> bool:
        cur = self.conn.execute(
            """INSERT OR IGNORE INTO filings (accession, symbol, form, label, items, filed, url, notable, fetched)
               VALUES (:accession, :symbol, :form, :label, :items, :filed, :url, :notable, :fetched)""", row)
        return cur.rowcount == 1

    def filings_since(self, since: datetime, symbol: str | None = None, notable_only: bool = False) -> list[sqlite3.Row]:
        q, args = "SELECT * FROM filings WHERE filed >= ?", [iso(since)]
        if symbol:
            q += " AND symbol = ?"
            args.append(symbol)
        if notable_only:
            q += " AND notable = 1"
        return self.conn.execute(q + " ORDER BY filed DESC", args).fetchall()

    def prune_filings(self, now: datetime, keep_days: int):
        self.conn.execute("DELETE FROM filings WHERE filed < ?", (iso(now - timedelta(days=keep_days)),))

    # ----- stock coverage history -----
    def save_stock_story(self, symbol: str, item_ids: list[int], row: dict):
        """Insert or update this stock's record of a story. A story is the same one as last run
        if it shares any article with it."""
        ids = set(item_ids)
        recent = self.conn.execute(
            "SELECT id, item_ids, first_seen FROM stock_stories WHERE symbol = ? AND last_seen >= ?",
            (symbol, iso(parse_iso(row["last_seen"]) - timedelta(days=4)))).fetchall()
        first = row["first_seen"]
        for r in recent:
            if ids & set(json.loads(r["item_ids"])):
                first = min(first, r["first_seen"])
                self.conn.execute("DELETE FROM stock_stories WHERE id = ?", (r["id"],))
        self.conn.execute(
            """INSERT INTO stock_stories (symbol, item_ids, headline, link, outlet, paywall, outlets, in_headline,
                                          section, first_seen, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (symbol, json.dumps(sorted(ids)), row["headline"], row["link"], row["outlet"], int(row["paywall"]),
             row["outlets"], int(row["in_headline"]), row["section"], first, row["last_seen"]))

    def purge_stock_stories(self, keep) -> int:
        """Delete saved stock stories that fail today's rules (rules can change after a story was saved)."""
        rows = self.conn.execute("SELECT id, headline, link, outlet FROM stock_stories").fetchall()
        bad = [r["id"] for r in rows if not keep(r)]
        self.conn.executemany("DELETE FROM stock_stories WHERE id = ?", [(i,) for i in bad])
        return len(bad)

    def stock_stories(self, since: datetime, symbol: str | None = None) -> list[sqlite3.Row]:
        q, args = "SELECT * FROM stock_stories WHERE last_seen >= ?", [iso(since)]
        if symbol:
            q += " AND symbol = ?"
            args.append(symbol)
        return self.conn.execute(q + " ORDER BY last_seen DESC", args).fetchall()

    # ----- AI "so what" lines -----
    def why_line(self, item_ids: list[int], valid_from: str | None = None) -> str | None:
        if not item_ids:
            return None
        q = "SELECT line FROM why_lines WHERE item_id IN (%s)" % ",".join("?" * len(item_ids))
        args = list(item_ids)
        if valid_from:  # lines written before a fix to how they're made are ignored and rewritten
            q += " AND created >= ?"
            args.append(str(valid_from))
        row = self.conn.execute(q + " ORDER BY created DESC LIMIT 1", args).fetchone()
        if not row:
            return None
        return row["line"].strip().split("\n")[0].strip()  # older lines may carry a stray note

    def save_why_line(self, item_ids: list[int], line: str, now: datetime):
        self.conn.executemany(
            "INSERT OR IGNORE INTO why_lines (item_id, line, created) VALUES (?, ?, ?)",
            [(i, line, iso(now)) for i in item_ids],
        )

    def calls_today(self, day_start: datetime) -> int:
        row = self.conn.execute(
            "SELECT COUNT(DISTINCT line) AS n FROM why_lines WHERE created >= ?", (iso(day_start),)
        ).fetchone()
        return row["n"] or 0

    # ----- source health -----
    def record_health(self, source_id: str, now: datetime, ok: bool, count: int, error: str | None):
        prev = self.conn.execute("SELECT last_ok FROM source_health WHERE source_id = ?", (source_id,)).fetchone()
        last_ok = iso(now) if ok else (prev["last_ok"] if prev else None)
        self.conn.execute(
            """INSERT OR REPLACE INTO source_health (source_id, last_attempt, last_ok, last_error, last_count)
               VALUES (?, ?, ?, ?, ?)""",
            (source_id, iso(now), last_ok, None if ok else error, count),
        )

    def health(self) -> dict[str, sqlite3.Row]:
        return {r["source_id"]: r for r in self.conn.execute("SELECT * FROM source_health").fetchall()}
