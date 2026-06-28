"""Filesystem paths used across the package. All resolved from the repo root."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

STATE_DIR = REPO_ROOT / ".state"
SEEN_FILE = STATE_DIR / "seen.json"
PROBES_DIR = STATE_DIR / "_probes"

STAGING_DIR = REPO_ROOT / "staging"
PHOTOS_DIR = STAGING_DIR / "photos"
EVENTS_FILE = STAGING_DIR / "events.json"
MESSAGES_FILE = STAGING_DIR / "messages.json"
SUMMARY_FILE = STAGING_DIR / "summary.md"
RAW_DIR = STAGING_DIR / "_raw"

PLAYWRIGHT_DIR = REPO_ROOT / ".playwright"
