"""Job sources. Each fetcher returns a list of Job objects from a public job-board API."""

from __future__ import annotations

import logging
import re
from typing import Callable

import requests

from ..models import Job
from . import ashby, greenhouse, lever

log = logging.getLogger(__name__)

FETCHERS: dict[str, Callable[[str, requests.Session], list[Job]]] = {
    "greenhouse": greenhouse.fetch,
    "lever": lever.fetch,
    "ashby": ashby.fetch,
}


def _term(x: str) -> re.Pattern:
    """Whole-word pattern for a filter term ('llm' must not match 'fulfillment')."""
    x = x.strip().lower()
    left = r"(?<![a-z0-9])" if x[:1].isalnum() else ""
    right = r"(?![a-z0-9])" if x[-1:].isalnum() else ""
    return re.compile(left + re.escape(x) + right)


def title_matches(title: str, include: list[str], exclude: list[str]) -> bool:
    t = title.lower()
    if any(_term(x).search(t) for x in exclude if x.strip()):
        return False
    return any(_term(x).search(t) for x in include if x.strip())


def fetch_all(companies: dict, session: requests.Session | None = None) -> tuple[list[Job], list[str]]:
    """Fetch every configured board. Returns (matching jobs, warnings)."""
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", "JobPilot/0.1 (personal job search tool)")
    include = companies.get("include_titles", [])
    exclude = companies.get("exclude_titles", [])
    jobs: list[Job] = []
    warnings: list[str] = []
    for platform, fetch in FETCHERS.items():
        for board in companies.get(platform, []) or []:
            try:
                found = fetch(board, session)
            except Exception as exc:  # one bad board must not stop the rest
                warnings.append(f"{platform}/{board}: {exc}")
                continue
            jobs.extend(j for j in found if title_matches(j.title, include, exclude))
    return jobs, warnings
