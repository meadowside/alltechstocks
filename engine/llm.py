"""Optional: one-sentence "so what" lines for stories on the site and in the email.

Runs only when ANTHROPIC_API_KEY is set. Each story gets one call, ever: the line is saved
in the database and reused on every later build. A failed call just means no line.

Every line is checked before it's used. Anything that reads as advice, a view on the shares or a
prediction is thrown away, so the worst case is a story with no line.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-haiku-5-5"
BANNED = ["bombshell", "shocking", "stunning", "game-changer", "game changer", "slams", "epic", "insane",
          "delve", "landscape", "crucial", "pivotal", "tapestry", "—", "–", ";"]

# Advice, opinions on the shares and predictions. Whole words, any case. A line containing any of
# these is thrown away. Better no line than one that reads like a tip.
ADVICE_RE = re.compile(r"\b(" + "|".join([
    r"buy", r"buys", r"buying", r"sell", r"sells", r"selling", r"hold", r"holding",
    r"bullish", r"bearish", r"bull case", r"bear case", r"upside", r"downside",
    r"undervalued", r"overvalued", r"cheap", r"expensive", r"bargain", r"valuation",
    r"price target", r"target price", r"rally", r"rallies", r"soar", r"soars", r"surge", r"surges", r"plunge", r"plunges",
    r"rebound", r"breakout", r"momentum", r"outperform\w*", r"underperform\w*",
    r"investors should", r"shareholders should", r"you should", r"worth (?:a look|owning|watching)",
    r"opportunity", r"opportunities", r"entry point", r"good time", r"bad time", r"recommend\w*",
    r"will (?:rise|fall|climb|drop|gain|lose|jump|slide|recover)", r"could (?:rise|fall|climb|drop|gain|jump|slide|recover|soar)",
    r"likely to (?:rise|fall|climb|drop|gain|jump|slide|recover)", r"set to (?:rise|fall|climb|drop|gain|jump|slide)",
    r"share price", r"shares (?:rise|rose|fall|fell|jump|jumped|slide|slid|climb|climbed|drop|dropped)",
    r"stock price", r"returns", r"portfolio", r"invest", r"investing", r"investment case",
])+r")\b", re.IGNORECASE)

PROMPT = """You write one line for All Tech Stocks, a news index of US and UK listed tech companies.
Readers use it for research. It is not an advice service and must never read like one.

Story headline: {headline}
Companies tagged: {companies}
What the coverage says:
{snippets}

Write ONE sentence, 25 words or fewer, saying what this means for the company's business: its customers,
products, costs, rivals, rules or plans. Plain British English. No hype.
Strict rules:
- Never say or hint whether to buy, sell or hold anything.
- Never mention the share price, valuation, returns or what the shares might do.
- Never predict. Describe what has happened or what the company has said, and nothing more.
- No dashes, no semicolons. Don't repeat the headline. Don't start with "This".
- Never mention the coverage, the sources or the article. Talk about the story.
Reply with the sentence only."""


def enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def why_it_matters(headline: str, snippets: list[str], companies: str = "") -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    body = {
        "model": os.environ.get("LLM_MODEL", DEFAULT_MODEL),
        "max_tokens": 200,
        # Haiku 5.5 thinks by default and the thinking counts against max_tokens, which left
        # replies empty. One plain sentence needs no thinking.
        "thinking": {"type": "disabled"},
        "output_config": {"effort": "low"},
        "messages": [{
            "role": "user",
            "content": PROMPT.format(headline=headline, companies=companies or "none",
                                     snippets="\n".join(f"- {s}" for s in snippets[:5] if s)),
        }],
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode(),
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
        raw = "".join(b.get("text", "") for b in data.get("content", []))
    except Exception as e:
        STATS["failed"] += 1
        print(f"  AI call failed: {e}")
        return None
    text = clean_line(raw)
    reason = reject_reason(text)
    if reason == "empty":
        reason = f"empty, stop_reason={data.get('stop_reason')}, blocks={[b.get('type') for b in data.get('content', [])]}"
    if reason:
        STATS["rejected"] += 1
        print(f"  AI line rejected ({reason}): {raw.strip()[:140]!r}")
        return None
    STATS["ok"] += 1
    return text


STATS = {"ok": 0, "rejected": 0, "failed": 0}


TITLE_PROMPT = """You suggest email subject lines for All Tech Stocks, a weekday email of the most covered
US and UK tech stock news. It is a research digest, not an advice service.

Today's most covered story ({sources} outlets): {top}
Other lead stories:
{others}

Write THREE different subject lines, one per line, no numbering, no quotes.
Each 60 characters or fewer, starting with a capital letter. Plain British English. Specific, not clickbait.
Lead with the top story in at least two of them. One may combine two stories.
Never suggest buying or selling, never mention share price moves or predictions.
No dashes, no semicolons, no hype words, no questions answered in the same line.
Reply with the three lines only."""


def email_titles(top: str, sources: int, others: list[str]) -> list[str]:
    """Three subject line ideas for the day's email. One cheap call per build."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or not top:
        return []
    body = {
        "model": os.environ.get("LLM_MODEL", DEFAULT_MODEL),
        "max_tokens": 200,
        "thinking": {"type": "disabled"},
        "output_config": {"effort": "low"},
        "messages": [{"role": "user", "content": TITLE_PROMPT.format(
            top=top, sources=sources, others="\n".join(f"- {o}" for o in others[:5]))}],
    }
    req = urllib.request.Request(
        API_URL, data=json.dumps(body).encode(),
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
        raw = "".join(b.get("text", "") for b in data.get("content", []))
    except Exception as e:
        print(f"  AI titles failed: {e}")
        return []
    out = []
    for line in raw.splitlines():
        t = line.strip().lstrip("-*0123456789.) ").strip().strip('"').strip()
        low = t.lower()
        if not t or len(t) > 80 or any(b in low for b in BANNED) or ADVICE_RE.search(t):
            continue
        out.append(t[0].upper() + t[1:])
    return out[:3]


def fallback_titles(top: str, sources: int, second: str | None) -> list[str]:
    """Used when the AI call is off or fails, so there are always three."""
    if not top:
        return []
    short = top.split(",")[0].split(":")[0].strip()
    titles = [top]
    if second and len(short) + len(second) < 85:
        titles.append(f"{short}, plus {second[0].lower() + second[1:]}")
    elif second:
        titles.append(second)
    titles.append(f"{sources} outlets on one story: {short}" if sources > 1 else f"Tech stocks today: {short}")
    return titles[:3]


def clean_line(raw: str) -> str:
    """Keep the first paragraph only. The model sometimes adds a note to itself after the sentence."""
    first = next((p for p in raw.strip().split("\n") if p.strip()), "")
    return first.strip().strip('"').strip()


def reject_reason(text: str) -> str | None:
    if not text:
        return "empty"
    words = len(text.split())
    if words > 35:
        return f"{words} words"
    low = text.lower()
    hit = next((b for b in BANNED if b in low), None)
    if hit:
        return f"banned: {hit}"
    m = ADVICE_RE.search(text)
    if m:
        return f"reads like advice or a prediction: {m.group(0)}"
    if not text.endswith((".", "!", "?")):
        return "unfinished sentence"
    return None
