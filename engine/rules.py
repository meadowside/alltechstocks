"""Exclusions and section assignment. Pure keyword rules, no AI."""
from __future__ import annotations

import re

from .config import Config


def _phrase_regex(phrases: list[str]) -> re.Pattern | None:
    words = [p for p in phrases if p]
    if not words:
        return None
    alts = sorted((re.escape(p.lower()) for p in words), key=len, reverse=True)
    return re.compile(r"(?<![\w])(" + "|".join(alts) + r")(?![\w])", re.IGNORECASE)


# Product reviews carry the year: "Pixel 11 2026 review", "OLED TV Review (2026)".
REVIEW_TITLE = re.compile(r"(\b20\d\d\s+review|\breview\s*\(20\d\d\))\s*$", re.IGNORECASE)


class Rules:
    def __init__(self, cfg: Config):
        r = cfg.rules
        ex = r["exclude"]
        self.url_patterns = [p.lower() for p in ex["url_patterns"]]
        self.title_keywords = [k.lower() for k in ex["title_keywords"]]
        self.title_exceptions = [k.lower() for k in ex.get("title_keyword_exceptions", [])]
        self.summary_keywords = [k.lower() for k in ex.get("summary_keywords", [])]

        self.section_order = cfg.section_ids
        self.section_res = {sid: _phrase_regex(words) for sid, words in r["section_keywords"].items()}
        self.tech_re = _phrase_regex(r.get("tech_keywords", []))
        self.tech_sections = r.get("tech_sections", list(self.section_res))

    def is_tech(self, text: str) -> bool:
        if self.tech_re and self.tech_re.search(text):
            return True
        return any(self.section_res.get(sid) and self.section_res[sid].search(text) for sid in self.tech_sections)

    # ---------- exclusions ----------
    def exclusion_reason(self, title: str, url: str, summary: str = "") -> str | None:
        u = (url or "").lower()
        for p in self.url_patterns:
            if p in u:
                return f"url:{p}"
        sm = (summary or "").lower()
        for k in self.summary_keywords:
            if k in sm:
                return f"summary:{k}"
        if REVIEW_TITLE.search(title or ""):
            return "title:ends in review"
        t = f" {(title or '').lower()} "
        for k in self.title_keywords:
            if k in t:
                if any(e in t for e in self.title_exceptions) and k in ("review:", "hands-on", "hands on:"):
                    continue
                return f"title:{k.strip()}"
        return None

    # ---------- sections ----------
    def section_hits(self, text: str) -> dict[str, int]:
        hits = {}
        for sid in self.section_order:
            rx = self.section_res.get(sid)
            hits[sid] = len(rx.findall(text)) if rx else 0
        return hits

    def pick_section(self, hits: dict[str, int], hint: list[str]) -> str:
        best = max(hits.values()) if hits else 0
        if best == 0:
            for h in hint:
                if h in self.section_order:
                    return h
            return self.section_order[-1]  # the catch-all, Company News
        tied = [s for s in self.section_order if hits[s] == best]
        for h in hint:
            if h in tied:
                return h
        return tied[0]
