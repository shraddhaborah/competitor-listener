"""Classify a diff with an LLM, falling back to rules when no API key is set.

The LLM answers one narrow question: "what kind of change is this, how much
does it matter, and what's the one-sentence summary?" It returns JSON, which
is validated before use, so a bad response degrades to the rule-based
result instead of crashing the run.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass

import requests

from .diff import Diff

log = logging.getLogger(__name__)

CATEGORIES = {"pricing", "feature", "positioning", "legal", "noise"}
IMPORTANCE = {"low": 0, "medium": 1, "high": 2}

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-haiku-5-5"
MAX_DIFF_CHARS = 12_000  # keep each call cheap and bounded

PROMPT = """You are a competitive-intelligence analyst. A competitor's web page changed.
Classify the change and summarize it for a busy product manager.

Competitor: {competitor}
Page: {page} ({url})

Removed lines:
{removed}

Added lines:
{added}

Respond with ONLY a JSON object, no prose:
{{"category": one of ["pricing","feature","positioning","legal","noise"],
  "importance": one of ["low","medium","high"],
  "summary": "one sentence, concrete, mention numbers if any changed",
  "details": ["up to 3 short bullet strings with specifics"]}}

Guidance: price, plan, limit or packaging changes are pricing and usually high.
New or removed capabilities are feature. Copy/tagline/messaging shifts are
positioning. Terms or privacy text is legal. Cosmetic or irrelevant edits are noise."""


@dataclass
class Triage:
    category: str
    importance: str
    summary: str
    details: list[str]
    source: str  # "llm" or "rules"

    def to_dict(self) -> dict:
        return asdict(self)

    def meets(self, threshold: str) -> bool:
        return self.category != "noise" and IMPORTANCE[self.importance] >= IMPORTANCE[threshold]


def triage(diff: Diff, competitor: str, page: str, url: str) -> Triage:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        try:
            return _llm_triage(diff, competitor, page, url, key)
        except Exception as err:  # noqa: BLE001 - any failure falls back to rules
            log.warning("LLM triage failed, using rules: %s", err)
    return rule_triage(diff)


def rule_triage(diff: Diff) -> Triage:
    n_add, n_rem = len(diff.added), len(diff.removed)
    if diff.touches_pricing:
        cat, imp = "pricing", "high"
        summary = f"Pricing-related text changed ({n_add} lines added, {n_rem} removed)."
    elif diff.changed_chars > 600:
        cat, imp = "feature", "medium"
        summary = f"Substantial content change ({n_add} lines added, {n_rem} removed)."
    else:
        cat, imp = "positioning", "low"
        summary = f"Minor copy change ({n_add} lines added, {n_rem} removed)."
    details = [f"+ {x[:140]}" for x in diff.added[:2]] + [f"- {x[:140]}" for x in diff.removed[:1]]
    return Triage(cat, imp, summary, details, source="rules")


def _llm_triage(diff: Diff, competitor: str, page: str, url: str, key: str) -> Triage:
    removed = "\n".join(diff.removed)[: MAX_DIFF_CHARS // 2] or "(none)"
    added = "\n".join(diff.added)[: MAX_DIFF_CHARS // 2] or "(none)"
    body = {
        "model": os.environ.get("TRIAGE_MODEL", DEFAULT_MODEL),
        "max_tokens": 400,
        "messages": [{
            "role": "user",
            "content": PROMPT.format(competitor=competitor, page=page, url=url, removed=removed, added=added),
        }],
    }
    resp = requests.post(
        API_URL,
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json=body,
        timeout=60,
    )
    resp.raise_for_status()
    text = "".join(b.get("text", "") for b in resp.json().get("content", []))
    return parse_llm_json(text)


def parse_llm_json(text: str) -> Triage:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON object in response")
    data = json.loads(text[start : end + 1])

    category = str(data.get("category", "")).lower()
    importance = str(data.get("importance", "")).lower()
    summary = str(data.get("summary", "")).strip()
    if category not in CATEGORIES or importance not in IMPORTANCE or not summary:
        raise ValueError(f"invalid triage payload: {data}")
    details = [str(d) for d in (data.get("details") or [])][:3]
    return Triage(category, importance, summary, details, source="llm")
