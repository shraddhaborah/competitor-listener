"""Fetch page HTML, with retries and an optional headless-browser path."""

from __future__ import annotations

import logging
import time

import requests

from .config import Page, Settings

log = logging.getLogger(__name__)


class FetchError(Exception):
    pass


def fetch(page: Page, settings: Settings, retries: int = 3) -> str:
    if page.render_js:
        return _fetch_rendered(page, settings)

    headers = {"User-Agent": settings.user_agent, "Accept-Language": "en-US,en;q=0.9"}
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(page.url, headers=headers, timeout=settings.timeout)
        except requests.RequestException as err:
            last_err = err
        else:
            if resp.ok:
                return resp.text
            if resp.status_code != 429 and resp.status_code < 500:
                # 404, 403 etc. won't fix themselves on retry: fail fast.
                raise FetchError(f"{page.url}: HTTP {resp.status_code}")
            last_err = FetchError(f"HTTP {resp.status_code}")

        if attempt < retries:
            log.warning("fetch %s failed (attempt %d/%d): %s", page.url, attempt, retries, last_err)
            time.sleep(2 ** attempt)
    raise FetchError(f"{page.url}: {last_err}")


def _fetch_rendered(page: Page, settings: Settings) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as err:  # pragma: no cover
        raise FetchError("render_js requires: pip install playwright && playwright install chromium") from err

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            ctx = browser.new_context(user_agent=settings.user_agent)
            pg = ctx.new_page()
            pg.goto(page.url, timeout=settings.timeout * 1000, wait_until="networkidle")
            return pg.content()
        finally:
            browser.close()
