"""Job sources. Each fetcher returns a list of Job objects from a public job-board API."""

from __future__ import annotations

import logging
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


def title_matches(title: str, include: list[str], exclude: list[str]) -> bool:
    t = f" {title.lower()} "
    if any(x.lower() in t for x in exclude):
        return False
    return any(x.lower() in t for x in include)


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
