"""One-time discovery helper.

Opens a visible browser, logs in, and captures the post-login DOM plus all
network requests for a short observation window. The output lands in
`.state/_probes/` (gitignored) so we can read it back and harden the real
extractors in `photos.py` / `events.py`.

Usage:
    python -m pencil_extract inspect
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from playwright.async_api import Request, Response, async_playwright

from pencil_extract.auth import ensure_logged_in, open_context
from pencil_extract.config import Config
from pencil_extract.paths import PROBES_DIR

OBSERVATION_SECONDS = 20


async def run() -> None:
    config = Config.from_env()
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

        page = await ensure_logged_in(context, config)
        print(f"Logged in. Recording for {OBSERVATION_SECONDS}s. Click around (Photos, Calendar) in the browser.")
        await asyncio.sleep(OBSERVATION_SECONDS)

        dom_html = await page.content()
        (run_dir / "landing.html").write_text(dom_html)
        (run_dir / "landing.url").write_text(page.url + "\n")
        (run_dir / "requests.json").write_text(json.dumps(requests, indent=2) + "\n")

        await context.close()

    print(f"Probes written to {run_dir}")


def main() -> None:
    asyncio.run(run())
