"""Application tracker stored in a local SQLite file (output/jobpilot.db).

Statuses are updated by you (manually), and every change is kept in a history table.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import OUTPUT_DIR, Job
from .scoring import FitResult

STATUSES = ["New", "Shortlisted", "Tailored", "Applied", "Online assessment",
            "Interviewing", "Offer", "Rejected", "Withdrawn", "Skipped"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE NOT NULL,
    source TEXT, company TEXT, title TEXT, location TEXT,
    url TEXT, apply_url TEXT, description TEXT,
    score INTEGER, verdict TEXT, fit TEXT,
    status TEXT NOT NULL DEFAULT 'New',
    resume_pdf TEXT, resume_docx TEXT, cover_pdf TEXT, cover_docx TEXT, tailor_notes TEXT,
    notes TEXT DEFAULT '',
    created_at TEXT, updated_at TEXT, applied_at TEXT
);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    status TEXT, note TEXT, at TEXT
);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Tracker:
    def __init__(self, path: Path | str | None = None):
        path = Path(path) if path else OUTPUT_DIR / "jobpilot.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        """Release the database file (Windows keeps open files locked)."""
        self.db.close()

    def __enter__(self) -> "Tracker":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- writes ----
    def upsert(self, job: Job, fit: FitResult) -> tuple[int, bool]:
        """Insert a job, or refresh its score if already stored. Returns (id, is_new)."""
        row = self.db.execute("SELECT id FROM jobs WHERE key=?", (job.key,)).fetchone()
        fit_json = json.dumps(fit.to_dict())
        if row:
            self.db.execute("UPDATE jobs SET description=?, score=?, verdict=?, fit=?, updated_at=? WHERE id=?",
                            (job.description, fit.score, fit.verdict, fit_json, _now(), row["id"]))
            self.db.commit()
            return row["id"], False
        status = "Skipped" if fit.verdict == "Skip" else "New"
        cur = self.db.execute(
            """INSERT INTO jobs (key, source, company, title, location, url, apply_url, description,
               score, verdict, fit, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (job.key, job.source, job.company, job.title, job.location, job.url, job.apply_url or job.url,
             job.description, fit.score, fit.verdict, fit_json, status, _now(), _now()))
        self.db.execute("INSERT INTO history (job_id, status, note, at) VALUES (?,?,?,?)",
                        (cur.lastrowid, status, "Found" if status == "New" else "Auto-skipped: " + "; ".join(fit.blockers)[:300], _now()))
        self.db.commit()
        return cur.lastrowid, True

    def set_status(self, job_id: int, status: str, note: str = "") -> None:
        if status not in STATUSES:
            raise ValueError(f"Unknown status {status!r}. Use one of: {', '.join(STATUSES)}")
        applied = ", applied_at=COALESCE(applied_at, ?)" if status == "Applied" else ""
        params: list[Any] = [status, _now()] + ([_now()] if applied else []) + [job_id]
        self.db.execute(f"UPDATE jobs SET status=?, updated_at=?{applied} WHERE id=?", params)
        self.db.execute("INSERT INTO history (job_id, status, note, at) VALUES (?,?,?,?)", (job_id, status, note, _now()))
        self.db.commit()

    def set_notes(self, job_id: int, notes: str) -> None:
        self.db.execute("UPDATE jobs SET notes=?, updated_at=? WHERE id=?", (notes, _now(), job_id))
        self.db.commit()

    def save_documents(self, job_id: int, **paths: str) -> None:
        cols = {k: v for k, v in paths.items() if k in {"resume_pdf", "resume_docx", "cover_pdf", "cover_docx", "tailor_notes"}}
        if not cols:
            return
        sets = ", ".join(f"{k}=?" for k in cols)
        self.db.execute(f"UPDATE jobs SET {sets}, updated_at=? WHERE id=?", [*cols.values(), _now(), job_id])
        self.db.commit()

    # ---- reads ----
    def get(self, job_id: int) -> dict | None:
        row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["fit"] = json.loads(d["fit"] or "{}")
        d["history"] = [dict(h) for h in self.db.execute(
            "SELECT status, note, at FROM history WHERE job_id=? ORDER BY id", (job_id,))]
        return d

    def list(self, status: str | None = None, verdict: str | None = None, include_skipped: bool = False) -> list[dict]:
        q, args = "SELECT id, company, title, location, score, verdict, status, url, updated_at, applied_at FROM jobs WHERE 1=1", []
        if status:
            q += " AND status=?"
            args.append(status)
        elif not include_skipped:
            q += " AND status!='Skipped'"
        if verdict:
            q += " AND verdict=?"
            args.append(verdict)
        q += " ORDER BY CASE status WHEN 'Interviewing' THEN 0 WHEN 'Online assessment' THEN 1 WHEN 'Offer' THEN 0 ELSE 2 END, score DESC, id DESC"
        return [dict(r) for r in self.db.execute(q, args)]

    def keys(self) -> set[str]:
        return {r["key"] for r in self.db.execute("SELECT key FROM jobs")}

    def search(self, text: str, min_score: int = 0, include_skipped: bool = False) -> list[dict]:
        like = f"%{text}%"
        q = ("SELECT id, company, title, location, score, verdict, status, url, updated_at, applied_at FROM jobs "
             "WHERE (title LIKE ? OR company LIKE ? OR description LIKE ? OR location LIKE ?) AND score >= ?")
        if not include_skipped:
            q += " AND status != 'Skipped'"
        q += " ORDER BY score DESC, id DESC"
        return [dict(r) for r in self.db.execute(q, (like, like, like, like, min_score))]

    def counts(self) -> dict[str, int]:
        return {r["status"]: r["n"] for r in self.db.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status")}

    @staticmethod
    def to_job(row: dict) -> Job:
        return Job(company=row["company"], title=row["title"], description=row["description"] or "",
                   url=row["url"] or "", apply_url=row["apply_url"] or "", location=row["location"] or "",
                   source=row.get("source") or "manual")
