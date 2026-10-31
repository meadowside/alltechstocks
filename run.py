"""All Tech Stocks engine.

  python run.py auto        # what the hourly GitHub Action runs: fetch news and filings, rank,
                            # rebuild the site, and build the email in the 6am hour on weekdays
  python run.py hourly      # fetch, rank and rebuild the site only
  python run.py email       # build today's email now (even if one exists)
  python run.py trends      # print coverage spikes and section shares from saved history (no fetching)
  python run.py weekly      # build this week's draft now from saved history (no fetching)

Options:
  --fixtures DIR            # read feeds from DIR/<source id>.xml (and DIR/edgar/<TICKER>.xml) instead of the internet
  --now 2026-10-22T05:31Z   # pretend it's this time (UTC), for testing
  --db PATH --out PATH      # where the database and the built site go
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from engine import edgar, llm, notify, trends, weekly
from engine.config import ROOT, Config
from engine.db import DB
from engine.fetch import fetch_all
from engine.publish import build_email, build_site
from engine.rules import Rules
from engine.stories import build_stories


def main():
    ap = argparse.ArgumentParser(description="All Tech Stocks engine")
    ap.add_argument("command", choices=["auto", "hourly", "email", "trends", "weekly"])
    ap.add_argument("--fixtures", type=Path)
    ap.add_argument("--now")
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "news.db")
    ap.add_argument("--out", type=Path, default=ROOT / "site")
    ap.add_argument("--date", help="for trends: the UK date to report on (default yesterday)")
    args = ap.parse_args()

    now = (datetime.strptime(args.now, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
           if args.now else datetime.now(timezone.utc).replace(microsecond=0))
    cfg = Config()
    rules = Rules(cfg)
    db = DB(args.db)
    local = now.astimezone(ZoneInfo(cfg.site["timezone"]))
    print(f"All Tech Stocks run: {args.command} at {local:%a %d %b %H:%M} UK time")

    if args.command == "trends":
        trends.record_daily(db, cfg, now)
        print(trends.report(db, cfg, now, args.date))
        db.close()
        return

    if args.command == "weekly":
        try:
            trends.record_daily(db, cfg, now)
            _build_weekly(db, cfg, rules, now, args.db.parent / "weekly")
        finally:
            db.close()
        return

    try:
        stats = fetch_all(cfg, db, rules, now, fixtures=args.fixtures)
        print(f"  feeds: {stats['ok']}/{stats['sources']} worked, {stats['new']} new items "
              f"({stats['excluded']} excluded by rules, {stats['rested']} Google queries resting this hour)")
        fs = edgar.fetch_filings(cfg, db, now, fixtures=args.fixtures)
        if fs["companies"]:
            print(f"  filings: {fs['ok']}/{fs['companies']} companies checked, {fs['new']} new filings")
        else:
            print("  filings: SEC EDGAR is off (add the SEC_USER_AGENT secret in GitHub)")
        db.prune(now)
        stories = build_stories(db, cfg, rules, now)
        shown = sum(1 for s in stories if s.eligible)
        print(f"  stories: {len(stories)} grouped, {shown} name a watched stock or pass the market rules")

        archive = args.db.parent / "emails"
        site = build_site(stories, db, cfg, now, args.out, archive)
        print(f"  site: {site['live']} stories live, {site['new_ai_lines']} new AI lines. "
              f"Top: {site['top']}. Most covered stock: {site['top_stock']}")

        tr = trends.record_daily(db, cfg, now)
        print(f"  trends: {tr['stock_rows']} stock-days and {tr['section_rows']} section-days refreshed, "
              f"{trends.history_days(db)} days of history")

        email_cfg = cfg.rules["email"]
        weekday_ok = local.weekday() < 5 or not email_cfg.get("weekdays_only", True)
        date = local.strftime("%Y-%m-%d")
        if args.command == "email" or (
            args.command == "auto" and weekday_ok and not db.email_built(date)
            and email_cfg["build_hour"] <= local.hour < email_cfg["build_hour"] + email_cfg.get("late_window_hours", 4)
        ):
            res = build_email(stories, db, cfg, now, args.out, archive)
            print(f"  email: {res['stories']} stories, {res['llm_lines']} new AI lines, opening note: {'yes' if res['opening'] else 'no'}"
                  + (f", short sections: {', '.join(res['short_sections'])}" if res["short_sections"] else ""))
            message = (f"All Tech Stocks email for {res['date']} is ready. "
                       "Double-click 'Get today's email' in the All Tech Stocks folder on your Desktop.")
            print(f"  notify: {notify.send(message)}")
        wk = cfg.rules.get("weekly", {})
        weekly_dir = args.db.parent / "weekly"
        if (args.command == "auto" and wk and wk.get("enabled", True) and local.weekday() == wk.get("day", 4)
                and not weekly.built(weekly_dir, date)
                and wk.get("build_hour", 14) <= local.hour < wk.get("build_hour", 14) + wk.get("late_window_hours", 6)):
            _build_weekly(db, cfg, rules, now, weekly_dir)
        if llm.enabled():
            st = llm.STATS
            print(f"  AI lines: {st['ok']} written, {st['rejected']} rejected, {st['failed']} failed calls")
    finally:
        db.close()


def _build_weekly(db, cfg, rules, now, weekly_dir):
    res = weekly.build(db, cfg, rules, now, weekly_dir)
    print(f"  weekly: draft for {res['date']}, {res['big_stories']} big stories, {res['results']} companies reported, "
          f"AI intro: {'yes' if res['ai_intro'] else 'no'}, AI angles: {res['ai_angles']}")
    for line in res["log"]:
        print(f"    {line}")
    message = (f"All Tech Stocks weekly draft for {res['date']} is ready. "
               "Double-click 'Get this week's draft' in the All Tech Stocks folder on your Desktop.")
    print(f"  notify: {notify.send(message)}")


if __name__ == "__main__":
    main()
