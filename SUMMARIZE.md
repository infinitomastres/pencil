# Summarize procedure (Claude follows this when asked to "summarize" /
# "analyze messages" / "what's new")

Reads `staging/messages.json` (the messages the last `extract` run pulled
from Pencil's Mensajes section) and writes `staging/summary.md` — a
plain-text heads-up of anything actionable.

## Steps

1. **Read the staged messages.**
   - Open `staging/messages.json` (may be missing or empty — if so, write a
     `summary.md` that just says "No new messages.", stop here).
   - Each entry has: `id, kid, sender, sender_role, sent_at, subject, body,
     thread_id, is_read, attachments`. `body` may contain HTML; strip tags
     mentally when classifying but quote interesting snippets verbatim.

2. **Bucket each message into zero or more of these four categories.**
   A single message can land in multiple. Skip messages that don't fit
   any category — they're informational chatter, not actionable.

   - **Homework / assignments** — anything that mentions tareas, deberes,
     proyecto, due dates, "para mañana", "para el viernes", etc.
   - **Permission slips / forms to sign** — autorizaciones, formularios,
     salidas/excursiones que requieren firma, pagos pendientes.
   - **Upcoming events / dates to remember** — excursiones, festivos,
     "día del…", reuniones de padres, performances, photo day.
   - **Things requiring your response** — direct questions to the parent,
     RSVPs (confirmar asistencia), head counts, "por favor responder",
     anything where silence has a cost.

3. **Write `staging/summary.md`** with this skeleton:

   ```markdown
   # Pencil heads-up — <today's date in your timezone>

   ## Homework / assignments
   - [Kid name] <one-line summary>. — Sender, sent <date>.

   ## Permission slips / forms
   - …

   ## Upcoming events / dates to remember
   - …

   ## Needs your response
   - …
   ```

   - Each bullet: kid name in brackets, one-line summary in English,
     attribution (sender + send date) at the end.
   - When the key signal is Spanish — names, places, exact dates, the
     exact phrase a teacher used — quote it verbatim. Don't translate
     "Mi primer Kinder" or "Profesora Johanna".
   - Drop entire sections that have no items. Don't pad with "none".
   - If nothing is actionable across all messages, the whole summary is
     just: `No actionable items in N messages.`

4. **Don't modify `.state/seen.json`.** Summarization is read-only;
   message dedupe happens at extract time. Re-reading the same messages
   produces the same summary (idempotent).

5. **Report one line back to the user** — message count + section
   counts. Example: `Summarized 42 messages → 3 homework, 1 permission
   slip, 2 events, 1 needs response. See staging/summary.md.`
