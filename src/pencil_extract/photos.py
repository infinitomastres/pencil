"""Photo feed scraper.

The selectors here are placeholders — we cannot know the real DOM until the
first `inspect` run. The shape of the code (iterate children, walk feed, diff
against seen, stage with sidecar metadata) is what we'll keep; selectors and
the navigation routes are the parts to update.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from playwright.async_api import Page

from pencil_extract.paths import PHOTOS_DIR
from pencil_extract.state import Seen

PHOTOS_ROUTE = "https://familias.pencilapp.net/#/photos"

# Placeholders — refine from `staging/_probes/<run>/landing.html`.
PHOTO_CARD_SELECTOR = '[data-testid="photo-card"], article.photo, .photo-item'
PHOTO_IMG_SELECTOR = "img"
PHOTO_ID_ATTR = "data-id"


@dataclass
class StagedPhoto:
    id: str
    child: str
    url: str
    caption: str
    taken_at: str  # ISO-8601 or empty


async def scrape(page: Page, seen: Seen) -> list[StagedPhoto]:
    """Walk the photo feed for every child profile, staging anything new."""
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    staged: list[StagedPhoto] = []

    for child in await _list_children(page):
        await _select_child(page, child)
        await page.goto(PHOTOS_ROUTE, wait_until="networkidle")
        await _scroll_to_end(page)

        for card in await page.query_selector_all(PHOTO_CARD_SELECTOR):
            raw_id = await card.get_attribute(PHOTO_ID_ATTR)
            img = await card.query_selector(PHOTO_IMG_SELECTOR)
            src = await img.get_attribute("src") if img else None
            if not src:
                continue
            photo_id = raw_id or hashlib.sha1(src.encode()).hexdigest()[:16]
            if photo_id in seen.photos:
                continue

            caption = (await card.inner_text() or "").strip()
            taken_at = await card.get_attribute("data-date") or ""

            saved_path = await _download(page, src, child, photo_id)
            _write_sidecar(saved_path, photo_id, child, src, caption, taken_at)
            staged.append(StagedPhoto(
                id=photo_id, child=child, url=src, caption=caption, taken_at=taken_at,
            ))

    return staged


async def _list_children(page: Page) -> list[str]:
    """Return a list of child profile slugs to iterate.

    Placeholder: returns a single empty slug so the caller still runs. After
    inspect we'll fill this in (children are usually listed in a sidebar or
    profile-switcher menu).
    """
    return [""]


async def _select_child(page: Page, child: str) -> None:
    """Switch the UI to the given child profile. No-op for the placeholder."""
    return None


async def _scroll_to_end(page: Page, max_iters: int = 50) -> None:
    """Force lazy-loaded feeds to render everything we can see."""
    last_height = 0
    for _ in range(max_iters):
        height = await page.evaluate("document.body.scrollHeight")
        if height == last_height:
            return
        last_height = height
        await page.mouse.wheel(0, height)
        await page.wait_for_timeout(400)


async def _download(page: Page, url: str, child: str, photo_id: str):
    response = await page.request.get(url)
    body = await response.body()
    ext = _guess_ext(url, response.headers.get("content-type", ""))
    child_dir = PHOTOS_DIR / (_slug(child) or "default")
    child_dir.mkdir(parents=True, exist_ok=True)
    path = child_dir / f"{photo_id}{ext}"
    path.write_bytes(body)
    return path


def _write_sidecar(
    media_path, photo_id: str, child: str, src: str, caption: str, taken_at: str
) -> None:
    sidecar = media_path.with_suffix(media_path.suffix + ".json")
    sidecar.write_text(json.dumps({
        "id": photo_id,
        "child": child,
        "source_url": src,
        "caption": caption,
        "taken_at": taken_at,
    }, indent=2) + "\n")


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _guess_ext(url: str, content_type: str) -> str:
    path = urlparse(url).path
    for candidate in (path.rsplit(".", 1)[-1] if "." in path else "",):
        if candidate and len(candidate) <= 5:
            return f".{candidate.lower()}"
    return mimetypes.guess_extension(content_type.split(";")[0].strip()) or ".bin"
