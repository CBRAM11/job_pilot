from __future__ import annotations

import requests

from ..models import Job, html_to_text

API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"


def parse(board: str, payload: dict) -> list[Job]:
    jobs = []
    for j in payload.get("jobs", []):
        url = j.get("absolute_url", "")
        jobs.append(
            Job(
                company=board,
                title=j.get("title", "").strip(),
                description=html_to_text(j.get("content", "")),
                url=url,
                apply_url=url,
                location=(j.get("location") or {}).get("name", ""),
                source="greenhouse",
                external_id=f"{board}:{j.get('id')}",
            )
        )
    return jobs


def fetch(board: str, session: requests.Session) -> list[Job]:
    r = session.get(API.format(board=board), timeout=30)
    r.raise_for_status()
    return parse(board, r.json())
