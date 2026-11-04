"""Discover roles from Hacker News' monthly "Ask HN: Who is hiring?" thread.

Uses HN's public Algolia API. Each top-level comment is one company's post, usually
"Company | Role | Location | VISA | ...", with the full description in the comment, so
JobPilot can score it directly. Many of these posts state visa sponsorship explicitly.
"""

from __future__ import annotations

import re

import requests

from ..models import Job, html_to_text

SEARCH = "https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=10"
ITEM = "https://hn.algolia.com/api/v1/items/{id}"
ROLE_WORDS = re.compile(r"machine learning|\bml\b|\bai\b|\bllm|deep learning|nlp|computer vision|data scien|applied scien|research engineer", re.I)


def latest_thread_id(session: requests.Session) -> str | None:
    hits = session.get(SEARCH, timeout=30).json().get("hits", [])
    for h in hits:
        if "who is hiring" in (h.get("title") or "").lower():
            return h["objectID"]
    return None


def parse_comment(c: dict) -> Job | None:
    text = html_to_text(c.get("text") or "")
    if len(text) < 120 or not ROLE_WORDS.search(text):
        return None
    first = text.split("\n", 1)[0]
    parts = [p.strip() for p in first.split("|") if p.strip()]
    company = parts[0][:80] if parts else "HN post"
    role = next((p for p in parts[1:] if ROLE_WORDS.search(p) or re.search(r"engineer|scientist", p, re.I)), None)
    title = (role or (parts[1] if len(parts) > 1 else "See post"))[:120]
    location = next((p for p in parts[1:] if re.search(r"remote|onsite|hybrid|, [A-Z]{2}\b|NYC|SF|San Francisco|New York", p, re.I)), "")
    url = next(iter(re.findall(r"https?://\S+", text)), f"https://news.ycombinator.com/item?id={c.get('id')}")
    return Job(company=company, title=title, description=text, url=f"https://news.ycombinator.com/item?id={c.get('id')}",
               apply_url=url.rstrip(").,"), location=location, source="hn", external_id=str(c.get("id")))


def fetch(session: requests.Session) -> list[Job]:
    tid = latest_thread_id(session)
    if not tid:
        return []
    thread = session.get(ITEM.format(id=tid), timeout=60).json()
    jobs = []
    for c in thread.get("children", []) or []:
        j = parse_comment(c)
        if j:
            jobs.append(j)
    return jobs
