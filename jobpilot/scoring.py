"""Decide how well a job fits the profile, and flag anything that rules it out.

Every rule here is plain, readable regex so you can see exactly why a job was scored
the way it was. Nothing is sent to an AI model at this stage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import Job, Profile

# Canonical skill -> patterns that mean the same thing in a job description.
VOCAB: dict[str, list[str]] = {
    "Python": [r"\bpython\b"],
    "Java": [r"\bjava\b(?!script)"],
    "C++": [r"c\+\+"],
    "C": [r"\bC\b(?![+#])"],
    "Go": [r"\bgolang\b", r"\bGo\b(?=[,/ )])"],
    "Scala": [r"\bscala\b"],
    "TypeScript": [r"\btypescript\b"],
    "SQL": [r"\bsql\b", r"\brelational databases?\b"],
    "PyTorch": [r"\bpytorch\b", r"\btorch\b"],
    "TensorFlow": [r"\btensorflow\b"],
    "Keras": [r"\bkeras\b"],
    "JAX": [r"\bjax\b"],
    "scikit-learn": [r"scikit[- ]?learn", r"\bsklearn\b"],
    "Hugging Face": [r"hugging ?face", r"\btransformers library\b"],
    "Transformers": [r"\btransformers?\b(?! library)", r"\bbert\b", r"\bdistilbert\b"],
    "LLMs": [r"\bllms?\b", r"large language models?", r"\bgenai\b", r"generative ai", r"\bgpt\b"],
    "RAG": [r"\brag\b", r"retrieval[- ]augmented"],
    "AI agents": [r"\bagents?\b", r"\bagentic\b"],
    "Vector search": [r"\bfaiss\b", r"vector (databases?|search|store)", r"\bembeddings?\b"],
    "Fine-tuning": [r"fine[- ]?tun"],
    "NLP": [r"\bnlp\b", r"natural language"],
    "Computer vision": [r"computer vision", r"\bopencv\b", r"\byolo", r"object detection", r"image classification"],
    "Deep learning": [r"deep learning", r"neural networks?", r"\bcnns?\b"],
    "Machine learning": [r"machine learning", r"\bml\b"],
    "Statistics": [r"\bstatistic", r"\bprobability\b", r"hypothesis test"],
    "Model evaluation": [r"\bevals?\b", r"\bevaluation\b", r"\bbenchmark", r"cross[- ]validation", r"a/b test"],
    "Explainability": [r"explainab", r"interpretab", r"\bshap\b"],
    "Feature engineering": [r"feature engineering", r"\bfeatures?\b(?= (store|pipeline))"],
    "Data analysis": [r"\bpandas\b", r"\bnumpy\b", r"data analysis", r"exploratory data"],
    "Docker": [r"\bdocker\b", r"\bcontaineri[sz]"],
    "Monitoring": [r"\bmonitoring\b", r"\bprometheus\b", r"\bgrafana\b", r"observability"],
    "Git": [r"\bgit\b", r"version control", r"\bgithub\b"],
    "Deployment": [r"\bdeploy", r"\bproduction\b", r"\bmlops\b"],
    "Recommendation systems": [r"recommend", r"\branking\b", r"\bpersonali[sz]"],
    "Adversarial robustness": [r"adversarial", r"robustness"],
    "Privacy / security": [r"\bprivacy\b", r"membership inference"],
    "Fairness": [r"\bfairness\b", r"\bbias\b"],
    # Common asks you may not have yet, so they show up as gaps:
    "AWS": [r"\baws\b", r"amazon web services"],
    "GCP": [r"\bgcp\b", r"google cloud"],
    "Azure": [r"\bazure\b"],
    "Kubernetes": [r"\bkubernetes\b", r"\bk8s\b", r"\beks\b"],
    "Spark": [r"\bspark\b", r"\bpyspark\b"],
    "Airflow": [r"\bairflow\b"],
    "Ray": [r"\bray\b(?= (train|serve|tune|cluster))", r"\bray\b,"],
    "CUDA / GPU programming": [r"\bcuda\b", r"\btriton\b", r"gpu programming", r"\bkernels?\b"],
    "LLM serving (vLLM etc.)": [r"\bvllm\b", r"\bsglang\b", r"tensorrt", r"inference engines?"],
    "Distributed training": [r"\bfsdp\b", r"multi[- ]gpu", r"multi[- ]node", r"distributed training", r"deepspeed", r"megatron"],
    "Reinforcement learning": [r"reinforcement learning", r"\brlhf\b", r"\bgrpo\b", r"\bppo\b"],
    "Distributed systems": [r"distributed systems?", r"microservices?"],
    "LangChain": [r"\blangchain\b", r"\bllamaindex\b"],
    "R": [r"\bR\b(?=[,/ )])"],
}

# Which canonical skills the profile can claim. Derived from the profile text, then
# patched for skills the profile shows under a different name.
PROFILE_ALIASES = {
    "Transformers": ["distilbert", "bert", "transformer"],
    "LLMs": ["llm", "ollama"],
    "AI agents": [],
    "Vector search": ["faiss", "sentence-transformers", "embeddings"],
    "Fine-tuning": ["fine-tun"],
    "Computer vision": ["yolov8", "opencv", "image classifiers"],
    "Deep learning": ["cnns", "deep learning", "wideresnet"],
    "Machine learning": ["machine learning"],
    "Model evaluation": ["cross-validation", "evaluation pipeline", "benchmarked", "baseline"],
    "Explainability": ["shap", "explainab"],
    "Feature engineering": ["engineering features"],
    "Data analysis": ["pandas", "numpy", "exploratory data analysis"],
    "Monitoring": ["prometheus", "grafana", "monitoring"],
    "Deployment": ["dockerized", "deploy", "served through"],
    "Recommendation systems": ["recommend"],
    "Adversarial robustness": ["adversarial"],
    "Privacy / security": ["membership-inference", "membership inference", "privacy"],
    "Statistics": [],
}

NO_SPONSOR = [
    r"(unable|not able|cannot|can't|can ?not|won't|will not|do not|does not|don't)\s+(to\s+)?(offer|provide|pursue|support|sponsor)[^.]{0,40}sponsor",
    r"(unable|not able|cannot|can ?not)\s+to\s+sponsor",
    r"not (be )?(available|eligible) for (any )?(work |visa )?sponsorship",
    r"(no|without) (visa |immigration )?sponsorship (is )?(available|offered|provided)",
    r"sponsorship (is )?not (available|offered|provided)",
    r"authori[sz]ed to work.{0,80}?without (the need for |requiring |needing |current or future )?(visa |employer |immigration )?sponsorship",
    r"not (able|in a position) to (sponsor|take over sponsorship)",
]
US_ONLY = [
    r"security clearance",
    r"\bts/sci\b",
    r"must be (a )?u\.?s\.? citizens?",
    r"u\.?s\.? citizenship (is )?required",
    r"requires? u\.?s\.? citizenship",
    r"active (secret|top secret)",
    r"public trust",
]
SPONSOR_OK = [r"will sponsor", r"visa sponsorship (is )?available", r"sponsorship (is )?available", r"we sponsor", r"can help with that"]
NEW_GRAD = [r"new grad", r"new college grad", r"university grad", r"recent grad", r"early career", r"entry[- ]level", r"campus", r"\b0\s*[-–]\s*[12]\+? years"]
SENIOR_TITLE = re.compile(r"\b(senior|sr\.?|staff|principal|lead|manager|director|head of|ii|iii|iv)\b", re.I)

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
SEASONS = {"winter": 12, "spring": 5, "summer": 8, "fall": 12, "autumn": 12}


@dataclass
class FitResult:
    score: int
    verdict: str  # Strong / Possible / Weak / Skip
    matched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    positives: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _search_any(patterns: list[str], text: str) -> re.Match | None:
    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            return m
    return None


def _snippet(text: str, m: re.Match, width: int = 90) -> str:
    start = max(0, m.start() - width // 2)
    return "…" + text[start: m.end() + width // 2].replace("\n", " ").strip() + "…"


def jd_skills(text: str) -> list[str]:
    found = []
    for skill, patterns in VOCAB.items():
        flags = 0 if skill in ("C", "Go", "R") else re.I
        if any(re.search(p, text, flags) for p in patterns):
            found.append(skill)
    return found


def profile_skills(profile: Profile) -> set[str]:
    corpus = profile.corpus().lower()
    have = set()
    for skill, patterns in VOCAB.items():
        flags = 0 if skill in ("C", "Go", "R") else re.I
        if skill in ("C", "Go", "R"):
            # Short names: only trust the explicit skills list.
            if skill in profile.all_skills:
                have.add(skill)
            continue
        if any(re.search(p, corpus, flags) for p in patterns):
            have.add(skill)
    for skill, aliases in PROFILE_ALIASES.items():
        if any(a in corpus for a in aliases):
            have.add(skill)
    return have


def _parse_point(month: str | None, year: str) -> tuple[int, int] | None:
    y = int(year)
    if not month:
        return None
    m = month.lower()
    if m in SEASONS:
        return (y, SEASONS[m])
    if m[:3] in MONTHS:
        return (y, MONTHS[m[:3]])
    return None


_POINT = re.compile(
    r"(?:(jan\w*|feb\w*|mar\w*|apr\w*|may|jun\w*|jul\w*|aug\w*|sep\w*|oct\w*|nov\w*|dec\w*|winter|spring|summer|fall|autumn)\s+)?(20\d\d)",
    re.I,
)


def graduation_check(text: str, title: str, grad: str) -> tuple[list[str], list[str], list[str]]:
    """Return (blockers, warnings, positives) about the graduation-date window."""
    gy, gm = (int(x) for x in grad.split("-"))
    me = gy * 12 + gm
    blockers, warnings, positives = [], [], []

    ty = re.findall(r"\b(20\d\d)\b", title)
    if ty:
        if str(gy) in ty:
            positives.append(f"Title targets {gy} graduates")
        else:
            blockers.append(f"Title is for {'/'.join(ty)} graduates; you graduate {grad}")
            return blockers, warnings, positives

    for sent in re.split(r"(?<=[.!?\n])\s+", text):
        if not re.search(r"graduat|class of|new grad", sent, re.I):
            continue
        points = [(m.group(1), m.group(2)) for m in _POINT.finditer(sent)]
        if not points:
            continue
        years = [int(y) for _, y in points]
        if len(points) == 1:
            p = _parse_point(*points[0])
            if years[0] != gy:
                blockers.append(f"Graduation requirement mentions {years[0]}; you graduate {grad}: “{sent.strip()[:140]}”")
            elif p and abs((p[0] * 12 + p[1]) - me) > 2:
                warnings.append(f"Graduation timing may not match: “{sent.strip()[:140]}”")
            else:
                positives.append(f"Graduation timing matches: “{sent.strip()[:120]}”")
        else:
            first = _parse_point(*points[0]) or (years[0], 1)
            last = _parse_point(*points[-1]) or (years[-1], 12)
            lo, hi = first[0] * 12 + first[1], last[0] * 12 + last[1]
            if lo <= me <= hi:
                positives.append(f"Your {grad} graduation is inside the window: “{sent.strip()[:120]}”")
            else:
                blockers.append(f"Graduation window excludes {grad}: “{sent.strip()[:140]}”")
        break
    return blockers, warnings, positives


def years_check(text: str, max_years: int) -> tuple[list[str], list[str]]:
    blockers, warnings = [], []
    for sent in re.split(r"(?<=[.!?\n])\s+", text):
        m = re.search(r"(\d+)\s*\+?\s*(?:[-–]|to)?\s*(\d+)?\s*\+?\s*years?", sent, re.I)
        if not m or not re.search(r"experience", sent, re.I):
            continue
        low = int(m.group(1))
        if low > 15:  # "over 80 years of history" etc.
            continue
        if low >= 3:
            blockers.append(f"Asks for {low}+ years of experience: “{sent.strip()[:140]}”")
        elif low > max_years:
            warnings.append(f"Asks for {low}+ years of experience: “{sent.strip()[:140]}”")
        break
    return blockers, warnings


def score_job(job: Job, profile: Profile) -> FitResult:
    text = f"{job.title}\n{job.description}"
    blockers: list[str] = []
    warnings: list[str] = []
    positives: list[str] = []

    if profile.get("needs_sponsorship"):
        m = _search_any(NO_SPONSOR, text)
        if m:
            blockers.append(f"Does not sponsor visas: {_snippet(text, m)}")
        elif (m := _search_any(SPONSOR_OK, text)):
            positives.append(f"Mentions sponsorship: {_snippet(text, m)}")
    m = _search_any(US_ONLY, text)
    if m:
        blockers.append(f"Requires US citizenship or clearance: {_snippet(text, m)}")

    if SENIOR_TITLE.search(job.title) and not re.search(r"new grad|graduate|early career", job.title, re.I):
        blockers.append(f"Senior-level title: {job.title}")

    b, w, p = graduation_check(job.description, job.title, profile.get("graduation", "2027-05"))
    blockers += b
    warnings += w
    positives += p

    b, w = years_check(job.description, int(profile.get("max_years_experience", 1)))
    blockers += b
    warnings += w

    if _search_any(NEW_GRAD, text):
        positives.append("Open to new grads / entry level")

    asked = jd_skills(text)
    have = profile_skills(profile)
    matched = [s for s in asked if s in have]
    missing = [s for s in asked if s not in have]

    base = 100 * len(matched) / max(1, len(matched) + len(missing)) if asked else 50
    score = base + 8 * len([x for x in positives if "new grad" in x.lower() or "graduat" in x.lower()]) - 12 * len(warnings)
    score = int(max(0, min(100, round(score))))
    if blockers:
        score = min(score, 20)
        verdict = "Skip"
    elif score >= 65:
        verdict = "Strong"
    elif score >= 45:
        verdict = "Possible"
    else:
        verdict = "Weak"
    return FitResult(score, verdict, matched, missing, blockers, warnings, positives)
