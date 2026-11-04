"""Fetch a single job's full description from its application URL.

Supports the public job-posting APIs of the most common applicant-tracking systems:
Greenhouse, Lever, Ashby, Workday, Workable and SmartRecruiters. Returns None for
anything else (the job is still listed, just scored on its title until you open it).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import requests

from ..models import html_to_text

_ashby_cache: dict[str, dict] = {}


def _greenhouse(u, s):
    m = re.match(r"/(?:embed/job_app\?for=)?([^/]+)/jobs/(\d+)", u.path)
    if not m:
        return None
    j = s.get(f"https://boards-api.greenhouse.io/v1/boards/{m[1]}/jobs/{m[2]}", timeout=20).json()
    return j.get("title"), html_to_text(j.get("content", "")), (j.get("location") or {}).get("name", "")


def _lever(u, s):
    parts = [p for p in u.path.split("/") if p]
    if len(parts) < 2:
        return None
    p = s.get(f"https://api.lever.co/v0/postings/{parts[0]}/{parts[1]}", timeout=20).json()
    text = [p.get("descriptionPlain", "")]
    for sec in p.get("lists", []) or []:
        text.append(f"{sec.get('text', '')}\n{html_to_text(sec.get('content', ''))}")
    text.append(p.get("additionalPlain", ""))
    return p.get("text"), "\n\n".join(t for t in text if t), (p.get("categories") or {}).get("location", "")


def _ashby(u, s):
    parts = [p for p in u.path.split("/") if p]
    if len(parts) < 2:
        return None
    board, jid = parts[0], parts[1]
    if board not in _ashby_cache:
        data = s.get(f"https://api.ashbyhq.com/posting-api/job-board/{board}", timeout=20).json()
        _ashby_cache[board] = {j["id"]: j for j in data.get("jobs", [])}
    j = _ashby_cache[board].get(jid)
    if not j:
        return None
    return j.get("title"), j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml", "")), j.get("location", "")


def _workday(u, s):
    # https://<tenant>.wdN.myworkdayjobs.com/[en-US/]<site>/job/<location>/<slug>
    tenant = u.netloc.split(".")[0]
    parts = [p for p in u.path.split("/") if p]
    if parts and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", parts[0]):
        parts = parts[1:]
    if "job" not in parts:
        return None
    i = parts.index("job")
    site, rest = parts[i - 1] if i else parts[0], "/".join(parts[i + 1:])
    r = s.get(f"https://{u.netloc}/wday/cxs/{tenant}/{site}/job/{rest}", timeout=20, headers={"Accept": "application/json"})
    info = r.json().get("jobPostingInfo", {})
    return info.get("title"), html_to_text(info.get("jobDescription", "")), info.get("location", "")


def _workable(u, s):
    m = re.match(r"/([^/]+)/j/([A-Za-z0-9]+)", u.path)
    if not m:
        return None
    j = s.get(f"https://apply.workable.com/api/v2/accounts/{m[1]}/jobs/{m[2]}", timeout=20).json()
    text = "\n\n".join(html_to_text(j.get(k, "")) for k in ("description", "requirements", "benefits") if j.get(k))
    loc = j.get("location") or {}
    return j.get("title"), text, ", ".join(x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x)


def _smartrecruiters(u, s):
    m = re.match(r"/([^/]+)/(\d+)", u.path)
    if not m:
        return None
    j = s.get(f"https://api.smartrecruiters.com/v1/companies/{m[1]}/postings/{m[2]}", timeout=20).json()
    secs = ((j.get("jobAd") or {}).get("sections") or {})
    text = "\n\n".join(html_to_text(v.get("text", "")) for v in secs.values() if isinstance(v, dict))
    loc = j.get("location") or {}
    return j.get("name"), text, ", ".join(x for x in (loc.get("city"), loc.get("region")) if x)


RESOLVERS = [
    (re.compile(r"(^|\.)greenhouse\.io$"), _greenhouse),
    (re.compile(r"^jobs\.lever\.co$"), _lever),
    (re.compile(r"^jobs\.ashbyhq\.com$"), _ashby),
    (re.compile(r"\.myworkdayjobs\.com$"), _workday),
    (re.compile(r"^apply\.workable\.com$"), _workable),
    (re.compile(r"^jobs\.smartrecruiters\.com$"), _smartrecruiters),
]


def supported(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(rx.search(host) for rx, _ in RESOLVERS)


def fetch_description(url: str, session: requests.Session) -> tuple[str | None, str, str] | None:
    """Return (title, description, location) or None if unsupported/failed."""
    u = urlparse(url)
    host = u.netloc.lower()
    for rx, fn in RESOLVERS:
        if rx.search(host):
            try:
                out = fn(u, session)
            except Exception:
                return None
            if out and out[1] and len(out[1]) > 200:
                return out
            return None
    return None
