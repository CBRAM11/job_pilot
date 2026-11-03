"""Command line:  python -m jobpilot <command>

  fetch                         pull new jobs from your companies.yaml boards and score them
  add  --company C --title T --file jd.txt [--url U]   add a job you found anywhere (LinkedIn, Handshake…)
  list [--status S] [--all]     show tracked jobs, best first
  show ID                       full fit report for one job
  tailor ID [--ai] [--no-cover] make a tailored resume (+ cover letter) for that job
  prefill ID                    open the application in a browser and fill standard fields (you submit)
  status ID "Applied" [--note]  update a job's status
  serve [--port 5000]           open the dashboard in your browser
"""

from __future__ import annotations

import argparse
import sys
import textwrap

from . import pipeline
from .models import load_profile
from .tracker import STATUSES, Tracker


def _print_fit(job: dict) -> None:
    fit = job["fit"]
    print(f"\n#{job['id']}  {job['title']} — {job['company']}  ({job['location'] or 'location n/a'})")
    print(f"Fit: {job['score']}/100  {job['verdict']}   Status: {job['status']}")
    print(f"Link: {job['url'] or '-'}")
    for label, key in (("Rules it out", "blockers"), ("Watch out", "warnings"), ("Good signs", "positives")):
        for item in fit.get(key, []):
            print(f"  {label}: {item}")
    print(f"  You have: {', '.join(fit.get('matched', [])) or '-'}")
    print(f"  Gaps:     {', '.join(fit.get('missing', [])) or '-'}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jobpilot", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch")
    a = sub.add_parser("add")
    a.add_argument("--company", required=True)
    a.add_argument("--title", required=True)
    a.add_argument("--file", help="text file with the job description (or paste it when asked)")
    a.add_argument("--url", default="")
    a.add_argument("--location", default="")
    l = sub.add_parser("list")
    l.add_argument("--status", choices=STATUSES)
    l.add_argument("--all", action="store_true", help="include skipped jobs")
    s = sub.add_parser("show")
    s.add_argument("id", type=int)
    t = sub.add_parser("tailor")
    t.add_argument("id", type=int)
    t.add_argument("--ai", action="store_true", help="also use Claude for the summary and cover letter (needs ANTHROPIC_API_KEY)")
    t.add_argument("--no-cover", action="store_true")
    p = sub.add_parser("prefill")
    p.add_argument("id", type=int)
    st = sub.add_parser("status")
    st.add_argument("id", type=int)
    st.add_argument("status", choices=STATUSES)
    st.add_argument("--note", default="")
    sv = sub.add_parser("serve")
    sv.add_argument("--port", type=int, default=5000)
    args = ap.parse_args(argv)

    tracker = Tracker()
    if args.cmd == "fetch":
        res = pipeline.fetch_and_score(tracker)
        print(f"Matching jobs found: {res['found']}  (new: {res['new']}, refreshed: {res['updated']})")
        for w in res["warnings"]:
            print(f"  skipped board — {w}")
        print("Run `python -m jobpilot list` to see them.")
    elif args.cmd == "add":
        if args.file:
            desc = open(args.file, encoding="utf-8").read()
        else:
            print("Paste the job description, then press Ctrl-D (Mac/Linux) or Ctrl-Z then Enter (Windows):")
            desc = sys.stdin.read()
        job_id, _ = pipeline.add_manual(tracker, args.company, args.title, desc, args.url, args.location)
        _print_fit(tracker.get(job_id))
    elif args.cmd == "list":
        rows = tracker.list(status=args.status, include_skipped=args.all)
        if not rows:
            print("Nothing here yet. Try `fetch` or `add`.")
        for r in rows:
            print(f"#{r['id']:<4} {r['score']:>3}  {r['verdict']:<8} {r['status']:<17} {r['company'][:18]:<18} {r['title'][:60]}")
    elif args.cmd == "show":
        _print_fit(tracker.get(args.id))
    elif args.cmd == "tailor":
        res = pipeline.tailor_job(tracker, args.id, use_llm=args.ai, with_cover=not args.no_cover)
        print(f"Saved to {res['folder']}")
        for n in res["notes"]:
            print(textwrap.fill(n, 100, initial_indent="  • ", subsequent_indent="    "))
    elif args.cmd == "prefill":
        from .prefill import prefill

        job = tracker.get(args.id)
        if not job.get("resume_pdf"):
            print("Tailoring a resume first…")
            pipeline.tailor_job(tracker, args.id)
            job = tracker.get(args.id)
        rep = prefill(job["apply_url"] or job["url"], load_profile(), job.get("resume_pdf"), job.get("cover_pdf"))
        print("Filled:", *rep.filled, sep="\n  ")
        print("Uploaded:", *rep.uploaded, sep="\n  ")
        print("Needs you:", *rep.needs_you, sep="\n  ")
        print(f"\nAfter you submit: python -m jobpilot status {args.id} Applied")
    elif args.cmd == "status":
        tracker.set_status(args.id, args.status, args.note)
        print(f"#{args.id} → {args.status}")
    elif args.cmd == "serve":
        from .web import create_app

        print(f"Dashboard: http://127.0.0.1:{args.port}")
        create_app(tracker).run(host="127.0.0.1", port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
