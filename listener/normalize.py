"""Turn raw HTML into stable, comparable text.

Most "changes" on a web page are noise: rotating testimonials, CSRF tokens,
cookie banners, copyright years, cache-busting query strings. Normalizing
aggressively here is what keeps the alert channel trustworthy.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

# Elements that never carry competitive signal.
DEFAULT_STRIP = [
    "script", "style", "noscript", "svg", "iframe", "template", "link", "meta",
    "[aria-hidden=true]",
    "[id*=cookie]", "[class*=cookie]", "[id*=consent]", "[class*=consent]",
    "[class*=testimonial]", "[class*=carousel]", "[class*=marquee]",
]

# Text patterns that churn without meaning anything.
NOISE_PATTERNS = [
    re.compile(r"©\s*\d{4}(\s*[-–]\s*\d{4})?"),                                  # copyright years
    re.compile(r"\b\d{1,2}:\d{2}(:\d{2})?\s*(am|pm)?\b", re.IGNORECASE),         # clock times
    re.compile(r"\b[0-9a-f]{24,}\b", re.IGNORECASE),                             # hashes / tokens
    re.compile(r"\b(updated|last updated)\s+\d+\s+\w+\s+ago\b", re.IGNORECASE),  # relative dates
]


def normalize(html: str, selector: str | None = None, ignore: list[str] | None = None) -> str:
    soup = BeautifulSoup(html, "html.parser")

    for sel in DEFAULT_STRIP + (ignore or []):
        for el in soup.select(sel):
            el.decompose()

    root = soup
    if selector:
        found = soup.select(selector)
        if not found:
            raise ValueError(f"selector {selector!r} matched nothing; the page layout may have changed")
        root = BeautifulSoup("".join(str(f) for f in found), "html.parser")

    lines = []
    for raw in root.get_text("\n").splitlines():
        line = " ".join(raw.split())
        for pat in NOISE_PATTERNS:
            line = pat.sub("", line).strip()
        if line:
            lines.append(line)

    # Collapse consecutive duplicate lines (common with responsive nav menus).
    deduped = [ln for i, ln in enumerate(lines) if i == 0 or ln != lines[i - 1]]
    return "\n".join(deduped) + "\n"
