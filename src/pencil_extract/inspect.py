"""One-time discovery helper.

Opens a visible browser, lets you log in by hand, and captures DOM snapshots
plus all network requests so we can read them back and harden the real
extractors in `photos.py` / `events.py`. The login form selectors aren't yet
known, so this mode does NOT try to auto-login — that's exactly what we're
discovering here.

After you finish logging in (the URL leaves `#/login`), the session is saved
to `.playwright/storage.json` so subsequent `extract` runs can reuse it.

Usage:
    python -m pencil_extract inspect
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from playwright.async_api import Request, Response, async_playwright

from pencil_extract.auth import BASE_URL, open_context
from pencil_extract.paths import PROBES_DIR

LOGIN_TIMEOUT_MS = 10 * 60_000  # 10 min — generous, you're typing


async def run() -> None:
    PROBES_DIR.mkdir(parents=True, exist_ok=True)
    run_dir = PROBES_DIR / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir()

    requests: list[dict] = []

    async with async_playwright() as pw:
        context = await open_context(pw, headless=False)

        def on_request(req: Request) -> None:
            requests.append({
                "ts": datetime.now(timezone.utc).isoformat(),
                "method": req.method,
                "url": req.url,
                "resource_type": req.resource_type,
            })

        def on_response(resp: Response) -> None:
            for entry in reversed(requests):
                if entry["url"] == resp.url and "status" not in entry:
                    entry["status"] = resp.status
                    entry["response_content_type"] = resp.headers.get("content-type", "")
                    break

        context.on("request", on_request)
        context.on("response", on_response)

        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(BASE_URL, wait_until="domcontentloaded")
        # Don't wait for networkidle — Firestore long-polls after login and
        # we'd hang indefinitely. A short sleep lets Angular render the
        # login form into the DOM.
        await asyncio.sleep(2)

        (run_dir / "login.html").write_text(await page.content())
        (run_dir / "login.url").write_text(page.url + "\n")

        print()
        print("=" * 60)
        print(" LOG IN IN THE BROWSER.")
        print(" I'll proceed automatically once the URL leaves #/login.")
        print("=" * 60)
        print()

        await page.wait_for_url(
            lambda url: "#/login" not in url,
            timeout=LOGIN_TIMEOUT_MS,
        )
        # Brief settle, but no networkidle wait (Firestore now connected).
        await asyncio.sleep(2)

        print("Logged in. Session is cached in .playwright/user-data for reuse.")

        (run_dir / "landing.html").write_text(await page.content())
        (run_dir / "landing.url").write_text(page.url + "\n")

        print()
        print("Now click around the app in the browser: visit the Photos")
        print("section, the Calendar section, scroll a bit. When you're done,")
        print("come back here and press Enter to save the captures.")
        print()
        await asyncio.to_thread(input, "Press Enter when done... ")

        (run_dir / "final.html").write_text(await page.content())
        (run_dir / "final.url").write_text(page.url + "\n")
        (run_dir / "requests.json").write_text(json.dumps(requests, indent=2) + "\n")

        await context.close()

    print(f"\nProbes written to {run_dir.relative_to(run_dir.parents[2])}")


def main() -> None:
    asyncio.run(run())
