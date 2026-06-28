"""Command-line entry points."""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys

from playwright.async_api import async_playwright

from pencil_extract import events, photos
from pencil_extract import state as state_mod
from pencil_extract.api_sniff import Sniffer
from pencil_extract.auth import BASE_URL, ensure_logged_in, open_context
from pencil_extract.config import Config, ConfigError
from pencil_extract.paths import RAW_DIR, STAGING_DIR


SCROLL_STEPS = 60         # photo feed lazy-loads on scroll; cap iterations
SCROLL_PAUSE_MS = 500


async def _extract(headless: bool) -> None:
    config = Config.from_env()
    seen = state_mod.load()
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    if RAW_DIR.exists():
        shutil.rmtree(RAW_DIR)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        context = await open_context(pw, headless=headless)
        page = context.pages[0] if context.pages else await context.new_page()

        sniffer = Sniffer(page, raw_dir=RAW_DIR)
        await ensure_logged_in(context, config)

        # Photos page — drive the SPA to fetch the first page(s), then we
        # paginate forward from sniffed URLs.
        await page.goto(f"{BASE_URL}#/home", wait_until="networkidle")
        await _scroll_to_end(page)
        await sniffer.settle()

        # Calendar page
        await page.goto(f"{BASE_URL}#/agenda", wait_until="networkidle")
        await sniffer.settle(2.0)

        new_photos = await photos.scrape(page, seen, sniffer.captures)
        new_events = events.scrape(seen, sniffer.captures)

        await context.close()

    print(f"Staged {len(new_photos)} photos and {len(new_events)} events into staging/.")
    print('Next: ask Claude in this repo to "sync" (see SYNC.md).')


async def _scroll_to_end(page) -> None:
    last_height = 0
    for _ in range(SCROLL_STEPS):
        height = await page.evaluate("document.body.scrollHeight")
        if height == last_height:
            return
        last_height = height
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(SCROLL_PAUSE_MS)


def _clear_staging() -> None:
    if STAGING_DIR.exists():
        shutil.rmtree(STAGING_DIR)
    print("Cleared staging/.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="pencil-extract")
    sub = parser.add_subparsers(dest="cmd", required=True)
    # extract defaults to headed mode during bring-up — Cloudflare flags
    # headless Chrome harder than headed. Once the API parsing is proven,
    # flip the default to headless for unattended scheduled runs.
    ext = sub.add_parser("extract", help="Log in, scrape via API sniffing, stage new items")
    ext.add_argument(
        "--headless",
        action="store_true",
        help="Run without a visible browser window. May 403 on Cloudflare; "
             "use only after the extractor is otherwise stable.",
    )
    sub.add_parser("inspect", help="Open a visible browser and dump DOM/network probes")
    sub.add_parser("clear-staging", help="Wipe staging/ without touching .state/seen.json")

    args = parser.parse_args(argv)

    try:
        if args.cmd == "extract":
            asyncio.run(_extract(headless=args.headless))
        elif args.cmd == "inspect":
            from pencil_extract.inspect import main as inspect_main
            inspect_main()
        elif args.cmd == "clear-staging":
            _clear_staging()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
