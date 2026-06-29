"""Calendar event extractor — works off the captured
`/appointments-per-user` response and the `/locations/all/{id}` lookup.

Like photos, we parse defensively and dump the raw body into `staging/_raw/`
so we can iterate when the shape doesn't match our guesses.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

from pencil_extract.api_sniff import Captures
from pencil_extract.paths import EVENTS_FILE, STAGING_DIR
from pencil_extract.state import Seen


@dataclass
class StagedEvent:
    id: str
    kid: str
    title: str
    description: str
    location: str
    start: str
    end: str
    all_day: bool
    source: str


def scrape(seen: Seen, captures: Captures) -> list[StagedEvent]:
    if not captures.events:
        print("WARNING: no /appointments-per-user responses captured — did the calendar load?")
        return []

    locations = _flatten_locations(captures)

    staged: list[StagedEvent] = []
    staged_ids: set[str] = set()
    for cap in captures.events:
        for raw in _as_list(cap.body):
            event = _parse(raw, locations)
            if not event:
                continue
            if event.id in seen.events or event.id in staged_ids:
                continue
            staged_ids.add(event.id)
            staged.append(event)

    _write_staging(staged)
    return staged


def _flatten_locations(captures: Captures) -> dict[str, str]:
    """Build location_id → name from any captured /locations/all responses.

    Pencil wraps the list as `{success: true, locations: [...]}`.
    """
    out: dict[str, str] = {}
    for cap in captures.locations:
        body = cap.body
        items: list = []
        if isinstance(body, dict):
            items = body.get("locations") or body.get("data") or []
        elif isinstance(body, list):
            items = body
        for loc in items:
            if not isinstance(loc, dict):
                continue
            lid = str(loc.get("id") or loc.get("location_id") or "")
            name = str(loc.get("name") or loc.get("title") or "")
            if lid and name:
                out[lid] = name
    return out


def _as_list(body: Any) -> list[Any]:
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        # appointments-per-user returns {success, datetime, appointments: [...]}
        for key in ("appointments", "data", "events", "items", "results"):
            v = body.get(key)
            if isinstance(v, list):
                return v
    return []


def _parse(raw: Any, locations: dict[str, str]) -> StagedEvent | None:
    if not isinstance(raw, dict):
        return None

    title = str(raw.get("title") or raw.get("name") or raw.get("subject") or "").strip()
    description = str(raw.get("description") or raw.get("notes") or "").strip()

    start = str(
        raw.get("start")
        or raw.get("start_date")
        or raw.get("startDate")
        or raw.get("fromDate")
        or raw.get("from")
        or ""
    )
    end = str(
        raw.get("end")
        or raw.get("end_date")
        or raw.get("endDate")
        or raw.get("toDate")
        or raw.get("to")
        or start
    )
    all_day = bool(raw.get("all_day") or raw.get("allDay") or raw.get("is_all_day"))

    loc_id = str(raw.get("location_id") or raw.get("location") or "")
    location = locations.get(loc_id, "") if loc_id else str(raw.get("place") or "")

    kid = ""
    kids = raw.get("kids")
    if isinstance(kids, list) and kids:
        first = kids[0]
        if isinstance(first, dict):
            kid = str(first.get("name") or first.get("id") or "")

    raw_id = raw.get("id") or raw.get("appointment_id") or raw.get("_id")
    if raw_id:
        event_id = str(raw_id)
    else:
        digest = hashlib.sha1(f"{title}|{start}|{kid}".encode()).hexdigest()
        event_id = digest[:16]

    if not title and not start:
        return None

    return StagedEvent(
        id=event_id,
        kid=kid,
        title=title,
        description=description,
        location=location,
        start=start,
        end=end,
        all_day=all_day,
        source="lts.pencilapp.net/appointments-per-user",
    )


def _write_staging(events: list[StagedEvent]) -> None:
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    existing: list[dict] = []
    if EVENTS_FILE.exists():
        try:
            existing = json.loads(EVENTS_FILE.read_text() or "[]")
        except json.JSONDecodeError:
            existing = []
    existing_ids = {e["id"] for e in existing if isinstance(e, dict)}
    merged = existing + [asdict(e) for e in events if e.id not in existing_ids]
    EVENTS_FILE.write_text(json.dumps(merged, indent=2) + "\n")
