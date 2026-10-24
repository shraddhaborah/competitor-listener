"""Entry point: python -m listener.run [--config targets.yaml] [--dry-run]"""

from __future__ import annotations

import argparse
import logging
import sys

from .alert import send_alerts
from .config import Page, load_config
from .diff import compute_diff, is_significant
from .fetch import FetchError, fetch
from .normalize import normalize
from .store import Store, now_iso
from .triage import triage

log = logging.getLogger("listener")


def process_page(page: Page, html: str, store: Store, cfg, dry_run: bool) -> dict | None:
    """Normalize, diff, triage and alert for one page. Returns a change record or None."""
    text = normalize(html, page.selector, page.ignore)
    previous = store.read_snapshot(page.slug)

    if previous is None:
        log.info("%s: first snapshot saved (baseline)", page.slug)
        if not dry_run:
            store.write_snapshot(page.slug, text)
        return None

    diff = compute_diff(previous, text, label=page.slug)
    if not is_significant(diff, cfg.settings.min_changed_chars):
        log.info("%s: no meaningful change", page.slug)
        if not dry_run and not diff.is_empty:
            store.write_snapshot(page.slug, text)  # absorb trivial drift
        return None

    result = triage(diff, page.competitor, page.name, page.url)
    log.info("%s: %s/%s via %s: %s", page.slug, result.category, result.importance, result.source, result.summary)

    receipts: list[str] = []
    if result.meets(cfg.settings.alert_threshold) and not dry_run:
        receipts = send_alerts(page.competitor, page.name, page.url, result, diff.unified)

    if not dry_run:
        store.write_snapshot(page.slug, text)

    return {
        "detected_at": now_iso(),
        "competitor": page.competitor,
        "page": page.name,
        "url": page.url,
        **result.to_dict(),
        "lines_added": len(diff.added),
        "lines_removed": len(diff.removed),
        "alerted": bool(receipts),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Watch competitor pages for meaningful changes.")
    ap.add_argument("--config", default="targets.yaml")
    ap.add_argument("--dry-run", action="store_true", help="don't write snapshots or send alerts")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")

    cfg = load_config(args.config)
    store = Store()
    data = store.read_log()
    failures = 0

    for page in cfg.pages:
        status = {"url": page.url, "checked_at": now_iso()}
        try:
            html = fetch(page, cfg.settings)
            record = process_page(page, html, store, cfg, args.dry_run)
            if record:
                data["changes"].insert(0, record)
            status["ok"] = True
        except (FetchError, ValueError) as err:
            # One broken page must never take down the whole run.
            failures += 1
            log.error("%s: %s", page.slug, err)
            status.update(ok=False, error=str(err)[:300])
        data["status"][page.slug] = status

    if not args.dry_run:
        store.write_log(data)

    log.info("done: %d pages, %d failures", len(cfg.pages), failures)
    # Fail the workflow only if everything failed (likely a config or network problem).
    return 1 if cfg.pages and failures == len(cfg.pages) else 0


if __name__ == "__main__":
    sys.exit(main())
