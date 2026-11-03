"""Tailor the resume and cover letter to one job.

The rule: tailoring may REORDER and SELECT what is in profile.yaml, and rewrite the summary,
but it never adds a project, number, or skill that the profile doesn't contain. Any text
written by the AI model is checked against the profile and thrown away if it adds facts.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from datetime import date

from . import llm
from .models import Job, Profile
from .scoring import NEW_GRAD, VOCAB, jd_skills, profile_skills

# Summary phrases, each backed by a real project in the profile. Key = canonical skill.
FOCUS_PHRASES: list[tuple[set[str], str]] = [
    ({"LLMs", "RAG", "Vector search"}, "building retrieval-augmented generation (RAG) pipelines and evaluating LLM output"),
    ({"Transformers", "NLP", "Fine-tuning", "Hugging Face"}, "fine-tuning transformer models for NLP"),
    ({"Machine learning", "scikit-learn", "Feature engineering", "Statistics", "Data analysis"}, "building predictive models with scikit-learn and feature engineering"),
    ({"Model evaluation"}, "evaluating models rigorously through baselines, cross-validation, and failure analysis"),
    ({"Docker", "Deployment", "Monitoring"}, "deploying models with Docker and Prometheus/Grafana monitoring"),
    ({"Explainability", "Fairness"}, "making models explainable and fair with SHAP and perturbation analysis"),
    ({"Computer vision"}, "building real-time computer vision pipelines with YOLOv8 and OpenCV"),
    ({"Deep learning", "PyTorch"}, "training deep learning models in PyTorch"),
    ({"Adversarial robustness", "Privacy / security"}, "defending models against privacy and adversarial attacks"),
]
TOOL_ORDER = ["Python", "PyTorch", "Hugging Face", "scikit-learn", "Docker", "SQL", "Java", "TensorFlow", "Git"]
TOOL_LABEL = {"Hugging Face": "Hugging Face"}


@dataclass
class TailoredResume:
    profile: dict
    summary: str
    show_grad_line: bool
    project_order: list[str]
    notes: list[str] = field(default_factory=list)  # human-readable list of what changed and why


def _count_hits(text: str, skills: list[str]) -> int:
    """Relevance = how many distinct signals of the asked skills appear in the project."""
    hits = 0
    for s in skills:
        flags = 0 if s in ("C", "Go", "R") else re.I
        hits += sum(1 for p in VOCAB[s] if re.search(p, text, flags))
    return hits


def rank_projects(profile: Profile, asked: list[str]) -> list[tuple[str, int]]:
    ranked = []
    for idx, p in enumerate(profile.get("projects", [])):
        text = " ".join([p["name"], " ".join(p.get("tech", [])), " ".join(p.get("themes", []))]
                        + [b["text"] for b in p.get("bullets", [])])
        ranked.append((p["id"], _count_hits(text, asked), -idx))
    ranked.sort(key=lambda t: (t[1], t[2]), reverse=True)
    return [(pid, hits) for pid, hits, _ in ranked]


def order_skills(skills: dict[str, list[str]], jd_text: str) -> dict[str, list[str]]:
    jd = jd_text.lower()

    def mentioned(item: str) -> bool:
        core = re.split(r"[ (/]", item.lower())[0]
        return len(core) > 1 and re.search(rf"(?<![a-z]){re.escape(core)}(?![a-z])", jd) is not None

    out = {}
    for cat, items in skills.items():
        out[cat] = [i for i in items if mentioned(i)] + [i for i in items if not mentioned(i)]
    return out


def rule_summary(profile: Profile, asked: list[str]) -> str | None:
    have = profile_skills(profile)
    wanted = [s for s in asked if s in have]
    scored = []
    for i, (keys, phrase) in enumerate(FOCUS_PHRASES):
        hits = len(keys & set(wanted))
        if hits:
            scored.append((hits, -i, phrase))
    scored.sort(reverse=True)
    phrases = [p for _, _, p in scored[:3]]
    if len(phrases) < 2:
        return None
    tools = [t for t in TOOL_ORDER if t in wanted] + [t for t in TOOL_ORDER if t not in wanted]
    tools = tools[:5]
    focus = ", ".join(phrases[:-1]) + ", and " + phrases[-1]
    return (f"AI and ML Engineer experienced in {focus}. "
            f"Proficient in {', '.join(tools[:-1])}, and {tools[-1]}.")


# ---------- checking AI-written text ----------

_NUM = re.compile(r"\d+(?:[.,]\d+)?%?")


def unsupported_claims(text: str, profile: Profile, allow_about_employer: bool = False) -> list[str]:
    """Return problems if text claims numbers or skills the profile doesn't support."""
    corpus = profile.corpus()
    have = profile_skills(profile)
    problems = []
    for n in _NUM.findall(text):
        if n.rstrip("%") not in corpus and n not in {"2026", "2027"}:
            problems.append(f"number not in profile: {n}")
    for sent in re.split(r"(?<=[.!?])\s+", text):
        extra = [s for s in jd_skills(sent) if s not in have]
        if not extra:
            continue
        about_them = allow_about_employer and re.search(r"\byour\b|\byou\b|\bthe team\b|\bthe role\b", sent, re.I)
        if not about_them:
            problems.append(f"claims skills not in profile ({', '.join(extra)}): {sent[:100]}")
    return problems


def llm_summary(profile: Profile, job: Job) -> tuple[str | None, str]:
    prompt = f"""Write a resume summary (2 sentences, at most 70 words) for this candidate, tailored to the job.
Start with "AI and ML Engineer". Use ONLY skills, tools, and facts that appear in the candidate profile.
Do not mention years of experience, the company name, or numbers. Do not invent anything.
Return only the summary text.

<candidate_profile>
{profile.corpus()}
</candidate_profile>

<job title="{job.title}" company="{job.company}">
{job.description[:6000]}
</job>"""
    text = llm.complete(prompt, max_tokens=300)
    if not text:
        return None, "AI summary skipped (no API key or request failed)."
    text = text.strip().strip('"')
    problems = unsupported_claims(text, profile)
    if problems or not text.startswith("AI and ML Engineer") or len(text.split()) > 85:
        return None, "AI summary rejected by fact check: " + "; ".join(problems[:3] or ["format"])
    return text, "Summary written by AI and passed the fact check."


def tailor_resume(job: Job, profile: Profile, use_llm: bool = False) -> TailoredResume:
    text = f"{job.title}\n{job.description}"
    asked = jd_skills(text)
    data = copy.deepcopy(profile.data)
    notes: list[str] = []

    ranked = rank_projects(profile, asked)
    order = [pid for pid, _ in ranked]
    by_id = {p["id"]: p for p in data["projects"]}
    data["projects"] = [by_id[pid] for pid in order]
    if order != [p["id"] for p in profile["projects"]]:
        notes.append("Projects reordered by relevance: " + ", ".join(
            f"{by_id[pid]['name'].split(':')[0]} ({hits} match{'' if hits == 1 else 'es'})"
            for pid, hits in ranked))
    else:
        notes.append("Project order unchanged (already best for this role).")

    data["skills"] = order_skills(data["skills"], text)
    notes.append("Skills the posting mentions moved to the front of each line.")

    summary = None
    if use_llm:
        summary, msg = llm_summary(profile, job)
        notes.append(msg)
    if not summary:
        summary = rule_summary(profile, asked)
        if summary:
            notes.append("Summary rebuilt around the posting's focus: " + summary)
    if not summary:
        summary = profile["summary"]
        notes.append("Kept your default summary (posting gave too little to tailor on).")

    show_grad = bool(re.search("|".join(NEW_GRAD + [r"graduat", r"\b20\d\d start\b"]), text, re.I))
    if show_grad:
        notes.append("Added 'Expected graduation / Available to start' line because the role targets graduates.")
    return TailoredResume(data, summary, show_grad, order, notes)


# ---------- cover letter ----------

def rule_cover_letter(job: Job, profile: Profile, tr: TailoredResume) -> str:
    projects = {p["id"]: p for p in profile["projects"]}
    top = [projects[pid] for pid in tr.project_order[:2]]
    asked = jd_skills(f"{job.title}\n{job.description}")
    research = profile.get("research", [])
    use_research = research and {"LLMs", "RAG", "Vector search", "Model evaluation"} & set(asked)
    edu = profile["education"][0]
    grad_year = profile.get("graduation", "2027-05").split("-")[0]
    company = job.company.strip().title() if job.company.islower() else job.company.strip()
    paras = [
        f"Dear {company} Hiring Team,",
        f"I’m applying for the {job.title} role. I’m completing an {edu['degree']} at the "
        f"{edu['school']} (graduating in May {grad_year}), and this role lines up closely with the work "
        f"I’ve focused on.",
        top[0]["story"].strip(),
        (research[0]["story"].strip() if use_research else top[1]["story"].strip()),
        f"I’d welcome the chance to bring this to {company}. I can start full-time in May {grad_year}"
        + (" and am open to relocating." if profile.get("willing_to_relocate") else "."),
        "Thank you for your consideration.",
        f"Sincerely,\n{profile['name']}",
    ]
    return "\n\n".join(paras)


def llm_cover_letter(job: Job, profile: Profile) -> tuple[str | None, str]:
    prompt = f"""Write a cover letter (250-330 words) from this candidate for this job.
Use ONLY facts, numbers, projects, and skills from the candidate profile; do not invent anything.
You may describe the company's needs using the job posting. Plain, specific, no clichés.
Do not mention visa or sponsorship. Start with "Dear <Company> Hiring Team," and end with
"Sincerely," and the candidate's name. Return only the letter.

<candidate_profile>
{profile.corpus()}
</candidate_profile>

<job title="{job.title}" company="{job.company}">
{job.description[:6000]}
</job>"""
    text = llm.complete(prompt, max_tokens=900)
    if not text:
        return None, "AI cover letter skipped (no API key or request failed)."
    problems = unsupported_claims(text, profile, allow_about_employer=True)
    if problems:
        return None, "AI cover letter rejected by fact check: " + "; ".join(problems[:3])
    return text.strip(), "Cover letter written by AI and passed the fact check."


def cover_letter(job: Job, profile: Profile, tr: TailoredResume, use_llm: bool = False) -> tuple[str, str]:
    if use_llm:
        text, msg = llm_cover_letter(job, profile)
        if text:
            return text, msg
        fallback = rule_cover_letter(job, profile, tr)
        return fallback, msg + " Used the template letter instead."
    return rule_cover_letter(job, profile, tr), "Template cover letter built from your project stories."


def today() -> str:
    d = date.today()
    return f"{d:%B} {d.day}, {d.year}"
