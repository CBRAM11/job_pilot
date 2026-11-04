"""The main actions, shared by the command line and the dashboard."""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import render
from .models import OUTPUT_DIR, Job, load_companies, load_profile
from .scoring import score_job
from .sources import fetch_all
from .tailor import cover_letter, tailor_resume, today
from .tracker import Tracker


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")[:40]


def fetch_and_score(tracker: Tracker, companies_path: Path | None = None) -> dict:
    profile = load_profile()
    jobs, warnings = fetch_all(load_companies(companies_path))
    new = updated = 0
    for job in jobs:
        _, is_new = tracker.upsert(job, score_job(job, profile))
        new += is_new
        updated += not is_new
    return {"found": len(jobs), "new": new, "updated": updated, "warnings": warnings}


def discover(tracker: Tracker, companies_path: Path | None = None, limit: int = 120) -> dict:
    """Search beyond your company list: the public new-grad list and HN 'Who is hiring?'."""
    import requests

    from .sources import hn, newgrad

    profile = load_profile()
    cfg = load_companies(companies_path)
    disc = cfg.get("discover", {}) or {}
    session = requests.Session()
    session.headers["User-Agent"] = "JobPilot/0.2 (personal job search tool)"
    report: dict = {"new": 0, "strong": [], "warnings": []}
    jobs = []
    if disc.get("newgrad_list", True):
        try:
            found, stats = newgrad.fetch(cfg, session, tracker.keys(), limit=limit)
            jobs += found
            report["newgrad"] = stats
        except Exception as exc:
            report["warnings"].append(f"New-grad list: {exc}")
    if disc.get("hn_whos_hiring", True):
        try:
            found = [j for j in hn.fetch(session) if j.key not in tracker.keys()]
            jobs += found
            report["hn"] = {"matching_posts": len(found)}
        except Exception as exc:
            report["warnings"].append(f"HN Who is hiring: {exc}")
    for job in jobs:
        fit = score_job(job, profile)
        if "couldn't be fetched automatically" in job.description and fit.verdict != "Skip":
            fit.verdict = "Unchecked"
            fit.score = min(fit.score, 40)
            fit.warnings.append("Full description not fetched: open the posting and paste it via 'Add a job' to check fit.")
        jid, is_new = tracker.upsert(job, fit)
        report["new"] += is_new
        if fit.verdict == "Strong":
            report["strong"].append((jid, job.company, job.title))
    return report


def add_manual(tracker: Tracker, company: str, title: str, description: str, url: str = "", location: str = "") -> tuple[int, dict]:
    job = Job(company=company, title=title, description=description, url=url, apply_url=url, location=location)
    fit = score_job(job, load_profile())
    job_id, _ = tracker.upsert(job, fit)
    if fit.verdict == "Skip":
        # Manually added jobs stay visible even when flagged, so you can decide.
        tracker.set_status(job_id, "New", "Added manually (flagged: " + "; ".join(fit.blockers)[:200] + ")")
    return job_id, fit.to_dict()


def rescore(tracker: Tracker, job_id: int) -> dict:
    row = tracker.get(job_id)
    job = Tracker.to_job(row)
    job.external_id = ""
    fit = score_job(job, load_profile())
    tracker.db.execute("UPDATE jobs SET score=?, verdict=?, fit=? WHERE id=?",
                       (fit.score, fit.verdict, json.dumps(fit.to_dict()), job_id))
    tracker.db.commit()
    return fit.to_dict()


def tailor_job(tracker: Tracker, job_id: int, use_llm: bool = False, with_cover: bool = True) -> dict:
    row = tracker.get(job_id)
    if not row:
        raise KeyError(f"No job with id {job_id}")
    profile = load_profile()
    job = Tracker.to_job(row)
    tr = tailor_resume(job, profile, use_llm=use_llm)

    folder = OUTPUT_DIR / f"{job_id:04d}_{_slug(job.company)}"
    folder.mkdir(parents=True, exist_ok=True)
    base = f"{_slug(profile['name'].split()[0])}_{_slug(profile['name'].split()[-1])}_Resume_{_slug(job.company)}"
    pdf, docx = folder / f"{base}.pdf", folder / f"{base}.docx"
    pages = render.render_pdf(tr, pdf)
    render.render_docx(tr, docx)
    notes = list(tr.notes)
    if pages > 1:
        notes.append(f"WARNING: resume is {pages} pages; trim a bullet in profile.yaml.")
    paths = {"resume_pdf": str(pdf), "resume_docx": str(docx)}

    if with_cover:
        text, msg = cover_letter(job, profile, tr, use_llm=use_llm)
        notes.append(msg)
        cbase = base.replace("_Resume_", "_Cover_Letter_")
        cpdf, cdocx = folder / f"{cbase}.pdf", folder / f"{cbase}.docx"
        render.render_letter_pdf(text, profile.data, cpdf, today())
        render.render_letter_docx(text, profile.data, cdocx, today())
        (folder / "cover_letter.txt").write_text(text, encoding="utf-8")
        paths.update(cover_pdf=str(cpdf), cover_docx=str(cdocx))

    (folder / "tailoring_notes.txt").write_text("\n".join(notes), encoding="utf-8")
    tracker.save_documents(job_id, tailor_notes="\n".join(notes), **paths)
    if row["status"] in ("New", "Shortlisted", "Skipped"):
        tracker.set_status(job_id, "Tailored", "Resume tailored")
    return {"folder": str(folder), "pages": pages, "notes": notes, **paths}
