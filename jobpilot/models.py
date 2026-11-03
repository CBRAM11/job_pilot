from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
OUTPUT_DIR = ROOT / "output"


@dataclass
class Job:
    company: str
    title: str
    description: str
    url: str = ""
    apply_url: str = ""
    location: str = ""
    source: str = "manual"
    external_id: str = ""

    @property
    def key(self) -> str:
        """Stable identity used to avoid storing the same posting twice."""
        if self.external_id:
            return f"{self.source}:{self.external_id}"
        return f"{self.source}:{self.company}:{self.title}:{self.url}".lower()


@dataclass
class Profile:
    data: dict[str, Any]

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    @property
    def all_skills(self) -> list[str]:
        out: list[str] = []
        for items in self.data.get("skills", {}).values():
            out.extend(items)
        for p in self.data.get("projects", []):
            out.extend(p.get("tech", []))
        return out

    def corpus(self) -> str:
        """All profile text. Used to check that generated text adds no new facts."""
        return yaml.safe_dump(self.data, allow_unicode=True)


def load_profile(path: Path | None = None) -> Profile:
    """Load config/profile.yaml (your real, git-ignored profile), or the example if you haven't made one."""
    if path is None:
        path = CONFIG_DIR / "profile.yaml"
        if not path.exists():
            path = CONFIG_DIR / "profile.example.yaml"
    with open(path, encoding="utf-8") as f:
        return Profile(yaml.safe_load(f))


def load_companies(path: Path | None = None) -> dict[str, Any]:
    path = path or CONFIG_DIR / "companies.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


_TAG_RE = re.compile(r"<[^>]+>")


def html_to_text(raw: str) -> str:
    """Turn job-board HTML into plain text, keeping list items on their own lines."""
    if not raw:
        return ""
    text = html.unescape(raw)  # Greenhouse double-escapes its HTML
    text = re.sub(r"(?i)<\s*(br|/p|/li|/h\d|/div)\s*/?>", "\n", text)
    text = re.sub(r"(?i)<\s*li[^>]*>", "\n- ", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()
