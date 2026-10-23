"""Writes sample RSS feeds and SEC filings for testing the engine without the internet.

Every EVENT here is MADE UP. The company names are real so the watchlist tagging can be tested,
but none of these results, deals, filings or quotes happened. These files exist only to check that
tagging, clustering, scoring, exclusions, filings and the email work as designed. They are never
published: the test build goes to site-test/, which is not deployed.

  python tests/make_fixtures.py --now 2026-10-23T05:31Z --out tests/fixtures
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

GN = "https://news.google.com/rss/articles/"

# (feed id, minutes ago, title, summary, url, google <source> (name, url) or None)
STORIES = [
    # 1. Big results story, many outlets -> should be the top story, NVDA top stock
    ("cnbc-tech", 300, "TEST Nvidia revenue tops estimates as data centre sales double",
     "Nvidia reported third-quarter revenue above Wall Street estimates, driven by data centre chips.",
     "https://www.cnbc.com/2026/10/22/test-nvidia-q3.html", None),
    ("reuters", 290, "TEST Nvidia quarterly revenue beats forecasts on data centre demand - Reuters",
     "Nvidia results Reuters", GN + "NV1", ("Reuters", "https://www.reuters.com")),
    ("bloomberg", 280, "TEST Nvidia sales jump as cloud giants keep buying chips - Bloomberg",
     "Nvidia Bloomberg", GN + "NV2", ("Bloomberg", "https://www.bloomberg.com")),
    ("ft", 270, "TEST Nvidia results show data centre revenue doubling",
     "The chipmaker said Microsoft and Amazon were among its biggest customers in the quarter.",
     "https://www.ft.com/content/test-nvidia", None),
    ("marketwatch", 260, "TEST Nvidia earnings: data centre revenue doubles - MarketWatch",
     "Nvidia MarketWatch", GN + "NV3", ("MarketWatch", "https://www.marketwatch.com")),
    ("the-register", 250, "TEST Nvidia data centre revenue doubles in Q3",
     "Nvidia's data centre business brought in twice as much as a year ago, the company said.",
     "https://www.theregister.com/2026/10/22/test_nvidia_q3/", None),
    ("gn-us-1", 240, "TEST Nvidia Q3 results: data centre sales double - The Wall Street Journal",
     "Nvidia WSJ", GN + "NV4", ("The Wall Street Journal", "https://www.wsj.com")),
    ("nvidia-newsroom", 320, "TEST NVIDIA Announces Financial Results for Third Quarter",
     "NVIDIA today reported revenue for the third quarter, with record data center revenue.",
     "https://nvidianews.nvidia.com/news/test-q3", None),

    # 2. UK results story via RNS + UK outlets -> UK page, SGE
    ("lse-rns", 400, "TEST Sage Group plc: Full year results - London Stock Exchange",
     "Sage RNS", GN + "SGE1", ("London Stock Exchange", "https://www.londonstockexchange.com")),
    ("cityam", 380, "TEST Sage Group results: subscription revenue rises 11 per cent",
     "Sage Group said subscription revenue rose as more small firms moved to its cloud software.",
     "https://www.cityam.com/test-sage-results/", None),
    ("this-is-money", 370, "TEST Sage Group full year results lift cloud subscriptions",
     "The Newcastle-based software firm reported higher full year subscription revenue.",
     "https://www.thisismoney.co.uk/money/markets/test-sage.html", None),
    ("guardian-business", 360, "TEST Sage Group reports rise in full year subscription revenue",
     "Sage Group, the accounting software company, reported a rise in full year results.",
     "https://www.theguardian.com/business/2026/oct/23/test-sage", None),
    ("proactive", 350, "TEST Sage Group full year results show subscription growth - Proactive Investors",
     "Sage Proactive", GN + "SGE2", ("Proactive Investors", "https://www.proactiveinvestors.co.uk")),

    # 3. Regulation: Microsoft and the CMA
    ("bbc-tech", 500, "TEST CMA opens investigation into Microsoft cloud licensing",
     "The Competition and Markets Authority said Microsoft's licensing may make it harder to switch cloud provider.",
     "https://www.bbc.co.uk/news/articles/test-cma-microsoft", None),
    ("guardian-tech", 480, "TEST Competition watchdog investigates Microsoft cloud licensing",
     "The CMA investigation will look at how Microsoft licenses software on rival clouds.",
     "https://www.theguardian.com/technology/2026/oct/22/test-cma-microsoft", None),
    ("reuters", 470, "TEST UK regulator opens Microsoft cloud licensing investigation - Reuters",
     "CMA Microsoft Reuters", GN + "MS1", ("Reuters", "https://www.reuters.com")),

    # 4. Company newsroom only -> held back (official source, no outlet yet)
    ("arm-newsroom", 120, "TEST Arm announces new automotive compute platform - Arm Newsroom",
     "Arm newsroom", GN + "ARM1", ("Arm", "https://newsroom.arm.com")),

    # 5. Tip listicles and price predictions -> excluded by rules
    ("gn-us-2", 200, "TEST 3 tech stocks to buy now before earnings - The Motley Fool",
     "Motley Fool", GN + "TIP1", ("The Motley Fool", "https://www.fool.com")),
    ("gn-us-2", 210, "TEST Analyst raises Nvidia price target to $300 - Investing.com",
     "Price target", GN + "TIP2", ("Investing.com", "https://www.investing.com")),
    ("gn-us-3", 220, "TEST Is Apple stock a buy now? - Zacks",
     "Zacks", GN + "TIP3", ("Zacks", "https://www.zacks.com")),

    # 6. Market-wide story, no single stock in the headlines -> runs with a Market tag
    ("reuters", 150, "TEST Tech stocks slide as Nasdaq falls for third day - Reuters",
     "Wall Street", GN + "MKT1", ("Reuters", "https://www.reuters.com")),
    ("cnbc-tech", 140, "TEST Tech stocks fall as the Nasdaq slides for a third session",
     "The Nasdaq composite fell as chip stocks and software names declined.",
     "https://www.cnbc.com/2026/10/23/test-nasdaq.html", None),
    ("bloomberg", 145, "TEST Tech shares slide for third day as Nasdaq drops - Bloomberg",
     "Nasdaq Bloomberg", GN + "MKT2", ("Bloomberg", "https://www.bloomberg.com")),
    ("ap", 135, "TEST Nasdaq falls again as tech stocks slide - AP News",
     "Nasdaq AP", GN + "MKT3", ("AP News", "https://apnews.com")),

    # 7. Deal
    ("wsj-tech", 600, "TEST Salesforce agrees to buy data startup Lakeview for $2 billion",
     "Salesforce said the acquisition would add data tools to its AI products.",
     "https://www.wsj.com/tech/test-salesforce-lakeview", None),
    ("reuters", 590, "TEST Salesforce to acquire Lakeview in $2 billion deal - Reuters",
     "Salesforce Reuters", GN + "CRM1", ("Reuters", "https://www.reuters.com")),
    ("techcrunch", 580, "TEST Salesforce is buying Lakeview for $2B",
     "Salesforce is acquiring Lakeview, a data startup, in a deal worth about $2 billion.",
     "https://techcrunch.com/2026/10/22/test-salesforce-lakeview/", None),

    # 8. Chips
    ("bloomberg", 700, "TEST TSMC September sales rise on AI chip demand - Bloomberg",
     "TSMC Bloomberg", GN + "TSM1", ("Bloomberg", "https://www.bloomberg.com")),
    ("toms-hardware", 690, "TEST TSMC sales rise as AI chip demand stays high",
     "TSMC reported higher September sales as AI chip demand continued.",
     "https://www.tomshardware.com/tech-industry/test-tsmc-sales", None),

    # 9. Single outlet UK story
    ("the-register", 90, "TEST Raspberry Pi opens new Cambridge design centre",
     "Raspberry Pi Holdings is opening a design centre in Cambridge.",
     "https://www.theregister.com/2026/10/23/test_raspberry_pi/", None),

    # 10. Not tech, no stock -> dropped
    ("bbc-business", 100, "TEST Oil prices rise after supply talks stall",
     "Brent crude rose after talks between producers ended without agreement.",
     "https://www.bbc.co.uk/news/articles/test-oil", None),

    # 11. AI section, two stocks
    ("the-verge", 360, "TEST Microsoft and OpenAI sign new cloud agreement",
     "Microsoft will keep supplying Azure cloud capacity to OpenAI under the new agreement.",
     "https://www.theverge.com/test-microsoft-openai", None),
    ("cnbc-tech", 350, "TEST Microsoft signs new AI cloud agreement with OpenAI",
     "The agreement covers Azure capacity for OpenAI's models.",
     "https://www.cnbc.com/2026/10/22/test-microsoft-openai.html", None),
]

RSS_HEAD = '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel><title>{0}</title><link>https://example.com</link><description>Test feed</description>\n'

# (ticker, minutes ago, form, summary) for EDGAR company feeds
FILINGS = [
    ("NVDA", 310, "8-K", "<b>Filed:</b> 2026-10-22 <b>AccNo:</b> 0001045810-26-000101 <b>Size:</b> 1 MB<br>Item 2.02: Results of Operations and Financial Condition<br>Item 9.01: Financial Statements and Exhibits"),
    ("NVDA", 900, "4", "<b>Filed:</b> 2026-10-22 <b>AccNo:</b> 0001045810-26-000099 <b>Size:</b> 5 KB"),
    ("MSFT", 1200, "10-Q", "<b>Filed:</b> 2026-10-22 <b>AccNo:</b> 0000789019-26-000050 <b>Size:</b> 9 MB"),
    ("CRM", 610, "8-K", "<b>Filed:</b> 2026-10-22 <b>AccNo:</b> 0001108524-26-000077 <b>Size:</b> 300 KB<br>Item 1.01: Entry into a Material Definitive Agreement<br>Item 7.01: Regulation FD Disclosure"),
]
ATOM_HEAD = '<?xml version="1.0" encoding="UTF-8"?>\n<feed xmlns="http://www.w3.org/2005/Atom"><title>{0} filings (TEST)</title>\n'


def write(now: datetime, out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    feeds = defaultdict(list)
    for feed, mins, title, summary, url, gsrc in STORIES:
        when = format_datetime(now - timedelta(minutes=mins))
        src = f'<source url="{escape(gsrc[1])}">{escape(gsrc[0])}</source>' if gsrc else ""
        feeds[feed].append(
            f"<item><title>{escape(title)}</title><link>{escape(url)}</link><guid>{escape(url)}</guid>"
            f"<pubDate>{when}</pubDate><description>{escape(summary)}</description>{src}</item>"
        )
    for feed, items in feeds.items():
        (out / f"{feed}.xml").write_text(RSS_HEAD.format(feed) + "\n".join(items) + "\n</channel></rss>\n",
                                         encoding="utf-8")
    edgar = out / "edgar"
    edgar.mkdir(exist_ok=True)
    by_ticker = defaultdict(list)
    for t, mins, form, summary in FILINGS:
        acc = summary.split("AccNo:</b> ")[1].split(" ")[0]
        when = (now - timedelta(minutes=mins)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        by_ticker[t].append(
            f'<entry><title>{form} - TEST filing</title>'
            f'<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/test/{acc}-index.htm"/>'
            f'<summary type="html">{escape(summary)}</summary><updated>{when}</updated>'
            f'<category scheme="https://www.sec.gov/" label="form type" term="{form}"/>'
            f'<id>urn:tag:sec.gov,2008:accession-number={acc}</id></entry>')
    for t, entries in by_ticker.items():
        (edgar / f"{t}.xml").write_text(ATOM_HEAD.format(t) + "\n".join(entries) + "\n</feed>\n", encoding="utf-8")
    return len(feeds)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--now", required=True)
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "fixtures")
    a = ap.parse_args()
    now = datetime.strptime(a.now, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
    print(f"wrote {write(now, a.out)} feeds and {len(FILINGS)} filings")
