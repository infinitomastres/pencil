"""Messages extractor — works off captured `/get-staff-pending-messages`
(and similar) responses, plus whatever we can read from `vm.messages` in
the Angular scope.

The exact message API contract isn't documented and we haven't yet seen a
real response body — the first extract run with `/#/mensajes` navigation
will populate `staging/_raw/*-messages.json` for us to iterate against.
This parser is intentionally lenient: probes several plausible field
names, drops anything truly malformed, keeps the rest.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

from pencil_extract.api_sniff import Captures
from pencil_extract.paths import MESSAGES_FILE, STAGING_DIR
from pencil_extract.state import Seen


@dataclass
class StagedMessage:
    id: str
    kid: str
    sender: str
    sender_role: str
    sent_at: str
    subject: str
    body: str
    thread_id: str
    is_read: bool
    attachments: list[str] = field(default_factory=list)
    source: str = "lts.pencilapp.net/get-staff-pending-messages"


def scrape(
    seen: Seen,
    captures: Captures,
    spa_messages: list[dict] | None = None,
) -> list[StagedMessage]:
    """Combine REST sniffs and SPA-scope reads, dedupe, write staging file."""
    candidates: list[Any] = []
    for cap in captures.messages:
        candidates.extend(_as_list(cap.body))
    if spa_messages:
        candidates.extend(spa_messages)

    if not candidates:
        print("WARNING: no messages captured — /#/mensajes endpoint shape may differ.")
        return []

    staged: list[StagedMessage] = []
    staged_ids: set[str] = set()
    for raw in candidates:
        msg = _parse(raw)
        if not msg:
            continue
        if msg.id in seen.messages or msg.id in staged_ids:
            continue
        staged_ids.add(msg.id)
        staged.append(msg)

    _write_staging(staged)
    return staged


# ---------- helpers ----------

def _as_list(body: Any) -> list[Any]:
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in (
            "messages", "mensajes", "data",
            "threads", "conversations", "items",
            "pending", "inbox", "results",
        ):
            v = body.get(key)
            if isinstance(v, list):
                return v
    return []


def _parse(raw: Any) -> StagedMessage | None:
    if not isinstance(raw, dict):
        return None

    # Try several field name conventions before giving up
    body_text = _first_str(raw, "body", "text", "content", "message", "mensaje", "description")
    subject = _first_str(raw, "subject", "title", "asunto")
    sent_at = _first_str(
        raw, "sent_at", "sentAt", "created_at", "createdAt",
        "date", "fecha", "timestamp",
    )

    sender = ""
    sender_role = ""
    sender_obj = raw.get("sender") or raw.get("from") or raw.get("user") or raw.get("autor")
    if isinstance(sender_obj, dict):
        sender = _first_str(sender_obj, "name", "fullname", "full_name", "displayName")
        sender_role = _first_str(sender_obj, "role", "charge", "rol")
    else:
        sender = _first_str(raw, "sender_name", "from_name", "user_name", "author")
        sender_role = _first_str(raw, "sender_role", "from_role", "user_role")

    kid = ""
    kid_obj = raw.get("kid") or raw.get("child")
    if isinstance(kid_obj, dict):
        kid = _first_str(kid_obj, "name", "fullname")
    else:
        kids_list = raw.get("kids") or raw.get("children")
        if isinstance(kids_list, list) and kids_list and isinstance(kids_list[0], dict):
            kid = _first_str(kids_list[0], "name", "fullname")
        else:
            kid = _first_str(raw, "kid_name", "child_name")

    thread_id = _first_str(raw, "thread_id", "threadId", "conversation_id", "conversationId")
    is_read = bool(raw.get("is_read") or raw.get("isRead") or raw.get("read"))

    attachments: list[str] = []
    atts = raw.get("attachments") or raw.get("files") or raw.get("media")
    if isinstance(atts, list):
        for a in atts:
            if isinstance(a, str):
                attachments.append(a)
            elif isinstance(a, dict):
                url = _first_str(a, "url", "href", "src", "file")
                if url:
                    attachments.append(url)

    raw_id = (
        raw.get("id")
        or raw.get("_id")
        or raw.get("message_id")
        or raw.get("messageId")
    )
    if raw_id:
        msg_id = str(raw_id)
    elif sent_at or body_text:
        digest = hashlib.sha1(f"{sent_at}|{sender}|{body_text[:120]}".encode()).hexdigest()
        msg_id = digest[:16]
    else:
        return None

    if not body_text and not subject:
        return None

    return StagedMessage(
        id=msg_id,
        kid=kid,
        sender=sender,
        sender_role=sender_role,
        sent_at=sent_at,
        subject=subject,
        body=body_text,
        thread_id=thread_id,
        is_read=is_read,
        attachments=attachments,
    )


def _first_str(d: dict, *keys: str) -> str:
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return str(v)
    return ""


def _write_staging(messages: list[StagedMessage]) -> None:
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    existing: list[dict] = []
    if MESSAGES_FILE.exists():
        try:
            existing = json.loads(MESSAGES_FILE.read_text() or "[]")
        except json.JSONDecodeError:
            existing = []
    existing_ids = {m["id"] for m in existing if isinstance(m, dict)}
    merged = existing + [asdict(m) for m in messages if m.id not in existing_ids]
    MESSAGES_FILE.write_text(json.dumps(merged, indent=2) + "\n")
