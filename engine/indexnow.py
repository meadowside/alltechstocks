"""IndexNow: tell Bing about new and changed pages as soon as they're published.

Bing feeds ChatGPT search and Microsoft Copilot, so a page Bing knows about quickly can be found by
those too. Yandex, Seznam and Naver share the same submissions.

plan() runs during the build and decides what to send: every page in the sitemap once a day, plus
any page new to the sitemap since the last run (a company page opening up, a new weekly ranking).
It only writes the list. send() runs after the site is deployed, so the pages exist when Bing looks.

Never breaks the build: problems are printed and skipped.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

ENDPOINT = "https://api.indexnow.org/indexnow"
FULL_FROM_HOUR = 6  # the daily full list goes after 6am UK time, once the overnight news is in


def plan(urls: list[str], base: str, key: str, data_dir: Path, local: datetime) -> int:
    """Work out which URLs to submit after deploy. Returns how many."""
    state_path, pending_path = data_dir / "indexnow.json", data_dir / "indexnow_pending.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    except ValueError:
        state = {}
    known = set(state.get("known", []))
    today = local.date().isoformat()
    if state.get("last_full") != today and local.hour >= FULL_FROM_HOUR:
        send_now, state["last_full"] = list(urls), today
    else:
        send_now = [u for u in urls if u not in known]
    state["known"] = sorted(set(urls))
    state_path.write_text(json.dumps(state, indent=1), encoding="utf-8")
    host = base.split("://", 1)[-1].strip("/")
    if send_now:
        pending_path.write_text(json.dumps({"host": host, "key": key, "keyLocation": f"{base}/{key}.txt",
                                            "urlList": send_now[:10000]}), encoding="utf-8")
    elif pending_path.exists():
        pending_path.unlink()
    return len(send_now)


def send(pending_path: Path) -> None:
    if not pending_path.exists():
        print("IndexNow: nothing to send")
        return
    body = pending_path.read_bytes()
    n = len(json.loads(body)["urlList"])
    req = urllib.request.Request(ENDPOINT, data=body, method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"IndexNow: sent {n} URLs, response {r.status}")
    except Exception as e:  # noqa: BLE001
        print(f"IndexNow: send failed, site unaffected ({type(e).__name__}: {e})")
    pending_path.unlink()


if __name__ == "__main__":
    send(Path(sys.argv[1] if len(sys.argv) > 1 else "data/indexnow_pending.json"))
