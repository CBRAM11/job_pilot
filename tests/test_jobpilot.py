"""Run with:  python -m unittest discover -s tests -v   (or pytest)"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jobpilot import render
from jobpilot.models import Job, html_to_text, load_profile
from jobpilot.scoring import score_job
from jobpilot.sources import ashby, greenhouse, lever, title_matches
from jobpilot.tailor import rule_summary, tailor_resume, unsupported_claims
from jobpilot.tracker import Tracker

P = load_profile()


def job(title, desc, company="Acme"):
    return Job(company=company, title=title, description=desc)


class ScoringTests(unittest.TestCase):
    def test_no_sponsorship_is_skipped(self):
        for text in [
            "We are unable to sponsor or take over sponsorship of an employment Visa at this time.",
            "It is not available for any work sponsorship.",
            "Employment eligibility is required as the company will not pursue visa sponsorship for these positions.",
            "Candidates must be authorized to work in the U.S. without the need for visa sponsorship.",
        ]:
            r = score_job(job("ML Engineer, New Grad", f"Python, PyTorch. {text}"), P)
            self.assertEqual(r.verdict, "Skip", text)

    def test_clearance_is_skipped(self):
        r = score_job(job("AI/ML Engineer", "Python. Active TS/SCI security clearance required."), P)
        self.assertEqual(r.verdict, "Skip")

    def test_grad_window_inside(self):
        r = score_job(job("2027 University Graduate - Machine Learning Engineer",
                          "MS with an expected graduation date of December 2026 – June 2027. Python, scikit-learn, SQL."), P)
        self.assertNotEqual(r.verdict, "Skip")
        self.assertTrue(any("inside the window" in x for x in r.positives))

    def test_grad_window_outside(self):
        r = score_job(job("Software Engineer New Grad", "A 2025 or 2026 graduate with or pursuing a B.S. Python."), P)
        self.assertEqual(r.verdict, "Skip")

    def test_wrong_year_in_title(self):
        r = score_job(job("ML Engineer Graduate - 2026 Start", "Python, PyTorch."), P)
        self.assertEqual(r.verdict, "Skip")

    def test_senior_and_years(self):
        self.assertEqual(score_job(job("Senior ML Engineer", "Python"), P).verdict, "Skip")
        self.assertEqual(score_job(job("ML Engineer", "5+ years of experience building ML systems. Python."), P).verdict, "Skip")
        w = score_job(job("ML Engineer", "2+ years of experience with Python and PyTorch."), P)
        self.assertTrue(w.warnings)
        for ceiling in ["Up to 2 years of internship or professional experience in software.",
                        "Less than 2 years of engineering experience.", "0-2 years of experience."]:
            r = score_job(job("AI Engineer", f"Python. {ceiling}"), P)
            self.assertFalse([w for w in r.warnings + r.blockers if "years" in w], ceiling)

    def test_unrelated_roles_are_not_strong(self):
        for title, desc in [
            ("AI Enablement Analyst", "Help teams adopt AI tools. Excel, SQL, Python. New grads welcome."),
            ("AI Newsroom Developer Fellow", "Build tools for journalists using LLMs. JavaScript, Python. Recent graduates."),
            ("Software Engineer - Data Fulfillment", "Build data pipelines in Java and SQL on AWS. Git. New grads welcome."),
            ("Data Annotator - AI Training", "Label images for AI models. Python helpful."),
        ]:
            self.assertIn(score_job(job(title, desc), P).verdict, ("Weak", "Possible", "Skip"), title)

    def test_real_ml_roles_rank_above_unrelated(self):
        ml = score_job(job("Machine Learning Engineer, New Grad",
                           "Train deep learning models in PyTorch, fine-tune transformers, build RAG, evaluate LLMs. Docker."), P)
        analyst = score_job(job("AI Enablement Analyst", "Python and SQL. Help teams adopt AI."), P)
        self.assertEqual(ml.verdict, "Strong")
        self.assertGreater(ml.score, analyst.score + 30)

    def test_core_skill_gaps_cap_at_possible(self):
        r = score_job(job("Research Engineer - Inference", "Optimize LLM inference with vLLM, CUDA, Triton kernels; PyTorch; distributed training."), P)
        self.assertNotEqual(r.verdict, "Strong")

    def test_skill_match_and_gaps(self):
        r = score_job(job("AI Engineer", "Python, PyTorch, RAG, Kubernetes, AWS. New grads welcome."), P)
        self.assertIn("RAG", r.matched)
        self.assertIn("AWS", r.missing)
        self.assertIn(r.verdict, ("Strong", "Possible"))


class TailorTests(unittest.TestCase):
    def test_projects_reordered_for_cv(self):
        tr = tailor_resume(job("Computer Vision Engineer", "Object detection with YOLO and OpenCV, real-time video."), P)
        self.assertEqual(tr.project_order[0], "traffic")

    def test_projects_reordered_for_llm(self):
        tr = tailor_resume(job("NLP Engineer", "Fine-tune transformers with Hugging Face for NLP and LLM apps."), P)
        self.assertEqual(tr.project_order[0], "fakenews")

    def test_nothing_added_or_dropped(self):
        tr = tailor_resume(job("ML Engineer", "Python, Docker, monitoring"), P)
        self.assertEqual(sorted(p["id"] for p in tr.profile["projects"]), sorted(p["id"] for p in P["projects"]))
        for cat, items in tr.profile["skills"].items():
            self.assertEqual(sorted(items), sorted(P["skills"][cat]))
        self.assertEqual(unsupported_claims(tr.summary, P), [])

    def test_rule_summary_has_no_new_claims(self):
        s = rule_summary(P, ["LLMs", "RAG", "Docker", "Monitoring", "Python"])
        self.assertTrue(s.startswith("AI and ML Engineer"))
        self.assertEqual(unsupported_claims(s, P), [])

    def test_fact_check_catches_inventions(self):
        self.assertTrue(unsupported_claims("Built Kubernetes clusters on AWS serving 10M users.", P))
        self.assertTrue(unsupported_claims("Improved accuracy to 97.3%.", P))
        self.assertEqual(unsupported_claims("Reached 99.80% accuracy with PyTorch.", P), [])

    def test_ai_text_is_fact_checked(self):
        from unittest import mock
        from jobpilot import tailor
        j = job("ML Engineer", "Python, RAG")
        good = "AI and ML Engineer who builds RAG pipelines and fine-tunes transformers with PyTorch and Hugging Face."
        bad = "AI and ML Engineer with 3 years of Kubernetes and AWS experience."
        with mock.patch.object(tailor.llm, "complete", return_value=good):
            self.assertEqual(tailor.tailor_resume(j, P, use_llm=True).summary, good)
        with mock.patch.object(tailor.llm, "complete", return_value=bad):
            self.assertNotEqual(tailor.tailor_resume(j, P, use_llm=True).summary, bad)
        with mock.patch.object(tailor.llm, "complete", return_value=None):
            self.assertTrue(tailor.tailor_resume(j, P, use_llm=True).summary.startswith("AI and ML Engineer"))

    def test_grad_line_only_for_grad_roles(self):
        self.assertTrue(tailor_resume(job("ML Engineer, New Grad", "Python"), P).show_grad_line)
        self.assertFalse(tailor_resume(job("ML Engineer", "Python"), P).show_grad_line)

    def test_render_one_page(self):
        tr = tailor_resume(job("ML Engineer, New Grad", "Python, PyTorch, RAG, Docker"), P)
        with tempfile.TemporaryDirectory() as d:
            pages = render.render_pdf(tr, Path(d) / "r.pdf")
            render.render_docx(tr, Path(d) / "r.docx")
            self.assertEqual(pages, 1)
            self.assertTrue((Path(d) / "r.docx").stat().st_size > 5000)


class SourceTests(unittest.TestCase):
    def test_greenhouse(self):
        jobs = greenhouse.parse("acme", {"jobs": [{"id": 1, "title": "ML Engineer", "absolute_url": "https://x/1",
                                                   "location": {"name": "NYC"}, "content": "&lt;p&gt;Use &lt;b&gt;PyTorch&lt;/b&gt;&lt;/p&gt;"}]})
        self.assertEqual(jobs[0].location, "NYC")
        self.assertIn("PyTorch", jobs[0].description)
        self.assertNotIn("<", jobs[0].description)

    def test_lever(self):
        jobs = lever.parse("acme", [{"id": "a", "text": "AI Engineer", "hostedUrl": "https://h", "applyUrl": "https://h/apply",
                                     "descriptionPlain": "Build agents.", "lists": [{"text": "Requirements", "content": "<li>Python</li>"}],
                                     "categories": {"location": "Remote"}}])
        self.assertEqual(jobs[0].apply_url, "https://h/apply")
        self.assertIn("Python", jobs[0].description)

    def test_ashby(self):
        jobs = ashby.parse("acme", {"jobs": [{"id": "z", "title": "ML Engineer", "jobUrl": "https://j", "applyUrl": "https://j/application",
                                              "descriptionPlain": "RAG", "location": "SF", "isListed": True},
                                             {"id": "y", "title": "Hidden", "isListed": False}]})
        self.assertEqual(len(jobs), 1)

    def test_title_filter(self):
        inc, exc = ["machine learning", "ai engineer"], ["senior", "intern"]
        self.assertTrue(title_matches("Machine Learning Engineer, New Grad", inc, exc))
        self.assertFalse(title_matches("Senior Machine Learning Engineer", inc, exc))
        self.assertFalse(title_matches("Account Executive", inc, exc))
        self.assertFalse(title_matches("Software Engineer - Data Fulfillment", ["llm"], []))
        self.assertTrue(title_matches("LLM Engineer", ["llm"], []))
        self.assertFalse(title_matches("Machine Learning Engineer II", inc, ["ii"]))
        self.assertTrue(title_matches("Machine Learning Engineer, Infrastructure", inc, ["ii"]))

    def test_html_to_text(self):
        self.assertEqual(html_to_text("<ul><li>A</li><li>B</li></ul>").split(), ["-", "A", "-", "B"])


class TrackerTests(unittest.TestCase):
    def test_flow(self):
        with tempfile.TemporaryDirectory() as d:
            with Tracker(Path(d) / "t.db") as t:
                self._flow(t)

    def _flow(self, t):
        j = job("ML Engineer, New Grad", "Python, PyTorch")
        jid, new = t.upsert(j, score_job(j, P))
        self.assertTrue(new)
        _, again = t.upsert(j, score_job(j, P))
        self.assertFalse(again)
        t.set_status(jid, "Applied", "via site")
        row = t.get(jid)
        self.assertEqual(row["status"], "Applied")
        self.assertTrue(row["applied_at"])
        self.assertEqual([h["status"] for h in row["history"]], ["New", "Applied"])
        with self.assertRaises(ValueError):
            t.set_status(jid, "Hired?")

    def test_skipped_hidden_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            with Tracker(Path(d) / "t.db") as t:
                j = job("Senior ML Engineer", "Python")
                t.upsert(j, score_job(j, P))
                self.assertEqual(t.list(), [])
                self.assertEqual(len(t.list(include_skipped=True)), 1)


class DiscoverTests(unittest.TestCase):
    """Discovery with the network faked: real code paths, canned API responses."""

    class FakeResp:
        def __init__(self, data):
            self._d = data
        def json(self):
            return self._d
        def raise_for_status(self):
            pass

    def session(self, routes):
        outer = self
        class S:
            headers = {}
            def get(self, url, **kw):
                for key, data in routes.items():
                    if key in url:
                        return outer.FakeResp(data)
                raise ConnectionError(url)
        return S()

    def test_newgrad_select_and_score(self):
        import time
        from jobpilot.sources import newgrad
        now = time.time()
        listings = [
            {"id": "1", "company_name": "Acme AI", "title": "Machine Learning Engineer, New Grad", "active": True, "is_visible": True,
             "date_posted": now, "url": "https://job-boards.greenhouse.io/acme/jobs/111", "locations": ["San Francisco, CA"],
             "sponsorship": "Other", "degrees": ["Master's"]},
            {"id": "2", "company_name": "Gov Co", "title": "AI Engineer", "active": True, "is_visible": True, "date_posted": now,
             "url": "https://example.com/x", "locations": ["Arlington, VA"], "sponsorship": "U.S. Citizenship is Required", "degrees": []},
            {"id": "3", "company_name": "UK Co", "title": "Machine Learning Engineer", "active": True, "is_visible": True,
             "date_posted": now, "url": "https://example.com/y", "locations": ["London, UK"], "sponsorship": "Other", "degrees": []},
            {"id": "4", "company_name": "Old Co", "title": "Machine Learning Engineer", "active": True, "is_visible": True,
             "date_posted": now - 90 * 86400, "url": "https://example.com/z", "locations": ["Austin, TX"], "sponsorship": "Other", "degrees": []},
            {"id": "5", "company_name": "Label Co", "title": "Data Annotator - AI Training", "active": True, "is_visible": True,
             "date_posted": now, "url": "https://example.com/w", "locations": ["Remote in USA"], "sponsorship": "Other", "degrees": []},
        ]
        jd = "We build LLM products. Python, PyTorch, RAG, Docker. New grads welcome; graduating December 2026 - June 2027. " * 3
        s = self.session({"listings.json": listings,
                          "boards-api.greenhouse.io/v1/boards/acme/jobs/111": {"title": "ML Engineer, New Grad", "content": jd,
                                                                               "location": {"name": "San Francisco, CA"}}})
        from jobpilot.models import load_companies
        cfg = load_companies()
        jobs, stats = newgrad.fetch(cfg, s, known_keys=set())
        self.assertEqual(sorted(j.company for j in jobs), ["Acme AI", "Gov Co"])
        self.assertEqual(stats["full_descriptions"], 1)
        by = {j.company: score_job(j, P) for j in jobs}
        self.assertEqual(by["Gov Co"].verdict, "Skip")          # citizenship flag from the list
        self.assertIn(by["Acme AI"].verdict, ("Strong", "Possible"))
        # already-tracked jobs are not fetched again
        again, stats2 = newgrad.fetch(cfg, s, known_keys={"newgrad:1", "newgrad:2"})
        self.assertEqual(again, [])
        self.assertEqual(stats2["already_tracked"], 2)

    def test_detail_resolvers(self):
        from jobpilot.sources import details
        long = "<p>" + "Build ML systems with Python and PyTorch. " * 10 + "</p>"
        s = self.session({
            "api.lever.co/v0/postings/acme/abc": {"text": "AI Engineer", "descriptionPlain": long, "lists": [], "categories": {"location": "NYC"}},
            "/wday/cxs/nvidia/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/ML-Engineer_JR1":
                {"jobPostingInfo": {"title": "ML Engineer", "jobDescription": long, "location": "Santa Clara"}},
            "apply.workable.com/api/v2/accounts/raydar/jobs/ABC123": {"title": "ML Engineer", "description": long, "location": {"city": "SF"}},
            "api.smartrecruiters.com/v1/companies/LinkedIn3/postings/744000": {"name": "Data Scientist",
                "jobAd": {"sections": {"jobDescription": {"text": long}}}, "location": {"city": "Mountain View"}},
            "api.ashbyhq.com/posting-api/job-board/quora": {"jobs": [{"id": "q1", "title": "MLE New Grad", "descriptionPlain": long, "location": "Remote"}]},
        })
        cases = {
            "https://jobs.lever.co/acme/abc/apply": "AI Engineer",
            "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/ML-Engineer_JR1": "ML Engineer",
            "https://apply.workable.com/raydar/j/ABC123/": "ML Engineer",
            "https://jobs.smartrecruiters.com/LinkedIn3/744000-data-scientist": "Data Scientist",
            "https://jobs.ashbyhq.com/quora/q1/application": "MLE New Grad",
        }
        for url, title in cases.items():
            got = details.fetch_description(url, s)
            self.assertIsNotNone(got, url)
            self.assertEqual(got[0], title)
            self.assertIn("PyTorch", got[1])
        self.assertIsNone(details.fetch_description("https://careers.example.com/job/1", s))

    def test_hn_parse(self):
        from jobpilot.sources import hn
        c = {"id": 42, "text": "Acme Robotics | Machine Learning Engineer | San Francisco, CA | ONSITE | VISA<p>"
                               "We train vision models in PyTorch for warehouse robots. New grads welcome. "
                               "We sponsor visas. Apply: https://acme.example/jobs</p>"}
        j = hn.parse_comment(c)
        self.assertEqual(j.company, "Acme Robotics")
        self.assertEqual(j.title, "Machine Learning Engineer")
        self.assertEqual(j.apply_url, "https://acme.example/jobs")
        self.assertIsNone(hn.parse_comment({"id": 1, "text": "Bakery | Pastry chef | Paris<p>" + "Croissants. " * 20}))


FORM = """<!doctype html><form onsubmit="document.body.dataset.sent='yes';return false">
<label for=a>First Name *</label><input id=a><label for=b>Last Name *</label><input id=b>
<label for=c>Email *</label><input id=c type=email><label for=r>Resume/CV *</label><input id=r type=file>
<label for=s>Will you now or in the future require sponsorship? *</label>
<select id=s><option value="">Select</option><option>Yes</option><option>No</option></select>
<label><input type=checkbox id=k> I agree</label><button>Submit Application</button></form>"""


class PrefillTests(unittest.TestCase):
    def test_fills_but_never_submits(self):
        try:
            from playwright.sync_api import sync_playwright
            from jobpilot.prefill import fill_page
        except ImportError:
            self.skipTest("playwright not installed")
        with tempfile.TemporaryDirectory() as d:
            form, pdf = Path(d) / "f.html", Path(d) / "r.pdf"
            form.write_text(FORM)
            pdf.write_bytes(b"%PDF-1.4\n")
            try:
                with sync_playwright() as pw:
                    b = pw.chromium.launch(headless=True)
                    page = b.new_page()
                    page.goto(form.as_uri())
                    fill_page(page, P, str(pdf), None)
                    self.assertEqual(page.input_value("#a"), "Chathur Buja Ram")
                    self.assertEqual(page.input_value("#b"), "Bathi")
                    self.assertEqual(page.input_value("#s"), "Yes")
                    self.assertEqual(page.evaluate("document.querySelector('#r').files.length"), 1)
                    self.assertFalse(page.is_checked("#k"))
                    self.assertIsNone(page.evaluate("document.body.dataset.sent"))
                    b.close()
            except Exception as exc:
                if "Executable doesn't exist" in str(exc):
                    self.skipTest("run `playwright install chromium` first")
                raise


if __name__ == "__main__":
    unittest.main()
