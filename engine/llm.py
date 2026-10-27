"""Optional: short "so what" paragraphs (three or four sentences) for stories on the site and in the email.

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
    # Buying and selling the shares, not companies buying companies ("Salesforce plans to buy Braze" is fine).
    r"buy (?:the |its |their )?(?:shares|stock|dip)", r"sell (?:the |its |their )?(?:shares|stock)",
    r"hold (?:the |its |their )?(?:shares|stock)", r"buying (?:the )?(?:shares|stock|opportunity)",
    r"selling (?:the )?(?:shares|stock)", r"(?:buy|sell|hold) (?:rating|signal|call)", r"a (?:buy|sell|hold)\b",
    r"time to (?:buy|sell)", r"worth buying",
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

Write THREE or FOUR short sentences, 80 words or fewer in total, as one paragraph, saying what this means
for the company's business. Cover what happened, then the effect on its customers, products, costs, rivals,
rules or plans, and any wider context a reader needs (a rival's move, a regulator, a past deal).
Vary the sentence lengths. Plain British English. No hype.
Strict rules:
- Never say or hint whether to buy, sell or hold anything.
- Never mention the share price, valuation, returns or what the shares might do.
- Never predict. Describe what has happened or what the company has said, and nothing more.
- No dashes, no semicolons. Don't repeat the headline. Don't start with "This".
- Never mention the coverage, the headline, the sources or the article. Talk about the story.
- Never pad. Don't say what isn't known, isn't clear or isn't described. Every sentence must add a fact
  or a consequence. If there's only enough for two solid sentences, write two.
If the coverage gives too little to say anything useful, reply with the single word NONE.
Reply with the paragraph only."""


def enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def why_it_matters(headline: str, snippets: list[str], companies: str = "") -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    body = {
        "model": os.environ.get("LLM_MODEL", DEFAULT_MODEL),
        "max_tokens": 400,
        # Haiku 5.5 thinks by default and the thinking counts against max_tokens, which left
        # replies empty. A short paragraph needs no thinking.
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
    text = drop_padding(clean_line(raw))
    if text.strip(" .").upper() == "NONE" or NON_ANSWER.search(text):
        return ""  # nothing useful to say: no line, not an error
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


def _ask(prompt: str, max_tokens: int = 300) -> str | None:
    """One plain call to the model. Returns the reply text, or None if the key is missing or the call fails."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    body = {
        "model": os.environ.get("LLM_MODEL", DEFAULT_MODEL),
        "max_tokens": max_tokens,
        "thinking": {"type": "disabled"},
        "output_config": {"effort": "low"},
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        API_URL, data=json.dumps(body).encode(),
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
        return "".join(b.get("text", "") for b in data.get("content", []))
    except Exception as e:
        print(f"  AI call failed: {e}")
        return None


def _lead_list(leads: list[tuple[str, int]]) -> str:
    return "\n".join(f"- {h} ({n} outlet{'s' if n != 1 else ''})" for h, n in leads[:6])


TITLE_PROMPT = """You suggest email subject lines for All Tech Stocks, a weekday email of the most covered
US and UK tech stock news. It is a research digest, not an advice service.

Today's lead stories, most covered first:
{leads}

Write THREE different subject lines, one per line, no numbering, no quotes.
Every line must combine TWO of the lead stories, so a reader sees two reasons to open it.
Use the two most covered stories in at least one line. Vary the pairing across the three.
Name the companies. Join the two with a comma or "and", or "as" if one led to the other.
Each 70 characters or fewer, starting with a capital letter. Plain British English. Specific, not clickbait.
Never suggest buying or selling, never mention share price moves or predictions.
No dashes, no semicolons, no hype words, no questions.
Reply with the three lines only."""


def email_titles(leads: list[tuple[str, int]]) -> list[str]:
    """Three subject line ideas for the day's email, each pairing two lead stories. One call per build.
    leads: (headline, outlet count), most covered first."""
    if len(leads) < 2:
        return []
    raw = _ask(TITLE_PROMPT.format(leads=_lead_list(leads)), 200)
    if not raw:
        return []
    out = []
    for line in raw.splitlines():
        t = line.strip().lstrip("-*0123456789.) ").strip().strip('"').strip()
        low = t.lower()
        if not t or len(t) > 80 or "?" in t or any(b in low for b in BANNED) or ADVICE_RE.search(t):
            continue
        out.append(t[0].upper() + t[1:])
    return out[:3]


NOTE_PROMPT = """You draft the opening note for today's All Tech Stocks email, a weekday digest of the most
covered US and UK tech stock news. Andy signs it. It is a research digest, never an advice service.

Today is {day}.
Lead stories, most covered first:
{leads}

Write two or three short sentences, 60 words or fewer in total, saying what's big today.
Pick the two or three stories that matter most for the companies' businesses and say what happened.
Skip crime, celebrity, gossip and stories with no real business angle, even if they're listed.
Voice: a sharp, plain-speaking British editor writing to regular readers. Contractions are fine.
A short fragment to open is fine. Name the companies.
Strict rules:
- Never say or hint whether to buy, sell or hold anything.
- Never mention share prices, valuations, returns or what the shares might do.
- Never predict. Upcoming results may be mentioned only as scheduled events.
- No dashes of any kind, no semicolons, no commas before "and" in a list, no hype words.
- Don't mention outlets, sources, coverage counts or this email. Don't greet the reader or sign off.
Reply with the note only."""


def opening_note(leads: list[tuple[str, int]], day: str) -> str | None:
    """A suggested opening note for Andy to edit. None if the call fails or the note breaks a rule."""
    if not leads:
        return None
    raw = _ask(NOTE_PROMPT.format(leads=_lead_list(leads), day=day), 300)
    if not raw:
        return None
    text = " ".join(p.strip() for p in raw.strip().split("\n") if p.strip()).strip('"').strip()
    reason = note_reject_reason(text)
    if reason:
        print(f"  AI opening note rejected ({reason}): {text[:160]!r}")
        return None
    return text


TOP_WHY_PROMPT = """You suggest the "Why it matters" line for the top story in today's All Tech Stocks email,
a weekday digest of the most covered US and UK tech stock news. Andy edits and signs it.
It is a research digest, never an advice service.

Top story: {headline}
Companies tagged: {companies}
What the coverage says:
{snippets}

Write two or three short sentences, 60 words or fewer in total, on why this story matters beyond the
headline: what it changes for the company's business and for its customers, rivals or the wider sector.
Lead with the single biggest consequence. Voice: a sharp, plain-speaking British editor. Contractions are fine.
Strict rules:
- Never say or hint whether to buy, sell or hold anything.
- Never mention share prices, valuations, returns or what the shares might do.
- Never predict. Describe what has happened or what the companies have said. "If the deal goes through"
  style conditions are fine, forecasts are not.
- No dashes of any kind, no semicolons, no hype words. Don't repeat the headline. Don't start with "This".
- Never mention the coverage, the sources or the article.
If the coverage gives too little to say anything useful, reply with the single word NONE.
Reply with the line only."""


def top_why(headline: str, snippets: list[str], companies: str = "") -> str | None:
    """A suggested 'Why it matters' line for the email's top story. None if off, failed or rule-breaking."""
    raw = _ask(TOP_WHY_PROMPT.format(headline=headline, companies=companies or "none",
                                     snippets="\n".join(f"- {s}" for s in snippets[:6] if s)), 300)
    if not raw:
        return None
    text = " ".join(p.strip() for p in raw.strip().split("\n") if p.strip()).strip('"').strip()
    if text.strip(" .").upper() == "NONE" or NON_ANSWER.search(text):
        return None
    reason = note_reject_reason(text)
    if reason:
        print(f"  AI why-it-matters rejected ({reason}): {text[:160]!r}")
        return None
    return text


def note_reject_reason(text: str) -> str | None:
    if not text:
        return "empty"
    words = len(text.split())
    if words > 75:
        return f"{words} words"
    sentences = len(re.findall(r"[.!?](?:\s|$)", text))
    if sentences > 4:
        return f"{sentences} sentences"
    low = text.lower()
    hit = next((b for b in BANNED if b in low), None)
    if hit:
        return f"banned: {hit}"
    m = ADVICE_RE.search(text)
    if m:
        return f"reads like advice or a prediction: {m.group(0)}"
    if not text.endswith((".", "!")):
        return "unfinished"
    return None


def fallback_titles(leads: list[tuple[str, int]]) -> list[str]:
    """Used when the AI call is off or fails: each title pairs two lead stories."""
    def short(h: str) -> str:
        return h.split(",")[0].split(":")[0].split(" | ")[0].strip()
    heads = [short(h) for h, _ in leads if h]
    if not heads:
        return []
    if len(heads) == 1:
        return [heads[0]]
    def low(h: str) -> str:  # "Earnings playbook" -> "earnings playbook", but "UK government" stays
        return h[0].lower() + h[1:] if len(h) > 1 and h[1].islower() else h
    a, rest = heads[0], heads[1:]
    titles = [f"{a}, plus {low(b)}" for b in rest[:3]]
    if len(rest) >= 2:
        titles[-1] = f"{rest[0]}, plus {low(rest[1])}"
    return titles[:3]


# Sentences that talk about the source material or what isn't known instead of the story.
PADDING = re.compile(r"\b(the|its|this) (coverage|headline|article|report|information given|details available)\b|"
                     r"\b(does not|doesn't|do not|don't|did not|didn't) (say|state|describe|detail|give|mention|specify|explain|disclose)|"
                     r"\bwithout (those|these|more|further) details\b|\bnothing (further|more|else)\b.*\bsaid\b|"
                     r"\b(limited|little) (information|detail)\b|"
                     r"\b(is|are|remains?|it's) (not yet |still )?(unclear|not clear)|"
                     r"\bdirect effect\b|\bno detail\b", re.I)


def drop_padding(text: str) -> str:
    """Remove sentences that pad the line with what the coverage doesn't say. If that leaves fewer
    than two real sentences, there's nothing worth saying and the line is dropped."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    if len(parts) < 2:
        return "" if PADDING.search(text) else text
    kept = [p for p in parts if not PADDING.search(p)]
    return " ".join(kept) if len(kept) >= 2 else ""  # mostly padding: no line at all


def clean_line(raw: str) -> str:
    """Keep the first paragraph only. The model sometimes adds a note to itself after the sentence."""
    first = next((p for p in raw.strip().split("\n") if p.strip()), "")
    return first.strip().strip('"').strip()


# The model sometimes says "nothing here" in a sentence instead of answering NONE.
NON_ANSWER = re.compile(r"no clear (effect|impact|link)|names no |doesn't name|does not name|the headline suggests|"
                        r"the headline (only|just)|unclear (how|whether|what)|not clear (how|whether|what)|"
                        r"no (direct|obvious) (effect|impact|link)|isn't about|is not about", re.I)


def reject_reason(text: str) -> str | None:
    if not text:
        return "empty"
    words = len(text.split())
    if words > 120:
        return f"{words} words"
    sentences = len(re.findall(r"[.!?](?:\s|$)", text))
    if sentences > 5:
        return f"{sentences} sentences"
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
