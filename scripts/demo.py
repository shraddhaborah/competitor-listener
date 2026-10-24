"""Simulate a full detection cycle locally. No internet, no GitHub needed.

    python scripts/demo.py              # runs the cycle, then opens the dashboard
    python scripts/demo.py --no-serve   # just runs the cycle and prints results

What it does:
  1. Serves a fake competitor pricing page on localhost.
  2. Runs the listener once (saves a baseline).
  3. Edits the page: raises a price and adds a feature, plus a noise-only
     edit on a second page (copyright year + rotating testimonial).
  4. Runs the listener again and shows what was detected and what was ignored.
  5. Opens the dashboard with the results.

Everything happens in a temporary folder; your repo is not modified.
If ANTHROPIC_API_KEY is set, the demo uses LLM triage; otherwise rules.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import threading
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from listener.run import main as run_listener  # noqa: E402

PRICING_BEFORE = """<html><body>
<nav>Product Pricing Docs</nav>
<main id="plans">
  <h2>Free</h2><p>$0</p><p>Up to 3 projects</p>
  <h2>Pro</h2><p>$8 / user / month</p><p>Unlimited projects</p><p>Priority support</p>
  <h2>Enterprise</h2><p>Contact sales</p>
</main>
<footer>© 2025 Acme Inc.</footer>
</body></html>"""

PRICING_AFTER = (
    PRICING_BEFORE
    .replace("$8 / user / month", "$12 / user / month")
    .replace("<p>Priority support</p>", "<p>Priority support</p><p>New: AI assistant included</p>")
)

HOME_BEFORE = """<html><body>
<h1>Ship faster with Acme</h1>
<div class="testimonial-carousel">"Acme changed how we work." - Jane, CTO</div>
<p>Plan, build and launch in one place.</p>
<footer>© 2025 Acme Inc.</footer>
</body></html>"""

HOME_AFTER = (
    HOME_BEFORE
    .replace('"Acme changed how we work." - Jane, CTO', '"We cut planning time in half." - Raj, VP Eng')
    .replace("© 2025", "© 2026")
)


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def serve(directory: Path) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(directory)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def banner(text: str) -> None:
    print(f"\n{'=' * 60}\n{text}\n{'=' * 60}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--no-serve", action="store_true", help="don't open the dashboard afterwards")
    args = ap.parse_args()

    # Local traffic must not go through any corporate/system proxy, and the demo
    # must never open real GitHub issues or post to a real webhook.
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
    for var in ("GITHUB_TOKEN", "GITHUB_REPOSITORY", "ALERT_WEBHOOK_URL"):
        os.environ.pop(var, None)

    tmp = Path(tempfile.mkdtemp(prefix="competitor-listener-demo-"))
    site, work = tmp / "site", tmp / "work"
    site.mkdir()
    (work / "docs").mkdir(parents=True)
    shutil.copy(ROOT / "docs" / "index.html", work / "docs" / "index.html")

    (site / "pricing.html").write_text(PRICING_BEFORE)
    (site / "home.html").write_text(HOME_BEFORE)
    site_srv = serve(site)
    base = f"http://127.0.0.1:{site_srv.server_port}"

    (work / "targets.yaml").write_text(f"""
settings: {{min_changed_chars: 40, alert_threshold: medium}}
competitors:
  - name: Acme
    pages:
      - {{name: Pricing, url: "{base}/pricing.html", selector: "#plans"}}
      - {{name: Homepage, url: "{base}/home.html"}}
""")

    triage_mode = "LLM (ANTHROPIC_API_KEY found)" if os.environ.get("ANTHROPIC_API_KEY") else "rules (no API key)"
    print(f"Demo folder: {tmp}\nTriage mode: {triage_mode}")

    os.chdir(work)
    try:
        banner("RUN 1: first look at the competitor (saves baselines)")
        run_listener(["--config", "targets.yaml"])

        (site / "pricing.html").write_text(PRICING_AFTER)
        (site / "home.html").write_text(HOME_AFTER)
        banner("Competitor edits their site:\n"
               "  Pricing:  Pro $8 -> $12, new 'AI assistant' line   (should ALERT)\n"
               "  Homepage: new testimonial, copyright 2025 -> 2026  (should be IGNORED)")

        banner("RUN 2: check again")
        run_listener(["--config", "targets.yaml"])

        data = json.loads((work / "docs" / "changes.json").read_text())
        banner(f"RESULT: {len(data['changes'])} change(s) logged")
        for c in data["changes"]:
            print(f"  [{c['category']}/{c['importance']}] {c['competitor']} {c['page']}: {c['summary']}")
            for d in c.get("details", []):
                print(f"      {d}")
        homepage_logged = any(c["page"] == "Homepage" for c in data["changes"])
        print(f"\n  Homepage noise correctly ignored: {'NO, check normalize.py' if homepage_logged else 'yes'}")

        if args.no_serve:
            return 0

        dash = serve(work / "docs")
        url = f"http://127.0.0.1:{dash.server_port}/index.html"
        banner(f"Dashboard: {url}\nPress Enter to stop.")
        webbrowser.open(url)
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass
        return 0
    finally:
        os.chdir(ROOT)
        site_srv.shutdown()


if __name__ == "__main__":
    sys.exit(main())
