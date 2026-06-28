"""Photo extractor — works off captured `/memory-maker/mykid-utc` responses.

The SPA paginates with `skip`/`take` query params. We use whatever the
browser already requested as our anchor (so we get the right
`kid-id`/`location-id`/`user_utc` without having to reconstruct them from
the related-users response), then paginate forward with `page.request.get`
until a short page indicates the end.

The exact JSON shape isn't documented anywhere — we parse defensively and
fall back to writing the raw body to `staging/_raw/` so we can debug if
something doesn't match.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from playwright.async_api import Page

from pencil_extract.api_sniff import Capture, Captures
from pencil_extract.paths import PHOTOS_DIR
from pencil_extract.state import Seen

PAGE_SIZE = 35
MEMORY_MAKER_PATH = "/memory-maker/mykid-utc"


@dataclass
class StagedPhoto:
    id: str
    kid: str
    media_url: str
    caption: str
    taken_at: str
    saved_path: str


async def scrape(
    page: Page,
    seen: Seen,
    captures: Captures,
    spa_kids: list[dict] | None = None,
) -> list[StagedPhoto]:
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)

    if not captures.photos_pages:
        print("WARNING: no /memory-maker responses captured — did the photos page load?")
        return []

    sniffed_anchors = _anchors(captures.photos_pages)
    if not sniffed_anchors:
        print("WARNING: couldn't parse query params from sniffed photo URLs.")
        return []

    # Use the first sniffed (location-id, user_utc) as defaults for kids the
    # SPA didn't itself fetch — they're per-account, not per-kid.
    first_anchor = next(iter(sniffed_anchors))
    default_loc, default_utc = first_anchor[1], first_anchor[2]
    sniffed_by_kid = {a[0]: (a[1], a[2], max_skip)
                      for a, max_skip in sniffed_anchors.items()}

    # Kid IDs come from vm.totalKids in the Angular scope (read by cli.py
    # via page.evaluate). Fall back to whatever the SPA itself fetched.
    if spa_kids:
        kid_ids = [str(k["id"]) for k in spa_kids if k.get("id") is not None]
        print(f"Found {len(kid_ids)} kid(s) in SPA scope: "
              f"{[k.get('name') or k.get('id') for k in spa_kids]}")
    else:
        kid_ids = list(sniffed_by_kid.keys())
        print(f"SPA scope unreadable — falling back to sniffed kid id(s): {kid_ids}")

    all_memories: list[Any] = []
    # Start with what the SPA already gave us so we don't re-fetch those pages.
    for cap in captures.photos_pages:
        all_memories.extend(_as_list(cap.body))

    for kid_id in kid_ids:
        if kid_id in sniffed_by_kid:
            loc_id, utc, max_skip = sniffed_by_kid[kid_id]
            skip = max_skip + PAGE_SIZE   # continue paginating where the SPA stopped
        else:
            loc_id, utc, skip = default_loc, default_utc, 0  # start from scratch

        while True:
            url = _build_url(kid_id, loc_id, utc, skip)
            resp = await page.request.get(url)
            if not resp.ok:
                if skip == 0:
                    print(f"  kid {kid_id}: HTTP {resp.status} — skipping")
                break
            try:
                body = await resp.json()
            except Exception:
                break
            page_items = _as_list(body)
            if not page_items:
                break
            all_memories.extend(page_items)
            # Pencil returns memories newest-first. Once a whole page is
            # all-already-known (in seen, or already on disk from a prior
            # extract), assume older pages are too and stop paginating.
            if _all_records_known(page_items, seen):
                break
            if len(page_items) < PAGE_SIZE:
                break
            skip += PAGE_SIZE

    staged: list[StagedPhoto] = []
    staged_ids: set[str] = set()
    skipped_existing = 0
    for memory in all_memories:
        for record in _expand(memory):
            photo_id = record["id"]
            if photo_id in seen.photos or photo_id in staged_ids:
                continue
            if not record["url"]:
                continue
            staged_ids.add(photo_id)
            # Already on disk from a previous extract that hasn't been
            # synced yet? Skip the download but don't count it as "new".
            if _on_disk(record["kid"], photo_id):
                skipped_existing += 1
                continue
            saved = await _download(page, record["url"], record["kid"], photo_id)
            if not saved:
                continue
            _write_sidecar(saved, record)
            staged.append(StagedPhoto(
                id=photo_id,
                kid=record["kid"],
                media_url=record["url"],
                caption=record["caption"],
                taken_at=record["taken_at"],
                saved_path=str(saved.relative_to(PHOTOS_DIR.parent.parent)),
            ))

    if skipped_existing:
        print(f"  skipped {skipped_existing} photo(s) already on disk from a prior extract")

    return staged


# ---------- helpers ----------

def _as_list(body: Any) -> list[Any]:
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in ("data", "memories", "photos", "items", "results"):
            v = body.get(key)
            if isinstance(v, list):
                return v
    return []


def _anchors(caps: list[Capture]) -> dict[tuple[str, str, str], int]:
    """Group sniffed URLs by (kid-id, location-id, user_utc) → max skip seen."""
    out: dict[tuple[str, str, str], int] = {}
    for cap in caps:
        params = parse_qs(urlsplit(cap.url).query)
        kid = (params.get("kid-id") or [""])[0]
        loc = (params.get("location-id") or [""])[0]
        utc = (params.get("user_utc") or [""])[0]
        skip = int((params.get("skip") or ["0"])[0] or 0)
        key = (kid, loc, utc)
        if skip > out.get(key, -1):
            out[key] = skip
    return out


def _on_disk(kid: str, photo_id: str) -> bool:
    """True if this photo's sidecar JSON already exists under staging/photos/<kid>/."""
    kid_dir = PHOTOS_DIR / (_slug(kid) or "default")
    if not kid_dir.exists():
        return False
    return any(kid_dir.glob(f"{photo_id}.*.json"))


def _all_records_known(memories: list[Any], seen: Seen) -> bool:
    """True if every photo record across these memories is already known
    (synced or staged on disk). Used to short-circuit pagination."""
    for mem in memories:
        for rec in _expand(mem):
            if rec["id"] in seen.photos:
                continue
            if _on_disk(rec["kid"], rec["id"]):
                continue
            return False
    return True




def _build_url(kid_id: str, loc_id: str, utc: str, skip: int) -> str:
    qs = urlencode({
        "skip": skip,
        "take": PAGE_SIZE,
        "kid-id": kid_id,
        "location-id": loc_id,
        "user_utc": utc,
    })
    return f"https://lts.pencilapp.net{MEMORY_MAKER_PATH}?{qs}"


def _expand(memory: Any) -> list[dict[str, str]]:
    """Turn one memory record into one or more (photo URL, metadata) rows.

    A memory may contain a single photo, multiple photos, or a video. The
    HTML hinted at `vm.memory.type == 'P'` vs 'V', `vm.memory.photos[0].photo`,
    and `vm.memory.kids[0]`. We probe several likely field names.
    """
    if not isinstance(memory, dict):
        return []

    mem_id = str(memory.get("id") or memory.get("_id") or "")
    kid = _kid_name(memory) or "default"
    caption = str(memory.get("caption") or memory.get("text") or memory.get("description") or "")
    taken_at = str(memory.get("date") or memory.get("created_at") or memory.get("createdAt") or "")

    mem_type = (memory.get("type") or "").upper()
    urls: list[tuple[str, str]] = []  # (url, sub_id)

    if mem_type == "V":
        v = memory.get("video") or memory.get("photo") or memory.get("url")
        if v:
            urls.append((str(v), "v"))
    else:
        photos = memory.get("photos")
        if isinstance(photos, list):
            for i, p in enumerate(photos):
                url = p.get("photo") if isinstance(p, dict) else p
                if url:
                    urls.append((str(url), str(p.get("id") or i) if isinstance(p, dict) else str(i)))
        else:
            for key in ("photo", "url", "media"):
                v = memory.get(key)
                if v:
                    urls.append((str(v), "0"))
                    break

    rows = []
    for url, sub in urls:
        photo_id = f"{mem_id}-{sub}" if mem_id else hashlib.sha1(url.encode()).hexdigest()[:16]
        rows.append({
            "id": photo_id,
            "memory_id": mem_id,
            "kid": kid,
            "url": url,
            "caption": caption,
            "taken_at": taken_at,
            "type": mem_type or "P",
        })
    return rows


def _kid_name(memory: dict[str, Any]) -> str:
    kids = memory.get("kids")
    if isinstance(kids, list) and kids:
        first = kids[0]
        if isinstance(first, dict):
            return str(first.get("name") or first.get("kid_name") or first.get("id") or "")
    for key in ("kid_name", "kid", "name"):
        v = memory.get(key)
        if v:
            return str(v)
    return ""


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(value: str) -> str:
    return _SLUG_RE.sub("-", value.lower()).strip("-") or "default"


def _guess_ext(url: str, content_type: str) -> str:
    path = urlsplit(url).path
    if "." in path:
        candidate = path.rsplit(".", 1)[-1]
        if 1 <= len(candidate) <= 5:
            return f".{candidate.lower()}"
    return mimetypes.guess_extension((content_type or "").split(";")[0].strip()) or ".bin"


async def _download(page: Page, url: str, kid: str, photo_id: str) -> Path | None:
    try:
        response = await page.request.get(url)
        if not response.ok:
            print(f"download {photo_id}: HTTP {response.status} for {url}")
            return None
        body = await response.body()
        ext = _guess_ext(url, response.headers.get("content-type", ""))
        kid_dir = PHOTOS_DIR / _slug(kid)
        kid_dir.mkdir(parents=True, exist_ok=True)
        path = kid_dir / f"{photo_id}{ext}"
        path.write_bytes(body)
        return path
    except Exception as exc:
        print(f"download {photo_id}: {exc}")
        return None


def _write_sidecar(media_path: Path, record: dict[str, str]) -> None:
    sidecar = media_path.with_suffix(media_path.suffix + ".json")
    sidecar.write_text(json.dumps(record, indent=2) + "\n")
