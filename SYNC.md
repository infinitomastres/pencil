# Sync procedure (Claude follows this when asked to "sync")

Stage 2 of the pipeline. The Python extractor has already populated `staging/`
with new photos, events, and messages. This procedure handles photos and
events (Drive + Calendar via MCP). For the message-summarizing step see
[`SUMMARIZE.md`](./SUMMARIZE.md) — sync them as well by running the
summarize procedure on `staging/messages.json` and then appending the
processed message IDs to `state.messages`.

Read `.env` for `DRIVE_FOLDER_ID`, `CALENDAR_ID`, and `TIMEZONE`. If any of
those is missing, stop and ask the user.

## Steps

1. **Survey staging.**
   - Read `staging/events.json` (may be missing / empty array).
   - Glob `staging/photos/**/*.{jpg,jpeg,png,heic,webp,mp4,mov}` (case-insensitive)
     and pair each media file with its sidecar `<id>.json`.
   - Read `.state/seen.json` and confirm no staged ID is already listed there
     (if it is, something is off — flag it, don't double-upload).

2. **Create calendar events.**
   For each event in `staging/events.json`:
   - Call `mcp__Google_Calendar__create_event` with:
     - `calendarId` from `$CALENDAR_ID`
     - `summary` = event title (prefix with child name in brackets if present:
       `[Lucas] Excursión al museo`)
     - `description` = event description + a footer line `Source: <source_url>`
       if present
     - `location` if present
     - `start` / `end` with the timezone from `$TIMEZONE`
     - all-day events: use date-only start/end
   - On success, collect the event's stable ID (from the staging JSON, not the
     Google event ID — we dedupe on Pencil's side).
   - On failure, leave the entry in `staging/events.json` and report it; do
     **not** mark it seen.

3. **Upload photos.**
   For each media file:
   - Call `mcp__Google_Drive__create_file` into `$DRIVE_FOLDER_ID`, with:
     - filename = `<YYYY-MM-DD>_<child-slug>_<id>.<ext>` (date from sidecar)
     - description = sidecar `caption` if present, plus `Source: <source_url>`
   - On success, collect the photo's stable ID (from the sidecar).
   - On failure, leave the file + sidecar in place and report.

4. **Summarize messages.**
   - If `staging/messages.json` exists with entries, run the procedure in
     [`SUMMARIZE.md`](./SUMMARIZE.md): writes `staging/summary.md` and
     gives you the bucketed heads-up.
   - Collect the IDs of every message that appeared in the summary input
     (whether actionable or not — they're all "processed").

5. **Update state and clean staging.**
   - Append the successfully-uploaded photo IDs to `state.photos` in
     `.state/seen.json` (dedup, sorted).
   - Append the successfully-created event IDs to `state.events`.
   - Append the summarized message IDs to `state.messages`.
   - Save `.state/seen.json`.
   - Delete the successfully-synced files from `staging/` (keep failures so the
     next `extract` + `sync` cycle retries them). Leave `summary.md` in
     place so you can read it; it'll be overwritten by the next sync.

6. **Commit.**
   - Stage `.state/seen.json` only (staging is gitignored).
   - Commit with message: `sync: <N> photos, <M> events, <K> messages`.
   - Do **not** push automatically — leave that to the user.

7. **Report.**
   - One line: how many photos synced, events synced, messages summarized,
     plus any failures (with reasons).
