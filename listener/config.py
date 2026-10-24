"""Load and validate targets.yaml.

Each competitor has one or more pages. Onboarding a new competitor means
adding a block to the YAML file, no code changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Page:
    competitor: str
    name: str
    url: str
    selector: str | None = None          # CSS selector to focus on (optional)
    ignore: list[str] = field(default_factory=list)  # CSS selectors to strip
    render_js: bool = False              # use Playwright for JS-heavy pages

    @property
    def slug(self) -> str:
        return f"{slugify(self.competitor)}/{slugify(self.name)}"


@dataclass
class Settings:
    min_changed_chars: int = 40          # ignore diffs smaller than this
    alert_threshold: str = "medium"      # low | medium | high
    user_agent: str = "CompetitorListener/0.1 (+https://github.com)"
    timeout: int = 30


@dataclass
class Config:
    settings: Settings
    pages: list[Page]


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def load_config(path: str | Path = "targets.yaml") -> Config:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    settings = Settings(**(raw.get("settings") or {}))

    pages: list[Page] = []
    for comp in raw.get("competitors") or []:
        name = comp.get("name")
        if not name:
            raise ValueError("Every competitor needs a 'name'")
        for p in comp.get("pages") or []:
            if "url" not in p or "name" not in p:
                raise ValueError(f"{name}: every page needs 'name' and 'url'")
            pages.append(
                Page(
                    competitor=name,
                    name=p["name"],
                    url=p["url"],
                    selector=p.get("selector"),
                    ignore=p.get("ignore") or [],
                    render_js=bool(p.get("render_js", False)),
                )
            )

    slugs = [p.slug for p in pages]
    dupes = {s for s in slugs if slugs.count(s) > 1}
    if dupes:
        raise ValueError(f"Duplicate page names: {sorted(dupes)}")

    if settings.alert_threshold not in {"low", "medium", "high"}:
        raise ValueError("alert_threshold must be low, medium or high")

    return Config(settings=settings, pages=pages)
