"""Login flow against familias.pencilapp.net.

The portal is a hash-routed SPA served via Cloudflare. Cloudflare's bot
detection rejects logins from the default Playwright Chromium fingerprint
(403 on the auth POST). We launch a persistent context with the user's
installed Google Chrome and the `AutomationControlled` blink feature
disabled — that's enough to look like a real browser for this site.

Selectors are placeholders until the first `inspect` run gives us real ones.
They are kept in one place so they're easy to update.
"""

from __future__ import annotations

from playwright.async_api import (
    BrowserContext,
    Error as PWError,
    Page,
    Playwright,
    TimeoutError as PWTimeout,
)

from pencil_extract.config import Config
from pencil_extract.paths import PLAYWRIGHT_DIR

BASE_URL = "https://familias.pencilapp.net/"
LOGIN_URL = "https://familias.pencilapp.net/#/login"

# Placeholder selectors. Refine after running `python -m pencil_extract inspect`.
EMAIL_SELECTOR = 'input[type="email"], input[name="email"], input[autocomplete="username"]'
PASSWORD_SELECTOR = 'input[type="password"], input[name="password"]'
SUBMIT_SELECTOR = 'button[type="submit"]'

# Launch args that strip the most obvious Playwright/CDP fingerprints. Not
# bulletproof against advanced bot detection, but enough for run-of-the-mill
# Cloudflare rules.
STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--no-default-browser-check",
    "--no-first-run",
]


async def open_context(playwright: Playwright, *, headless: bool = True) -> BrowserContext:
    """Open a Chromium context that doesn't trip Cloudflare bot detection.

    Persistent context with a real user-data dir + Google Chrome channel
    (falls back to bundled Chromium if Chrome isn't installed). Cookies and
    localStorage survive across runs automatically, so we don't need a
    separate storage-state file.
    """
    PLAYWRIGHT_DIR.mkdir(parents=True, exist_ok=True)
    user_data_dir = PLAYWRIGHT_DIR / "user-data"

    common = dict(
        user_data_dir=str(user_data_dir),
        headless=headless,
        args=STEALTH_ARGS,
        viewport={"width": 1280, "height": 800},
        locale="es-ES",
    )

    try:
        return await playwright.chromium.launch_persistent_context(
            channel="chrome", **common,
        )
    except PWError:
        # Chrome channel not installed; fall back to bundled Chromium.
        return await playwright.chromium.launch_persistent_context(**common)


async def ensure_logged_in(context: BrowserContext, config: Config) -> Page:
    """Return a Page sitting on the app after a successful login.

    If the persistent context already has a valid Pencil session cookie this
    is a no-op navigate. Otherwise we fill the login form and submit.
    """
    page = context.pages[0] if context.pages else await context.new_page()
    await page.goto(BASE_URL, wait_until="domcontentloaded")
    await page.wait_for_load_state("networkidle")

    if "#/login" not in page.url:
        return page

    await _fill_login_form(page, config)
    await page.wait_for_url(lambda url: "#/login" not in url, timeout=30_000)
    await page.wait_for_load_state("networkidle")
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
