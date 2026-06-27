"""Login flow against familias.pencilapp.net.

The portal is a hash-routed SPA. We navigate to the base URL, wait for the
login form to render, fill in credentials, submit, and wait for the URL to
move off `#/login`. A storage-state file caches cookies + localStorage so
subsequent runs skip the form while the session is still valid.

Selectors are placeholders until the first `inspect` run gives us real ones.
They are kept in one place so they're easy to update.
"""

from __future__ import annotations

from playwright.async_api import BrowserContext, Page, Playwright, TimeoutError as PWTimeout

from pencil_extract.config import Config
from pencil_extract.paths import PLAYWRIGHT_DIR, STORAGE_STATE

BASE_URL = "https://familias.pencilapp.net/"
LOGIN_URL = "https://familias.pencilapp.net/#/login"

# Placeholder selectors. Refine after running `python -m pencil_extract inspect`.
EMAIL_SELECTOR = 'input[type="email"], input[name="email"], input[autocomplete="username"]'
PASSWORD_SELECTOR = 'input[type="password"], input[name="password"]'
SUBMIT_SELECTOR = 'button[type="submit"]'


async def open_context(playwright: Playwright, *, headless: bool = True) -> BrowserContext:
    """Open a Chromium context, restoring saved storage state if present."""
    PLAYWRIGHT_DIR.mkdir(parents=True, exist_ok=True)
    browser = await playwright.chromium.launch(headless=headless)
    kwargs = {"storage_state": str(STORAGE_STATE)} if STORAGE_STATE.exists() else {}
    return await browser.new_context(**kwargs)


async def ensure_logged_in(context: BrowserContext, config: Config) -> Page:
    """Return a Page sitting on the app after a successful login.

    If the saved session is still valid, this is a no-op navigate. Otherwise
    we fill the login form and submit, then persist the new storage state.
    """
    page = await context.new_page()
    await page.goto(BASE_URL, wait_until="domcontentloaded")
    # SPA may bounce us through `#/` before settling. Give it a beat.
    await page.wait_for_load_state("networkidle")

    if "#/login" not in page.url:
        return page

    await _fill_login_form(page, config)
    await page.wait_for_url(lambda url: "#/login" not in url, timeout=30_000)
    await page.wait_for_load_state("networkidle")
    await context.storage_state(path=str(STORAGE_STATE))
    return page


async def _fill_login_form(page: Page, config: Config) -> None:
    try:
        await page.wait_for_selector(EMAIL_SELECTOR, timeout=15_000)
    except PWTimeout as exc:
        raise RuntimeError(
            "Could not find the email input on the login page. "
            "Run `python -m pencil_extract inspect` and update EMAIL_SELECTOR in auth.py."
        ) from exc

    await page.fill(EMAIL_SELECTOR, config.email)
    await page.fill(PASSWORD_SELECTOR, config.password)
    await page.click(SUBMIT_SELECTOR)
