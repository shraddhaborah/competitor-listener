"""Deliver alerts: GitHub Issues (default inside Actions) and an optional webhook."""

from __future__ import annotations

import logging
import os

import requests

from .triage import Triage

log = logging.getLogger(__name__)

EMOJI = {"pricing": "💲", "feature": "🧩", "positioning": "📣", "legal": "⚖️", "noise": "·"}


def format_issue(competitor: str, page: str, url: str, t: Triage, unified_diff: str) -> tuple[str, str]:
    title = f"{EMOJI.get(t.category, '')} [{competitor}] {t.category} change on {page} ({t.importance})"
    bullets = "\n".join(f"- {d}" for d in t.details) or "- (no details)"
    diff_block = unified_diff[:6000] + ("\n... (truncated)" if len(unified_diff) > 6000 else "")
    body = (
        f"**{t.summary}**\n\n"
        f"{bullets}\n\n"
        f"Page: {url}\n"
        f"Triage: `{t.source}` · category `{t.category}` · importance `{t.importance}`\n\n"
        f"<details><summary>Raw diff</summary>\n\n```diff\n{diff_block}\n```\n</details>\n"
    )
    return title, body


def send_alerts(competitor: str, page: str, url: str, t: Triage, unified_diff: str) -> list[str]:
    """Send to every configured channel. Returns a list of delivery receipts."""
    title, body = format_issue(competitor, page, url, t, unified_diff)
    receipts: list[str] = []

    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if token and repo:
        try:
            receipts.append(_github_issue(token, repo, title, body, labels=["competitor-change", t.category]))
        except requests.RequestException as err:
            log.error("GitHub issue failed: %s", err)

    hook = os.environ.get("ALERT_WEBHOOK_URL")
    if hook:
        try:
            receipts.append(_webhook(hook, f"{title}\n{t.summary}\n{url}"))
        except requests.RequestException as err:
            log.error("webhook failed: %s", err)

    if not receipts:
        log.info("ALERT (no channel configured): %s\n%s", title, t.summary)
        receipts.append("stdout")
    return receipts


def _github_issue(token: str, repo: str, title: str, body: str, labels: list[str]) -> str:
    resp = requests.post(
        f"https://api.github.com/repos/{repo}/issues",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        json={"title": title, "body": body, "labels": labels},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("html_url", "github-issue")


def _webhook(url: str, text: str) -> str:
    # Slack uses "text", Discord uses "content"; sending both works for either.
    resp = requests.post(url, json={"text": text, "content": text[:1900]}, timeout=15)
    resp.raise_for_status()
    return "webhook"
