# JobPilot

A personal job-search assistant for AI/ML roles. It finds jobs that fit your profile, explains *why* each one fits (or doesn't), tailors your resume and cover letter to the posting, pre-fills the application in your browser, and tracks every application.

**It never clicks Submit.** You review everything and submit yourself. That keeps your accounts safe (most job sites ban bots) and keeps you in control of what goes out under your name.

## What it does

| Step | What happens |
|---|---|
| **Find** | Pulls new postings from the public job-board APIs (Greenhouse, Lever, Ashby) of the companies in `config/companies.yaml`. You can also paste any job you found on LinkedIn, Handshake or YC. |
| **Check fit** | Scores 0–100 and flags dealbreakers in plain language: *doesn't sponsor visas*, *requires US citizenship or clearance*, *graduation window excludes May 2027*, *asks for 3+ years*, *senior title*. Lists the skills you have and the gaps. |
| **Tailor** | Reorders your projects by relevance, moves the skills the posting mentions to the front, rewrites the summary around the role, and adds the graduation line for new-grad roles. Outputs a one-page PDF (to upload) and a Word file (to edit), plus a cover letter. |
| **Pre-fill** | Opens the application in a real browser, fills name, email, phone, LinkedIn and standard sponsorship and authorization questions, and uploads your resume and cover letter. Consent boxes, essays and CAPTCHAs are left for you. |
| **Track** | SQLite tracker with statuses (New → Tailored → Applied → Online assessment → Interviewing → Offer / Rejected), dated history and notes. |

### The no-hallucination rule

Tailoring only **reorders and selects** what's in `config/profile.yaml`. It never invents a project, number or skill. If you turn on Claude, every AI-written sentence is checked against your profile: any number or skill that isn't in your profile gets the text rejected, and JobPilot falls back to its rule-based version. `tailoring_notes.txt` explains every change.

## Setup (about 5 minutes)

```bash
git clone <your repo> jobpilot && cd jobpilot
python -m venv .venv
# Windows: .venv\Scripts\activate     Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium          # browser used for pre-fill
cp config/profile.example.yaml config/profile.yaml   # then put your real details in profile.yaml
cp .env.example .env                 # optional: add ANTHROPIC_API_KEY for AI-written summaries and cover letters
```

`config/profile.yaml` and `.env` are git-ignored, so your contact details and API key never get committed.

Requires Python 3.10+.

## Use it

```bash
python -m jobpilot serve             # dashboard at http://127.0.0.1:5000  (easiest)
```

Or from the command line:

```bash
python -m jobpilot fetch                                   # check your companies for new jobs
python -m jobpilot add --company Quora --title "ML Engineer, New Grad" --file jd.txt --url https://...
python -m jobpilot list                                    # best fits first (skipped ones hidden; add --all)
python -m jobpilot show 12                                 # full fit report
python -m jobpilot tailor 12 [--ai]                        # resume + cover letter in output/0012_Company/
python -m jobpilot prefill 12                              # opens the form and fills it; you submit
python -m jobpilot status 12 Applied --note "via site"
```

## Make it yours

- **`config/profile.yaml`**: your master resume (start from `profile.example.yaml`). Each project has bullets, a tech stack, `themes` (used to judge relevance) and a `story` (used in cover letters). Update it when you finish a project; every future resume improves.
- **`config/companies.yaml`**: boards to watch, plus title filters. Find a company's board name in its job links (`boards.greenhouse.io/<name>`, `jobs.lever.co/<name>`, `jobs.ashbyhq.com/<name>`).
- **Scoring rules**: `jobpilot/scoring.py`. Plain regex lists (`NO_SPONSOR`, `US_ONLY`, `VOCAB`…). Add a phrase when you see a new way companies say "no sponsorship".

## Project layout

```
jobpilot/
  sources/        Greenhouse, Lever, Ashby fetchers (public APIs)
  scoring.py      fit score + dealbreaker detection
  tailor.py       resume/cover-letter tailoring + fact check
  render.py       one-page PDF and Word output
  tracker.py      SQLite tracker with history
  prefill.py      Playwright form filler (never submits)
  pipeline.py     shared actions
  cli.py, web.py  command line and Flask dashboard
config/           profile.yaml, companies.yaml
output/           tracker database and generated documents (git-ignored)
tests/            python -m unittest discover -s tests
```

## Ideas to extend it (good portfolio talking points)

- Embedding-based matching (Sentence-Transformers) in place of the keyword vocabulary
- A Gmail reader that moves statuses automatically when an interview invite or rejection arrives
- An evaluation set of real postings, labeled by hand, to measure how accurate the fit scorer is
- Scheduled daily fetch with an email digest of new Strong matches
