import json

import pytest

from listener.config import Settings, load_config
from listener.diff import compute_diff, is_significant
from listener.normalize import normalize
from listener.run import process_page
from listener.store import Store
from listener.triage import parse_llm_json, rule_triage

PRICING_V1 = """
<html><head><script>var t = 1;</script><style>.x{}</style></head><body>
  <div class="cookie-banner">We use cookies</div>
  <main id="plans">
    <h2>Pro</h2><p>$8 / user / month</p><p>Unlimited projects</p>
  </main>
  <footer>© 2025 Acme Inc. · token a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4</footer>
</body></html>
"""

PRICING_V2 = PRICING_V1.replace("$8 / user / month", "$10 / user / month")


# --- normalize -----------------------------------------------------------------

def test_normalize_strips_noise():
    text = normalize(PRICING_V1)
    assert "var t" not in text
    assert "cookies" not in text
    assert "2025" not in text
    assert "a1b2c3d4e5f6" not in text
    assert "$8 / user / month" in text


def test_normalize_selector_focuses_content():
    text = normalize(PRICING_V1, selector="#plans")
    assert "Pro" in text and "Acme" not in text


def test_normalize_missing_selector_raises():
    with pytest.raises(ValueError, match="matched nothing"):
        normalize(PRICING_V1, selector="#does-not-exist")


def test_copyright_year_bump_is_not_a_change():
    a = normalize(PRICING_V1)
    b = normalize(PRICING_V1.replace("© 2025", "© 2026"))
    assert a == b


# --- diff ----------------------------------------------------------------------

def test_price_change_is_significant_even_if_tiny():
    d = compute_diff(normalize(PRICING_V1), normalize(PRICING_V2))
    assert d.touches_pricing
    assert is_significant(d, min_changed_chars=1000)


def test_reordered_lines_are_not_changes():
    d = compute_diff("a\nb\nc\n", "c\na\nb\n")
    assert d.is_empty


def test_small_copy_tweak_below_threshold():
    d = compute_diff("Build faster.\n", "Build faster!\n")
    assert not is_significant(d, min_changed_chars=40)


# --- triage --------------------------------------------------------------------

def test_rule_triage_flags_pricing_high():
    d = compute_diff(normalize(PRICING_V1), normalize(PRICING_V2))
    t = rule_triage(d)
    assert (t.category, t.importance, t.source) == ("pricing", "high", "rules")
    assert t.meets("medium")


def test_parse_llm_json_tolerates_wrapping_text():
    raw = 'Sure! {"category":"pricing","importance":"high","summary":"Pro went from $8 to $10.","details":["x"]}'
    t = parse_llm_json(raw)
    assert t.category == "pricing" and t.summary.startswith("Pro")


def test_parse_llm_json_rejects_bad_payload():
    with pytest.raises(ValueError):
        parse_llm_json('{"category":"vibes","importance":"high","summary":"?"}')


def test_noise_never_alerts():
    from listener.triage import Triage
    assert not Triage("noise", "high", "x", [], "llm").meets("low")


# --- config --------------------------------------------------------------------

def test_load_config(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("""
settings: {min_changed_chars: 10}
competitors:
  - name: Acme Co
    pages:
      - {name: Pricing Page, url: https://example.com/pricing, selector: "#plans"}
""")
    cfg = load_config(p)
    assert cfg.settings.min_changed_chars == 10
    assert cfg.pages[0].slug == "acme-co/pricing-page"


def test_config_rejects_duplicates(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("""
competitors:
  - name: A
    pages: [{name: P, url: https://a.com}, {name: P, url: https://a.com/2}]
""")
    with pytest.raises(ValueError, match="Duplicate"):
        load_config(p)


def test_repo_targets_yaml_is_valid():
    cfg = load_config("targets.yaml")
    assert cfg.pages


# --- end to end (no network) ----------------------------------------------------

def test_process_page_end_to_end(tmp_path, monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "GITHUB_TOKEN", "ALERT_WEBHOOK_URL"):
        monkeypatch.delenv(var, raising=False)

    from listener.config import Config, Page
    page = Page(competitor="Acme", name="Pricing", url="https://example.com/pricing", selector="#plans")
    cfg = Config(settings=Settings(), pages=[page])
    store = Store(tmp_path / "snaps", tmp_path / "changes.json")

    assert process_page(page, PRICING_V1, store, cfg, dry_run=False) is None   # baseline
    assert process_page(page, PRICING_V1, store, cfg, dry_run=False) is None   # unchanged

    record = process_page(page, PRICING_V2, store, cfg, dry_run=False)
    assert record["category"] == "pricing"
    assert record["importance"] == "high"
    assert record["alerted"] is True                                           # stdout channel
    assert "$10" in store.read_snapshot(page.slug)

    data = store.read_log()
    data["changes"].insert(0, record)
    store.write_log(data)
    assert json.loads((tmp_path / "changes.json").read_text())["changes"][0]["competitor"] == "Acme"


# --- fetch ---------------------------------------------------------------------

class _Resp:
    def __init__(self, code, text=""):
        self.status_code, self.text, self.ok = code, text, 200 <= code < 300


def test_fetch_fails_fast_on_404(monkeypatch):
    from listener import fetch as f
    from listener.config import Page
    calls = []
    monkeypatch.setattr(f.requests, "get", lambda *a, **k: calls.append(1) or _Resp(404))
    with pytest.raises(f.FetchError, match="404"):
        f.fetch(Page("A", "P", "https://a.com"), Settings())
    assert len(calls) == 1


def test_fetch_retries_on_503_then_succeeds(monkeypatch):
    from listener import fetch as f
    from listener.config import Page
    seq = iter([_Resp(503), _Resp(200, "<p>ok</p>")])
    monkeypatch.setattr(f.requests, "get", lambda *a, **k: next(seq))
    monkeypatch.setattr(f.time, "sleep", lambda s: None)
    assert f.fetch(Page("A", "P", "https://a.com"), Settings()) == "<p>ok</p>"


# --- demo script ----------------------------------------------------------------

def test_demo_script_runs():
    import subprocess
    import sys
    out = subprocess.run([sys.executable, "scripts/demo.py", "--no-serve"],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "1 change(s) logged" in out.stdout
    assert "noise correctly ignored: yes" in out.stdout
