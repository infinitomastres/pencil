"""Command-line entry points."""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys

from playwright.async_api import async_playwright

from pencil_extract import events, photos, state
from pencil_extract.auth import ensure_logged_in, open_context
from pencil_extract.config import Config, ConfigError
from pencil_extract.paths import STAGING_DIR


async def _extract() -> None:
    config = Config.from_env()
    seen = state.load()
    STAGING_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        context = await open_context(pw, headless=True)
        page = await ensure_logged_in(context, config)

        new_photos = await photos.scrape(page, seen)
        new_events = await events.scrape(page, seen)

        await context.close()

    print(f"Staged {len(new_photos)} photos and {len(new_events)} events into staging/.")
    print('Next: ask Claude in this repo to "sync" (see SYNC.md).')


def _clear_staging() -> None:
    if STAGING_DIR.exists():
        shutil.rmtree(STAGING_DIR)
    print("Cleared staging/.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="pencil-extract")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("extract", help="Log in, scrape, stage anything new since last run")
    sub.add_parser("inspect", help="Open a visible browser and dump DOM/network probes")
    sub.add_parser("clear-staging", help="Wipe staging/ without touching .state/seen.json")

    args = parser.parse_args(argv)

    try:
        if args.cmd == "extract":
            asyncio.run(_extract())
        elif args.cmd == "inspect":
            from pencil_extract.inspect import main as inspect_main
            inspect_main()
        elif args.cmd == "clear-staging":
            _clear_staging()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
