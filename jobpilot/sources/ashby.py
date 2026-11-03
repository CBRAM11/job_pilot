from __future__ import annotations

import requests

from ..models import Job, html_to_text

API = "https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true"


def parse(board: str, payload: dict) -> list[Job]:
    jobs = []
    for j in payload.get("jobs", []):
        if j.get("isListed") is False:
            continue
        desc = j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml", ""))
        comp = (j.get("compensation") or {}).get("scrapeableCompensationSalarySummary")
        if comp:
            desc = f"{desc}\n\nCompensation: {comp}"
        jobs.append(
            Job(
                company=board,
                title=j.get("title", "").strip(),
                description=desc,
                url=j.get("jobUrl", ""),
                apply_url=j.get("applyUrl", j.get("jobUrl", "")),
                location=j.get("location", ""),
                source="ashby",
                external_id=f"{board}:{j.get('id')}",
            )
        )
    return jobs


def fetch(board: str, session: requests.Session) -> list[Job]:
    r = session.get(API.format(board=board), timeout=30)
    r.raise_for_status()
    return parse(board, r.json())
