"""Persistence for the set of photo / event / message IDs we've already synced."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from pencil_extract.paths import SEEN_FILE, STATE_DIR


@dataclass
class Seen:
    photos: set[str] = field(default_factory=set)
    events: set[str] = field(default_factory=set)
    messages: set[str] = field(default_factory=set)


def load() -> Seen:
    if not SEEN_FILE.exists():
        return Seen()
    raw = json.loads(SEEN_FILE.read_text())
    return Seen(
        photos=set(raw.get("photos", [])),
        events=set(raw.get("events", [])),
        messages=set(raw.get("messages", [])),
    )


def save(seen: Seen) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "photos": sorted(seen.photos),
        "events": sorted(seen.events),
        "messages": sorted(seen.messages),
    }
    SEEN_FILE.write_text(json.dumps(payload, indent=2) + "\n")
