from __future__ import annotations

import requests

from ..models import Job, html_to_text

API = "https://api.lever.co/v0/postings/{board}?mode=json"


def parse(board: str, payload: list) -> list[Job]:
    jobs = []
    for p in payload:
        parts = [p.get("descriptionPlain", "")]
        for section in p.get("lists", []) or []:
            items = section.get("content", "")
            # Lever list content is HTML <li> items
            parts.append(f"{section.get('text', '')}\n{html_to_text(items)}")
        parts.append(p.get("additionalPlain", ""))
        cats = p.get("categories") or {}
        jobs.append(
            Job(
                company=board,
                title=p.get("text", "").strip(),
                description="\n\n".join(x for x in parts if x).strip(),
                url=p.get("hostedUrl", ""),
                apply_url=p.get("applyUrl", p.get("hostedUrl", "")),
                location=cats.get("location", ""),
                source="lever",
                external_id=f"{board}:{p.get('id')}",
            )
        )
    return jobs


def fetch(board: str, session: requests.Session) -> list[Job]:
    r = session.get(API.format(board=board), timeout=30)
    r.raise_for_status()
    return parse(board, r.json())
