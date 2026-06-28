"""Login flow against familias.pencilapp.net.

The portal is a hash-routed AngularJS 1.x SPA served via Cloudflare. The
login form is `<input type="submit" value="Entrar">` gated by `ng-disabled`
on form validity — Playwright's `fill()` dispatches `input` events that
ng-model picks up, so we just have to wait for the submit to become enabled
before clicking.

We use a persistent context + the user's installed Chrome to avoid
Cloudflare's bot-detection 403 on the auth POST. Cookies and localStorage
survive across runs via the persistent user-data dir, so subsequent runs
skip the form while the session is still valid.
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

EMAIL_SELECTOR = 'input[name="email"][type="email"]'
PASSWORD_SELECTOR = 'input[name="password"][type="password"]'
SUBMIT_SELECTOR = 'input[type="submit"].login-button'

STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--no-default-browser-check",
    "--no-first-run",
]


async def open_context(playwright: Playwright, *, headless: bool = True) -> BrowserContext:
    """Open a Chromium context that doesn't trip Cloudflare bot detection."""
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
        return await playwright.chromium.launch_persistent_context(**common)


async def ensure_logged_in(context: BrowserContext, config: Config) -> Page:
    """Return a Page sitting on the app after a successful login.

    If the persistent context already has a valid session this is a no-op
    navigate. Otherwise we fill the login form, wait for Angular to enable
    the submit button (ng-disabled is bound to form validity), and click.
    """
    page = context.pages[0] if context.pages else await context.new_page()
    await page.goto(BASE_URL, wait_until="domcontentloaded")
    await page.wait_for_load_state("networkidle")

    if "#/login" not in page.url:
        return page

    await _fill_login_form(page, config)
    await page.wait_for_url(lambda url: "#/login" not in url, timeout=60_000)
    await page.wait_for_load_state("networkidle")
    return page


async def _fill_login_form(page: Page, config: Config) -> None:
    try:
        await page.wait_for_selector(EMAIL_SELECTOR, timeout=15_000)
    except PWTimeout as exc:
        raise RuntimeError(
            "Could not find the email input on the login page. "
            "Run `python -m pencil_extract inspect` to capture the current DOM."
        ) from exc

    await page.fill(EMAIL_SELECTOR, config.email)
    await page.fill(PASSWORD_SELECTOR, config.password)

    # ng-disabled keeps the submit input disabled until Angular re-validates
    # after our input events. Wait for that before clicking.
    await page.wait_for_function(
        f"() => {{ const el = document.querySelector('{SUBMIT_SELECTOR}');"
        " return el && !el.disabled; }}",
        timeout=10_000,
    )
    await page.click(SUBMIT_SELECTOR)
