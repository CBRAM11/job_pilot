"""Discover new-grad roles from the community-maintained SimplifyJobs list on GitHub.

https://github.com/SimplifyJobs/New-Grad-Positions publishes every listing as JSON
(about 3,000 active roles, updated daily). JobPilot keeps US roles posted recently whose
titles match your filters, then fetches each one's full description so it can check
sponsorship, graduation window and skills against your profile.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone

import requests

from ..models import Job
from . import details

LISTINGS_URL = "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json"

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA",
    "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
}
US_WORDS = re.compile(r"\b(usa|united states|u\.s\.|remote in us|sf|nyc|san francisco|new york|seattle|boston)\b", re.I)


def is_us(locations: list[str]) -> bool:
    for loc in locations or []:
        if US_WORDS.search(loc):
            return True
        tail = loc.rsplit(",", 1)[-1].strip().upper()
        if tail in US_STATES or loc.strip().upper() in US_STATES:
            return True
    return False


def select(listings: list[dict], include: list[str], exclude: list[str], days: int, us_only: bool = True) -> list[dict]:
    from . import title_matches

    cutoff = time.time() - days * 86400
    out = []
    for x in listings:
        if not x.get("active") or not x.get("is_visible", True):
            continue
        if x.get("date_posted", 0) < cutoff:
            continue
        if not title_matches(x.get("title", ""), include, exclude):
            continue
        if us_only and not is_us(x.get("locations", [])):
            continue
        out.append(x)
    out.sort(key=lambda x: x.get("date_posted", 0), reverse=True)
    return out


def to_job(x: dict, fetched: tuple | None) -> Job:
    sponsorship = x.get("sponsorship", "Other")
    header = [f"Listed on the SimplifyJobs new-grad list ({datetime.fromtimestamp(x.get('date_posted', 0), timezone.utc):%b %d, %Y})."]
    if x.get("degrees"):
        header.append("Degrees: " + ", ".join(x["degrees"]) + ".")
    # Turn the list's own sponsorship flag into the wording the scorer recognises.
    if sponsorship == "Does Not Offer Sponsorship":
        header.append("This role does not offer visa sponsorship: sponsorship is not available.")
    elif sponsorship == "U.S. Citizenship is Required":
        header.append("U.S. citizenship is required for this role.")
    elif sponsorship == "Offers Sponsorship":
        header.append("Visa sponsorship is available.")
    if fetched:
        _, desc, loc = fetched
        description = " ".join(header) + "\n\n" + desc
        location = loc or ", ".join(x.get("locations", []))
    else:
        description = " ".join(header) + "\n\n(Full description couldn't be fetched automatically. Open the posting, " \
                      "then use 'Add a job' to paste it for a full fit check.)"
        location = ", ".join(x.get("locations", []))
    return Job(company=x.get("company_name", ""), title=x.get("title", ""), description=description,
               url=x.get("url", ""), apply_url=x.get("url", ""), location=location,
               source="newgrad", external_id=x.get("id", ""))


def fetch(cfg: dict, session: requests.Session, known_keys: set[str], limit: int = 120) -> tuple[list[Job], dict]:
    include, exclude = cfg.get("include_titles", []), cfg.get("exclude_titles", [])
    disc = cfg.get("discover", {}) or {}
    listings = session.get(disc.get("newgrad_url", LISTINGS_URL), timeout=60).json()
    picked = select(listings, include, exclude, int(disc.get("days", 14)), bool(disc.get("us_only", True)))
    jobs, fetched_n, skipped_known = [], 0, 0
    for x in picked:
        if f"newgrad:{x.get('id')}" in known_keys:
            skipped_known += 1
            continue
        if len(jobs) >= limit:
            break
        got = details.fetch_description(x["url"], session) if details.supported(x["url"]) else None
        fetched_n += bool(got)
        jobs.append(to_job(x, got))
    return jobs, {"listed": len(picked), "already_tracked": skipped_known, "full_descriptions": fetched_n}
