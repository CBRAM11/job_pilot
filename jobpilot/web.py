"""Local dashboard: python -m jobpilot serve  → http://127.0.0.1:5000"""

from __future__ import annotations

import threading
from pathlib import Path

from flask import Flask, abort, flash, redirect, render_template, request, send_file, url_for

from . import llm, pipeline
from .models import OUTPUT_DIR, load_profile
from .tracker import STATUSES, Tracker

PREFILL_REPORTS: dict[int, dict] = {}


def create_app(tracker: Tracker | None = None) -> Flask:
    app = Flask(__name__)
    app.secret_key = "jobpilot-local-only"
    tr = tracker or Tracker()

    @app.get("/")
    def index():
        status = request.args.get("status") or None
        verdict = request.args.get("verdict") or None
        q = (request.args.get("q") or "").strip()
        min_score = int(request.args.get("min") or 0)
        if q or min_score:
            rows = tr.search(q, min_score)
        else:
            rows = tr.list(status=status, verdict=verdict, include_skipped=status == "Skipped")
        return render_template("index.html", rows=rows, counts=tr.counts(), statuses=STATUSES,
                               status=status, verdict=verdict, q=q, min_score=min_score)

    @app.post("/discover")
    def discover():
        res = pipeline.discover(tr)
        parts = [f"Discovered {res['new']} new jobs."]
        if res.get("newgrad"):
            s = res["newgrad"]
            parts.append(f"New-grad list: {s['listed']} matching roles, {s['full_descriptions']} full descriptions fetched.")
        if res.get("hn"):
            parts.append(f"HN Who is hiring: {res['hn']['matching_posts']} AI/ML posts.")
        if res["strong"]:
            parts.append(f"{len(res['strong'])} strong matches.")
        parts += res["warnings"]
        flash(" ".join(parts))
        return redirect(url_for("index", verdict="Strong") if res["strong"] else url_for("index"))

    @app.post("/fetch")
    def fetch():
        res = pipeline.fetch_and_score(tr)
        msg = f"Found {res['found']} matching jobs ({res['new']} new)."
        if res["warnings"]:
            msg += f" {len(res['warnings'])} boards skipped: " + "; ".join(res["warnings"][:4])
        flash(msg)
        return redirect(url_for("index"))

    @app.route("/add", methods=["GET", "POST"])
    def add():
        if request.method == "POST":
            f = request.form
            if not (f.get("company") and f.get("title") and f.get("description")):
                flash("Company, title and description are required.")
                return render_template("add.html", form=f)
            job_id, _ = pipeline.add_manual(tr, f["company"].strip(), f["title"].strip(), f["description"],
                                            f.get("url", "").strip(), f.get("location", "").strip())
            return redirect(url_for("job", job_id=job_id))
        return render_template("add.html", form={})

    @app.get("/job/<int:job_id>")
    def job(job_id: int):
        row = tr.get(job_id)
        if not row:
            abort(404)
        return render_template("job.html", j=row, statuses=STATUSES, ai=llm.available(),
                               prefill=PREFILL_REPORTS.get(job_id))

    @app.post("/job/<int:job_id>/status")
    def status(job_id: int):
        tr.set_status(job_id, request.form["status"], request.form.get("note", ""))
        flash(f"Status updated to {request.form['status']}.")
        return redirect(url_for("job", job_id=job_id))

    @app.post("/job/<int:job_id>/notes")
    def notes(job_id: int):
        tr.set_notes(job_id, request.form.get("notes", ""))
        flash("Notes saved.")
        return redirect(url_for("job", job_id=job_id))

    @app.post("/job/<int:job_id>/tailor")
    def tailor(job_id: int):
        res = pipeline.tailor_job(tr, job_id, use_llm=bool(request.form.get("ai")), with_cover=True)
        flash("Tailored resume and cover letter are ready." + (" (Resume ran over one page — see notes.)" if res["pages"] > 1 else ""))
        return redirect(url_for("job", job_id=job_id))

    @app.post("/job/<int:job_id>/rescore")
    def rescore(job_id: int):
        pipeline.rescore(tr, job_id)
        flash("Fit re-checked against your current profile.")
        return redirect(url_for("job", job_id=job_id))

    @app.post("/job/<int:job_id>/prefill")
    def prefill_route(job_id: int):
        row = tr.get(job_id)
        if not row.get("resume_pdf"):
            pipeline.tailor_job(tr, job_id)
            row = tr.get(job_id)
        url = row["apply_url"] or row["url"]
        if not url:
            flash("This job has no application link. Add one by re-adding the job with its URL.")
            return redirect(url_for("job", job_id=job_id))

        def run():
            from .prefill import prefill

            try:
                prefill(url, load_profile(), row.get("resume_pdf"), row.get("cover_pdf"), keep_open="window",
                        on_report=lambda r: PREFILL_REPORTS.__setitem__(job_id, r.__dict__))
            except Exception as exc:
                PREFILL_REPORTS[job_id] = {"filled": [], "uploaded": [], "needs_you": [f"Browser error: {exc}"]}

        threading.Thread(target=run, daemon=True).start()
        flash("Opening the application in a new browser window. Review it, then submit it yourself.")
        return redirect(url_for("job", job_id=job_id))

    @app.get("/file")
    def file():
        p = Path(request.args.get("path", "")).resolve()
        if OUTPUT_DIR.resolve() not in p.parents or not p.exists():
            abort(404)
        return send_file(p, as_attachment=request.args.get("dl") == "1")

    return app
