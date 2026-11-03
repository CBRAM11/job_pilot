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
