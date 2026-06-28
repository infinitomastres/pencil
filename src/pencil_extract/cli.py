"""Command-line entry points."""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys

from playwright.async_api import async_playwright, TimeoutError as PWTimeout

from pencil_extract import events, messages as messages_mod, photos
from pencil_extract import state as state_mod
from pencil_extract.api_sniff import Sniffer
from pencil_extract.auth import BASE_URL, ensure_logged_in, open_context
from pencil_extract.config import Config, ConfigError
from pencil_extract.paths import RAW_DIR, STAGING_DIR


SCROLL_STEPS = 60         # photo feed lazy-loads on scroll; cap iterations
SCROLL_PAUSE_MS = 500
XHR_WAIT_MS = 20_000      # bound how long we wait for a specific API call

# The SPA hydrates the user's kid list onto vm.totalKids during login.
# /get-realted-users returns OTHER parents/teachers (175+ rows), not the
# user's own children, so we read the kid list directly from the Angular
# scope. AngularJS 1.x lets us walk the scope tree via angular.element().
READ_KIDS_JS = """
() => {
  if (typeof angular === 'undefined') return null;
  const seen = new Set();
  const out = [];
  const selectors = ['.drawer-kids', '.drawer-content', '.maindiv', '[ui-view]', '[ng-app]', 'body'];
  for (const sel of selectors) {
    for (const el of document.querySelectorAll(sel)) {
      let scope = angular.element(el).scope();
      while (scope) {
        if (scope.vm && Array.isArray(scope.vm.totalKids)) {
          for (const k of scope.vm.totalKids) {
            const id = k && (k.id != null ? k.id : (k.kid_id != null ? k.kid_id : null));
            if (id != null && !seen.has(String(id))) {
              seen.add(String(id));
              out.push({
                id: String(id),
                name: String(k.name || k.lastname || id),
                location_id: k.location_id || k.locationId || k.daycare || null,
                raw: k,  // for debug dump
              });
            }
          }
          if (out.length) return out;
        }
        scope = scope.$parent;
      }
    }
  }
  return null;
}
"""

# Drive the SPA's own kid-switcher rather than guessing per-kid URL params.
# The Angular controller's changeKid(kid) handler triggers a fresh
# /memory-maker XHR with the correct kid-id AND the correct location-id for
# that kid — saves us reverse-engineering which kid lives where.
SWITCH_KID_JS = """
(targetId) => {
  if (typeof angular === 'undefined') return 'no-angular';
  const selectors = ['.drawer-kids', '.drawer-content', '.maindiv', '[ui-view]', '[ng-app]', 'body'];
  for (const sel of selectors) {
    for (const el of document.querySelectorAll(sel)) {
      let scope = angular.element(el).scope();
      while (scope) {
        if (scope.vm && Array.isArray(scope.vm.totalKids) && typeof scope.vm.changeKid === 'function') {
          const kid = scope.vm.totalKids.find(k => String(k.id) === String(targetId));
          if (!kid) return 'kid-not-found';
          scope.$apply(() => scope.vm.changeKid(kid));
          return 'ok';
        }
        scope = scope.$parent;
      }
    }
  }
  return 'no-scope';
}
"""

# Read messages from whatever scope exposes them. Multiple candidate field
# names because we don't yet know what Pencil's controller named the array.
READ_MESSAGES_JS = """
() => {
  if (typeof angular === 'undefined') return null;
  const selectors = ['.drawer-content', '.maindiv', '[ui-view]', '[ng-app]', 'body'];
  const keys = ['messages', 'mensajes', 'conversations', 'threads', 'inbox', 'pendingMessages'];
  for (const sel of selectors) {
    for (const el of document.querySelectorAll(sel)) {
      let scope = angular.element(el).scope();
      while (scope) {
        const vm = scope.vm;
        if (vm) {
          for (const key of keys) {
            const v = vm[key];
            if (Array.isArray(v) && v.length) {
              try { return JSON.parse(JSON.stringify(v)); } catch (e) { return null; }
            }
          }
        }
        scope = scope.$parent;
      }
    }
  }
  return null;
}
"""


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

        # Photos page — wait specifically for the memory-maker XHR rather
        # than networkidle (Firestore keeps the network busy forever).
        await _navigate_and_wait_for(
            page,
            url=f"{BASE_URL}#/home",
            xhr_substring="/memory-maker/mykid-utc",
            label="photos feed",
        )
        await _scroll_to_end(page)
        await sniffer.settle()

        spa_kids = await _read_kids_from_spa(page)
        _dump_kids_for_debug(spa_kids)

        # For each kid the SPA didn't fetch automatically, ask the SPA to
        # switch to them — that fires a new /memory-maker XHR with the right
        # location-id for that kid (which we'd otherwise have to guess).
        await _fetch_other_kids(page, sniffer, spa_kids)

        # Calendar page
        await _navigate_and_wait_for(
            page,
            url=f"{BASE_URL}#/agenda",
            xhr_substring="/appointments-per-user",
            label="calendar feed",
        )
        await sniffer.settle(2.0)

        # Messages page — we don't yet know the exact XHR shape, so
        # navigate without a strict expect_response and let the sniffer
        # catch anything matching the URL patterns we probe for.
        await _navigate_messages(page, sniffer)
        spa_messages = await _read_messages_from_spa(page)

        new_photos = await photos.scrape(page, seen, sniffer.captures, spa_kids=spa_kids)
        new_events = events.scrape(seen, sniffer.captures)
        new_messages = messages_mod.scrape(seen, sniffer.captures, spa_messages=spa_messages)

        await context.close()

    print(
        f"Staged {len(new_photos)} photos, {len(new_events)} events, "
        f"and {len(new_messages)} messages into staging/."
    )
    print('Next: ask Claude in this repo to "sync" or "summarize" (see SYNC.md / SUMMARIZE.md).')


async def _navigate_and_wait_for(
    page,
    *,
    url: str,
    xhr_substring: str,
    label: str,
) -> None:
    """Navigate (if needed) and wait for a specific XHR to fire.

    Hash-route navigations don't re-trigger window load events, so we
    can't rely on `wait_until="load"`. Instead we set up an `expect_response`
    around the navigation and tolerate the timeout (the XHR may have
    already fired during login if the SPA's landing route is the same).
    """
    same_url = page.url.rstrip("/") == url.rstrip("/")
    try:
        async with page.expect_response(
            lambda r: xhr_substring in r.url and r.status < 400,
            timeout=XHR_WAIT_MS,
        ):
            if not same_url:
                await page.goto(url, wait_until="domcontentloaded")
            # If we're already on the target URL the SPA may have fetched
            # this XHR during login. expect_response will catch it if it
            # fires within the timeout; otherwise we just move on.
    except PWTimeout:
        print(f"warning: {label} XHR ({xhr_substring}) did not fire within "
              f"{XHR_WAIT_MS // 1000}s — continuing with whatever the sniffer caught.")


async def _read_kids_from_spa(page) -> list[dict]:
    """Read vm.totalKids out of the Angular scope after the home page renders."""
    try:
        result = await page.evaluate(READ_KIDS_JS)
    except Exception as exc:
        print(f"could not read kids from SPA scope: {exc}")
        return []
    return result or []


async def _read_messages_from_spa(page) -> list[dict]:
    """Read whatever message array lives on the messages controller scope."""
    try:
        result = await page.evaluate(READ_MESSAGES_JS)
    except Exception as exc:
        print(f"could not read messages from SPA scope: {exc}")
        return []
    if not result:
        return []
    import json as _json
    try:
        (RAW_DIR / "_spa_messages.json").write_text(
            _json.dumps(result, indent=2, default=str) + "\n"
        )
    except Exception:
        pass
    return result


async def _navigate_messages(page, sniffer: Sniffer) -> None:
    """Navigate to /#/mensajes and let the sniffer catch whatever XHRs fire.

    We don't pin an expect_response on a specific URL fragment because we
    haven't yet confirmed which endpoints the messages controller hits —
    the sniffer's URL probe in api_sniff.py catches the candidates.
    """
    url = f"{BASE_URL}#/mensajes"
    try:
        if page.url.rstrip("/") != url.rstrip("/"):
            await page.goto(url, wait_until="domcontentloaded")
    except PWTimeout:
        print("warning: /#/mensajes navigation timed out")
        return
    # Generous settle — messaging often fans out into multiple XHRs +
    # Firestore Listen reconnects, all of which we want to observe.
    await sniffer.settle(4.0)
    await _scroll_to_end(page)
    await sniffer.settle(2.0)


def _dump_kids_for_debug(spa_kids: list[dict]) -> None:
    if not spa_kids:
        return
    import json
    path = RAW_DIR / "_spa_kids.json"
    try:
        path.write_text(json.dumps(spa_kids, indent=2, default=str) + "\n")
    except Exception:
        pass


async def _fetch_other_kids(page, sniffer: Sniffer, spa_kids: list[dict]) -> None:
    """Trigger the SPA to fetch photos for each kid we haven't already sniffed."""
    if not spa_kids:
        return

    already_fetched = _sniffed_kid_ids(sniffer.captures.photos_pages)
    targets = [k for k in spa_kids if str(k["id"]) not in already_fetched]
    if not targets:
        return

    print(f"Switching SPA to fetch photos for {len(targets)} other kid(s): "
          f"{[k.get('name') for k in targets]}")

    for kid in targets:
        kid_id = str(kid["id"])
        kid_name = kid.get("name") or kid_id
        try:
            async with page.expect_response(
                lambda r: "/memory-maker/mykid-utc" in r.url and r.status < 400,
                timeout=XHR_WAIT_MS,
            ):
                result = await page.evaluate(SWITCH_KID_JS, kid_id)
                if result != "ok":
                    print(f"  {kid_name}: changeKid returned {result!r}, skipping")
                    continue
        except PWTimeout:
            print(f"  {kid_name}: no memory-maker XHR after switch — skipping")
            continue
        await _scroll_to_end(page)
        await sniffer.settle()


def _sniffed_kid_ids(photos_pages) -> set[str]:
    from urllib.parse import parse_qs, urlsplit
    ids: set[str] = set()
    for cap in photos_pages:
        params = parse_qs(urlsplit(cap.url).query)
        kid = (params.get("kid-id") or [""])[0]
        if kid:
            ids.add(kid)
    return ids


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
