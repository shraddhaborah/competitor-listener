"""Compute line diffs and decide whether they're worth triaging."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

PRICE_RE = re.compile(
    r"[$€£]\s?\d[\d,]*(\.\d+)?"                  # $8, €1,200.50
    r"|\b\d+(\.\d+)?\s?(usd|eur|gbp)\b"          # 20 USD
    r"|/\s?(mo|month|yr|year|user|seat)\b",       # / month, /user
    re.IGNORECASE,
)


@dataclass
class Diff:
    added: list[str]
    removed: list[str]
    unified: str

    @property
    def changed_chars(self) -> int:
        return sum(len(x) for x in self.added) + sum(len(x) for x in self.removed)

    @property
    def touches_pricing(self) -> bool:
        return any(PRICE_RE.search(x) for x in self.added + self.removed)

    @property
    def is_empty(self) -> bool:
        return not self.added and not self.removed


def compute_diff(old: str, new: str, label: str = "page") -> Diff:
    old_lines, new_lines = old.splitlines(), new.splitlines()
    unified = "\n".join(
        difflib.unified_diff(old_lines, new_lines, f"{label} (before)", f"{label} (after)", lineterm="", n=2)
    )
    sm = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    added, removed = [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "delete"):
            removed.extend(old_lines[i1:i2])
        if tag in ("replace", "insert"):
            added.extend(new_lines[j1:j2])

    # Lines that merely moved position aren't changes.
    moved = set(added) & set(removed)
    added = [x for x in added if x not in moved]
    removed = [x for x in removed if x not in moved]
    return Diff(added=added, removed=removed, unified=unified)


def is_significant(diff: Diff, min_changed_chars: int) -> bool:
    if diff.is_empty:
        return False
    # Any price-looking change is always worth a look, however small.
    return diff.touches_pricing or diff.changed_chars >= min_changed_chars
