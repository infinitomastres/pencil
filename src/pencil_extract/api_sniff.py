"""Capture Pencil's XHR responses as the SPA navigates.

Pencil's UI is AngularJS 1.x but it talks to a clean JSON API at
`lts.pencilapp.net`. Rather than scraping the rendered DOM (brittle), we
attach a `response` listener and snapshot the JSON bodies of the endpoints
we care about as they fly past. The browser handles auth, pagination
triggering, and Cloudflare; we just collect what comes back.

Endpoints captured (from `inspect` probes):

- POST `/get-realted-users`        → the family: user + kids
- GET  `/locations/all/{schoolId}` → location names (referenced by id in
                                      events)
- GET  `/memory-maker/mykid-utc`   → photos feed, paginated
- GET  `/appointments-per-user`    → calendar events
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from playwright.async_api import Page, Response


@dataclass
class Capture:
    url: str
    body: Any


@dataclass
class Captures:
    photos_pages: list[Capture] = field(default_factory=list)
    events: list[Capture] = field(default_factory=list)
    related: list[Capture] = field(default_factory=list)
    locations: list[Capture] = field(default_factory=list)


class Sniffer:
    """Attaches to a Page and accumulates JSON from the endpoints we want."""

    def __init__(self, page: Page, raw_dir: Path | None = None):
        self.page = page
        self.raw_dir = raw_dir
        self.captures = Captures()
        self._counter = 0
        page.on("response", self._on_response)

    async def _on_response(self, response: Response) -> None:
        url = response.url
        try:
            if "/memory-maker/mykid-utc" in url:
                body = await response.json()
                self.captures.photos_pages.append(Capture(url, body))
                self._dump("memory-maker", url, body)
            elif "/appointments-per-user" in url:
                body = await response.json()
                self.captures.events.append(Capture(url, body))
                self._dump("appointments", url, body)
            elif "/get-realted-users" in url:
                body = await response.json()
                self.captures.related.append(Capture(url, body))
                self._dump("related-users", url, body)
            elif "/locations/all/" in url:
                body = await response.json()
                self.captures.locations.append(Capture(url, body))
                self._dump("locations", url, body)
        except Exception:
            # Response may have been consumed, body may not be JSON, network
            # may have flaked. The captures we got are what we got.
            pass

    def _dump(self, kind: str, url: str, body: Any) -> None:
        if not self.raw_dir:
            return
        self._counter += 1
        path = self.raw_dir / f"{self._counter:03d}-{kind}.json"
        try:
            path.write_text(json.dumps({"url": url, "body": body}, indent=2) + "\n")
        except (TypeError, ValueError):
            pass

    async def settle(self, seconds: float = 1.5) -> None:
        """Give in-flight responses a beat to land in handlers."""
        await asyncio.sleep(seconds)
