# pencil-extract

Pull photos, calendar events, and teacher messages out of the
[Pencil](https://www.pencilapp.net) school-family portal
(`familias.pencilapp.net`) into your own Google Drive / Calendar and into
a plain-text "what's new" summary you can scan.

Two stages:

1. **Extract** — a Python CLI logs in with Playwright, scrapes anything new
   since the last run (photos, events, messages), and writes it to `staging/`.
2. **Sync + summarize** — open Claude in this repo and say "sync" or
   "summarize". `SYNC.md` covers Drive/Calendar pushes; `SUMMARIZE.md` turns
   `staging/messages.json` into `staging/summary.md` bucketed by
   homework / permission slips / events / needs-response.

State of what's already been synced lives in `.state/seen.json`, committed to
the repo so the incremental diff works across machines.

## Setup

Requires Python 3.11+. On macOS, install via Homebrew (`brew install python`)
if you only have the system Python 3.9.

Homebrew Python won't let you `pip install` globally (PEP 668), so use a venv:

```bash
python3 -m venv .venv
source .venv/bin/activate           # every new shell; deactivate with `deactivate`

pip install -e .
playwright install chromium

cp .env.example .env
# fill PENCIL_EMAIL, PENCIL_PASSWORD, DRIVE_FOLDER_ID, CALENDAR_ID
```

Once the venv is active, plain `python` and `pip` work; `python -m pencil_extract …`
runs the CLI. The `.venv/` directory is gitignored.

## First run: discover selectors

The Pencil portal is a SPA we couldn't introspect ahead of time, so the
selectors and DOM structure in `auth.py` / `photos.py` / `events.py` start as
educated guesses. Run the inspect mode first — it opens a visible browser, logs
in, and dumps DOM snapshots + captured network requests into
`staging/_probes/`. Use the output to harden the extractors.

```bash
python -m pencil_extract inspect
```

## Regular use

```bash
# 1. Stage anything new since the last run
python -m pencil_extract extract

# 2. Open Claude in this repo and either:
#    - "sync" — pushes photos to Drive, events to Calendar, summarizes
#      messages, and updates .state/seen.json. Follows SYNC.md.
#    - "summarize" — just turns staging/messages.json into
#      staging/summary.md without touching Drive/Calendar. Follows SUMMARIZE.md.
```

Other commands:

```bash
python -m pencil_extract clear-staging   # wipe staging/ without touching state
```

## Files of note

- `src/pencil_extract/` — the package
- `.state/seen.json` — committed list of photo/event/message IDs already synced
- `staging/` — gitignored; populated by `extract`, drained by `sync`
- `.playwright/user-data/` — gitignored; persisted browser profile so we skip
  the login form when the cookie is still valid
- `SYNC.md` — Drive + Calendar push procedure (Claude follows this)
- `SUMMARIZE.md` — message-analysis procedure (Claude follows this)
