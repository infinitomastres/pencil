"""Calendar scraper.

Placeholder selectors and navigation — to be refined from inspect output.
The output shape (a list of normalized event dicts written to
`staging/events.json`) is stable and what `SYNC.md` consumes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from playwright.async_api import Page

from pencil_extract.paths import EVENTS_FILE, STAGING_DIR
from pencil_extract.state import Seen

CALENDAR_ROUTE = "https://familias.pencilapp.net/#/calendar"

EVENT_SELECTOR = '[data-testid="calendar-event"], .calendar-event, .event-item'


@dataclass
class StagedEvent:
    id: str
    child: str
    title: str
    description: str
    location: str
    start: str  # ISO-8601
    end: str    # ISO-8601
    all_day: bool
    source_url: str


async def scrape(page: Page, seen: Seen) -> list[StagedEvent]:
    await page.goto(CALENDAR_ROUTE, wait_until="networkidle")

    staged: list[StagedEvent] = []
    for el in await page.query_selector_all(EVENT_SELECTOR):
        title = (await el.get_attribute("data-title")) or (await el.inner_text() or "").strip()
        start = await el.get_attribute("data-start") or ""
        end = await el.get_attribute("data-end") or start
        child = await el.get_attribute("data-child") or ""
        raw_id = await el.get_attribute("data-id")

        event_id = raw_id or hashlib.sha1(f"{title}|{start}|{child}".encode()).hexdigest()[:16]
        if event_id in seen.events:
            continue

        staged.append(StagedEvent(
            id=event_id,
            child=child,
            title=title,
            description=await el.get_attribute("data-description") or "",
            location=await el.get_attribute("data-location") or "",
            start=start,
            end=end,
            all_day=(await el.get_attribute("data-all-day") or "").lower() in {"true", "1"},
            source_url=page.url,
        ))

    _append(staged)
    return staged


def _append(new_events: list[StagedEvent]) -> None:
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    existing: list[dict] = []
    if EVENTS_FILE.exists():
        existing = json.loads(EVENTS_FILE.read_text() or "[]")
    existing_ids = {e["id"] for e in existing}
    merged = existing + [asdict(e) for e in new_events if e.id not in existing_ids]
    EVENTS_FILE.write_text(json.dumps(merged, indent=2) + "\n")
