"""Open an application form in a real browser window and fill in the standard fields.

It NEVER clicks Submit. You review every field, answer anything it couldn't, solve any
CAPTCHA, and submit yourself. Works best on Greenhouse, Lever and Ashby forms, and fills
obvious fields (name, email, phone, LinkedIn, resume upload) on most other sites.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import Profile

# label regex -> profile value getter
TEXT_FIELDS: list[tuple[str, callable]] = [
    (r"^\s*first\s*name|given name", lambda p: p.get("first_name") or p["name"].split()[0]),
    (r"^\s*(last|family)\s*name|surname", lambda p: p.get("last_name") or p["name"].split()[-1]),
    (r"^\s*(full\s*)?name\s*\*?\s*$|^\s*legal name", lambda p: p["name"]),
    (r"e-?mail", lambda p: p["email"]),
    (r"phone|mobile", lambda p: p["phone"]),
    (r"linkedin", lambda p: p.get("linkedin", "")),
    (r"github", lambda p: p.get("github", "")),
    (r"^\s*(current\s*)?(location|city)", lambda p: p.get("location", "")),
]
# label regex -> answers key, for yes/no style questions
QUESTION_FIELDS: list[tuple[str, str]] = [
    (r"(require|need).{0,40}sponsor", "require_sponsorship"),
    (r"(legally\s+)?authori[sz]ed to work", "authorized_to_work"),
    (r"relocat", "willing_to_relocate"),
    (r"how did you hear", "how_did_you_hear"),
    (r"earliest start|start date|when can you start", "start_date"),
    (r"salary|compensation expectation", "salary"),
]


@dataclass
class PrefillReport:
    filled: list[str] = field(default_factory=list)
    uploaded: list[str] = field(default_factory=list)
    needs_you: list[str] = field(default_factory=list)


def _label_for(page, el) -> str:
    """Best-effort human label for a form control."""
    return page.evaluate(
        """(el) => {
            const byFor = el.id ? document.querySelector(`label[for="${CSS.escape(el.id)}"]`) : null;
            const wrap = el.closest('label');
            const aria = el.getAttribute('aria-label') || '';
            const lb = el.getAttribute('aria-labelledby');
            const lbEl = lb ? document.getElementById(lb) : null;
            let near = '';
            let p = el.parentElement;
            for (let i = 0; i < 4 && p && !near; i++) {
                const l = p.querySelector('label, legend, .label, [class*="label"]');
                if (l && !l.contains(el)) near = l.innerText;
                p = p.parentElement;
            }
            return (byFor?.innerText || lbEl?.innerText || wrap?.innerText || aria || near ||
                    el.getAttribute('placeholder') || el.name || '').trim();
        }""",
        el,
    )


def fill_page(page, profile: Profile, resume_path: str | None, cover_path: str | None) -> PrefillReport:
    report = PrefillReport()
    answers = profile.get("answers", {})

    for el in page.query_selector_all("input:not([type=hidden]):not([type=submit]):not([type=button]), textarea, select"):
        try:
            if not el.is_visible() and el.get_attribute("type") != "file":
                continue
            kind = (el.get_attribute("type") or el.evaluate("e => e.tagName")).lower()
            label = _label_for(page, el)
            low = label.lower()
            if kind == "file":
                if re.search(r"cover", low) and cover_path:
                    el.set_input_files(cover_path)
                    report.uploaded.append(f"Cover letter → {label or 'file field'}")
                elif re.search(r"resume|cv", low) or (resume_path and not report.uploaded):
                    if resume_path:
                        el.set_input_files(resume_path)
                        report.uploaded.append(f"Resume → {label or 'file field'}")
                continue
            if kind in ("checkbox", "radio"):
                continue  # consent boxes and multiple-choice answers are yours to choose
            if el.input_value():
                continue  # never overwrite what's already there
            done = False
            for pattern, getter in TEXT_FIELDS:
                if re.search(pattern, low):
                    value = getter(profile)
                    if value and kind != "select":
                        el.fill(value)
                        report.filled.append(f"{label[:60]} = {value}")
                    done = True
                    break
            if done:
                continue
            for pattern, key in QUESTION_FIELDS:
                if re.search(pattern, low) and answers.get(key):
                    value = answers[key]
                    if kind == "select":
                        options = el.evaluate("s => [...s.options].map(o => o.text)")
                        match = next((o for o in options if o.strip().lower().startswith(value.lower())), None)
                        if match:
                            el.select_option(label=match)
                            report.filled.append(f"{label[:60]} = {match}")
                            done = True
                    else:
                        el.fill(value)
                        report.filled.append(f"{label[:60]} = {value}")
                        done = True
                    break
            if not done and label and re.search(r"\*|required", label + (el.get_attribute("aria-required") or "")):
                report.needs_you.append(label[:80])
        except Exception as exc:  # a strange field must not stop the rest
            report.needs_you.append(f"(couldn't fill a field: {exc.__class__.__name__})")

    # Always remind you to double-check the answers that matter most.
    reminders = {"require_sponsorship": "Visa sponsorship question", "authorized_to_work": "Work authorization question",
                 "willing_to_relocate": "Relocation question"}
    for pattern, key in QUESTION_FIELDS[:3]:
        if page.get_by_text(re.compile(pattern, re.I)).count():
            report.needs_you.append(f"{reminders[key]}: confirm it says {answers.get(key, '?')}")
    return report


def prefill(url: str, profile: Profile, resume_path: str | None = None, cover_path: str | None = None,
            headless: bool = False, keep_open: bool = True, on_report=None) -> PrefillReport:
    """keep_open=True: wait in the terminal for Enter. keep_open="window": wait until you close the tab."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        # Many boards show the job first; open the form if there's an obvious Apply button.
        for name in ("Apply for this job", "Apply now", "Apply"):
            btn = page.get_by_role("button", name=re.compile(rf"^{name}$", re.I))
            link = page.get_by_role("link", name=re.compile(rf"^{name}$", re.I))
            target = btn if btn.count() else link if link.count() else None
            if target is not None and not page.query_selector("input[type=file]"):
                try:
                    target.first.click()
                    page.wait_for_timeout(1500)
                except Exception:
                    pass
                break
        report = fill_page(page, profile, resume_path, cover_path)
        if on_report:
            on_report(report)
        if keep_open == "window" and not headless:
            page.wait_for_event("close", timeout=0)  # you close the tab after submitting
        elif keep_open and not headless:
            print("\nThe form is filled as far as JobPilot can safely go.")
            print("Review every field, answer the rest, then click Submit YOURSELF in the browser.")
            input("Press Enter here when you're done to close the browser... ")
        browser.close()
    return report
