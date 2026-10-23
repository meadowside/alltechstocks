# All Tech Stocks

US and UK tech stock news, ranked by who's covering it. A research source, never advice.

Built on the All UK Tech engine. It runs every hour on GitHub Actions, reads about 30 news feeds plus a
Google News search for every stock on the watchlist, groups articles about the same event into stories,
tags each story with the stocks it names and ranks stories and stocks by how many outlets cover them.
It also pulls US filings from SEC EDGAR. On weekday mornings it builds the email for Substack.
No AI is needed to run it. An optional cheap model writes the "So what" lines.

## How it works

```
feeds (config/sources.yaml) + per-stock Google News queries (built from config/watchlist.yaml)
  -> fetch + rules (tips, price targets and junk excluded)         engine/fetch.py, engine/rules.py
  -> tag stocks: names, aliases, $TICKER, (NASDAQ: XYZ)            engine/tickers.py
  -> SEC EDGAR filings for US stocks                               engine/edgar.py
  -> group into stories, score, rank stocks by coverage            engine/stories.py
  -> site: home, US, UK, 6 sections, All stocks, a page per stock  engine/publish.py, templates/
  -> 6am UK on weekdays: email to paste into Substack, plus cover image
```

Story score = min(coverage, 15) x stock relevance x freshness (halves every 12 hours) + 0.5 if an official
source is in the story. Coverage adds each outlet once, at its weight (major outlets 3, markets press 2,
tech press 1). A story runs only if it names a watched stock, or it's a market-wide story ("tech stocks
slide") with coverage of 6 or more.

Stock score = the sum of the scores of its stories in the last 24 hours, full weight when it's in a
headline and half when it's only in the text. That's attention, not merit, and every page says so.

## The rules this site keeps

- No buy or sell suggestions, no predictions, no top picks. Headlines with "stocks to buy", "price
  target", "is it a buy" and similar never appear, and Motley Fool, Seeking Alpha and Zacks are blocked
  (`exclude` in `config/rules.yaml`). Remove a phrase there if it blocks real news.
- The AI lines are told never to mention the shares, and every line is checked against a list of
  advice and prediction words (`ADVICE_RE` in `engine/llm.py`). A line that fails is thrown away.
- The disclaimer (`disclaimer` in `config/rules.yaml`) is on every page, in the email and on the cover.
- Price charts are TradingView's free widgets. Nothing from TradingView or Google Analytics loads
  until the reader accepts cookies or clicks Show price chart. No scraping anywhere.

## Prices: read before you take money

TradingView's widgets are offered for public websites and are free, but their general terms say
commercial use needs a separate agreement and the attribution must stay as designed. Running the
widgets on a free site with their credit intact is the sensible launch position. **Before the site
carries sponsors or a paid tier, email TradingView (the contact form on their widget pages) and get
written OK, or buy a proper display licence.** To switch the widgets off everywhere in one line, set
`prices.widgets: false` in `config/rules.yaml`. Every stock page still links to Google Finance.

Stock data in the widgets is delayed. Some exchanges may not be available. Check a few UK stock pages
after launch: if LSE charts don't load, the Google Finance link still works.

## Setup (about 40 minutes)

1. **Create a GitHub repo** called `alltechstocks` and push this folder to it. Make it public:
   Pages and Actions minutes are free for public repos.
2. **Turn on Pages**: repo Settings > Pages > Source: **GitHub Actions**.
3. **Switch on SEC filings**: in `config/rules.yaml`, change `sec.user_agent` to
   `All Tech Stocks` plus an email inbox you read. The SEC requires this. Until you do, filings stay off.
4. **Run it once**: Actions tab > Hourly run > Run workflow. The site goes live at
   `https://meadowside.github.io/alltechstocks/`.
5. **Check the feeds**: open `/status.html`. Sources marked Failing need a new URL in
   `config/sources.yaml`. Expect a few on day one.
6. **Point the domain**: Settings > Pages > custom domain `alltechstocks.com`. At your registrar add
   four A records for `@` pointing to 185.199.108.153, 185.199.109.153, 185.199.110.153 and
   185.199.111.153, and a CNAME for `www` pointing to `meadowside.github.io`. Tick Enforce HTTPS.
7. **Analytics and contact form**: create a new GA4 property and a new Formspree form, then paste
   their IDs into `ga_id` and `formspree_id` in `config/rules.yaml`.
8. **Newsletter**: create alltechstocks.substack.com (or change `newsletter_url`).

From then on it runs by itself every hour at :23.

## Optional extras (repo Settings > Secrets and variables > Actions)

| Secret | What it turns on |
| --- | --- |
| `ANTHROPIC_API_KEY` | AI "So what" lines (Haiku) under the top stories and in the email. Each story is written once and saved. Capped at 25 new lines an hour and 150 a day. |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | A message to your phone when the email is ready. |

## Your morning

1. At about 6:35 double-click **Get today's email** in the `desktop` folder (copy it to your Desktop
   folder for All Tech Stocks). It downloads the Markdown and the cover image.
2. Paste into a new Substack post, replace the bracketed bits and send. Report, don't tip.

## The watchlist

`config/watchlist.yaml` has 103 stocks: 73 US, 30 UK. Add or remove any. Each needs a symbol,
exchange (NASDAQ, NYSE or LSE), name and the aliases that should tag a story. Names that are also
everyday words ("Arm", "Meta", "Oracle") go under `exact` so only the capitalised form matches.
Put quotes round tickers YAML reads as words, like `"ON"`. Add `results: 2026-11-19` to show a
company's next results date on its page.

Some UK names may have been bought or delisted since this list was made. After a week the status page
lists every stock with no stories, which is the quickest way to spot them.

## Tuning after the first week

All in `config/rules.yaml`:

- **Same story listed twice?** Lower `min_link_similarity` and `merge_similarity` a little.
- **Two different stories merged?** Raise them.
- **Wrong section?** Add words to `section_keywords`.
- **A stock tagged wrongly?** Tighten its aliases in `config/watchlist.yaml`.
- **Tips or predictions getting through?** Add the phrase to `exclude.title_keywords`.
- **Google News errors on the status page?** Raise `auto_google.rotate` to 3 so each query runs every
  third hour.

## Running it on your own machine

```
pip install -r requirements.txt
python run.py hourly                 # fetch and build the site into ./site
python run.py email                  # build today's email now
```

Test without the internet, using made-up events:

```
python tests/make_fixtures.py --now 2026-10-23T05:31Z
python run.py auto --fixtures tests/fixtures --now 2026-10-23T05:31Z --db data/test/news.db --out site-test
```
