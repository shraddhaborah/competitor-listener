"""Persist snapshots and the change log.

Snapshots are plain text files committed to the repo, so git history doubles
as a full audit trail. The change log is JSON served by GitHub Pages for the
dashboard.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

MAX_LOG_ENTRIES = 500


class Store:
    def __init__(self, snapshot_dir: str | Path = "snapshots", log_path: str | Path = "docs/changes.json"):
        self.snapshot_dir = Path(snapshot_dir)
        self.log_path = Path(log_path)

    def _snap_path(self, slug: str) -> Path:
        return self.snapshot_dir / f"{slug}.txt"

    def read_snapshot(self, slug: str) -> str | None:
        p = self._snap_path(slug)
        return p.read_text() if p.exists() else None

    def write_snapshot(self, slug: str, text: str) -> None:
        p = self._snap_path(slug)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def read_log(self) -> dict:
        if self.log_path.exists():
            return json.loads(self.log_path.read_text())
        return {"generated_at": None, "changes": [], "status": {}}

    def write_log(self, data: dict) -> None:
        data["generated_at"] = now_iso()
        data["changes"] = data["changes"][:MAX_LOG_ENTRIES]
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
