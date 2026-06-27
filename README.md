# pencil-extract

Pull photos and calendar events out of the [Pencil](https://www.pencilapp.net)
school-family portal (`familias.pencilapp.net`) and into your own Google Drive
and Google Calendar.

Two stages:

1. **Extract** — a Python CLI logs in with a headless browser, scrapes anything
   new since the last run, and writes it to `staging/`.
2. **Sync** — you ask Claude (in a session opened in this repo) to push the
   staged artifacts to Drive/Calendar via the Google MCP servers. See
   [`SYNC.md`](./SYNC.md) for the exact procedure Claude follows.

State of what's already been synced lives in `.state/seen.json`, committed to
the repo so the incremental diff works across machines.

## Setup

```bash
pip install -e .
playwright install chromium

cp .env.example .env
# fill PENCIL_EMAIL, PENCIL_PASSWORD, DRIVE_FOLDER_ID, CALENDAR_ID
```

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

# 2. Open Claude in this repo and say: "sync the staged stuff."
#    Claude follows SYNC.md: uploads photos to Drive, creates Calendar events,
#    updates .state/seen.json, clears staging/, and commits.
```

Other commands:

```bash
python -m pencil_extract clear-staging   # wipe staging/ without touching state
```

## Files of note

- `src/pencil_extract/` — the package
- `.state/seen.json` — committed list of photo/event IDs already synced
- `staging/` — gitignored; populated by `extract`, drained by `sync`
- `.playwright/storage.json` — gitignored; persisted session so we skip the
  login form when the cookie is still valid
- `SYNC.md` — the procedure Claude runs during stage 2
