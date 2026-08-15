"""
backend/session_store.py

Durable session + handover-queue persistence (PRD §4.5, and the exact
schema promised to the frontend in docs/FRONTEND_HANDOFF.md §2).

This is the source of truth for `conversation_mode` ("ai" | "human") —
checked by main.py *before* the LangGraph graph is invoked at all, so a
session mid-handover never gets routed back through the AI agent by
accident. Don't conflate this with AgentState's per-invocation graph
state (see the note in backend/agent/state.py).

Same connection pattern as backend/sandbox/database.py (SQLite,
context-managed) — intentionally consistent, not a second DB technology.
"""

import json
import os
import sqlite3
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from backend.config import SANDBOX_DB_PATH  # noqa: E402

# Reuses the same SQLite file as the sandbox DB — separate tables, one
# less moving part than a second .db file for a hackathon-scoped build.
SESSION_DB_PATH = os.getenv("SESSION_DB_PATH", SANDBOX_DB_PATH)


SCHEMA = """
CREATE TABLE IF NOT EXISTS session_state (
    session_id          TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL,
    conversation_mode   TEXT NOT NULL DEFAULT 'ai' CHECK (conversation_mode IN ('ai', 'human')),
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    message_id   TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL REFERENCES session_state(session_id),
    role         TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'agent', 'system')),
    text         TEXT NOT NULL,
    timestamp    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_queue (
    ticket_id     TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL REFERENCES session_state(session_id),
    trigger_reason TEXT NOT NULL CHECK (
        trigger_reason IN ('fraud_flag', 'low_confidence_repeated', 'explicit_request', 'out_of_scope')
    ),
    summary_json  TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
    created_at    TEXT NOT NULL
);
"""


@contextmanager
def get_connection(db_path: str = SESSION_DB_PATH):
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_schema(db_path: str = SESSION_DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# session_state
# ---------------------------------------------------------------------------
def get_or_create_session(session_id: str, user_id: str) -> Dict[str, Any]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM session_state WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row:
            return dict(row)

        now = _now()
        conn.execute(
            "INSERT INTO session_state (session_id, user_id, conversation_mode, created_at, updated_at) "
            "VALUES (?, ?, 'ai', ?, ?)",
            (session_id, user_id, now, now),
        )
        return {
            "session_id": session_id,
            "user_id": user_id,
            "conversation_mode": "ai",
            "created_at": now,
            "updated_at": now,
        }


def get_conversation_mode(session_id: str) -> str:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT conversation_mode FROM session_state WHERE session_id = ?", (session_id,)
        ).fetchone()
    return row["conversation_mode"] if row else "ai"


def set_conversation_mode(session_id: str, mode: str) -> None:
    if mode not in ("ai", "human"):
        raise ValueError(f"Invalid conversation_mode: {mode!r}")
    with get_connection() as conn:
        conn.execute(
            "UPDATE session_state SET conversation_mode = ?, updated_at = ? WHERE session_id = ?",
            (mode, _now(), session_id),
        )


# ---------------------------------------------------------------------------
# messages
# ---------------------------------------------------------------------------
def append_message(session_id: str, role: str, text: str) -> Dict[str, Any]:
    message = {
        "message_id": f"msg_{uuid.uuid4().hex[:12]}",
        "session_id": session_id,
        "role": role,
        "text": text,
        "timestamp": _now(),
    }
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO messages (message_id, session_id, role, text, timestamp) "
            "VALUES (:message_id, :session_id, :role, :text, :timestamp)",
            message,
        )
    return message


def get_messages_since(session_id: str, since_timestamp: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        if since_timestamp:
            rows = conn.execute(
                "SELECT * FROM messages WHERE session_id = ? AND timestamp > ? ORDER BY timestamp ASC",
                (session_id, since_timestamp),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY timestamp ASC", (session_id,)
            ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# agent_queue
# ---------------------------------------------------------------------------
def create_ticket(session_id: str, trigger_reason: str, summary: Dict[str, Any]) -> Dict[str, Any]:
    ticket_id = f"tkt_{uuid.uuid4().hex[:12]}"
    now = _now()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO agent_queue (ticket_id, session_id, trigger_reason, summary_json, status, created_at) "
            "VALUES (?, ?, ?, ?, 'open', ?)",
            (ticket_id, session_id, trigger_reason, json.dumps(summary), now),
        )
    return {
        "ticket_id": ticket_id,
        "session_id": session_id,
        "trigger_reason": trigger_reason,
        "summary": summary,
        "status": "open",
        "created_at": now,
    }


def list_open_tickets() -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT q.*, s.user_id, u.full_name AS user_name FROM agent_queue q "
            "JOIN session_state s ON s.session_id = q.session_id "
            "LEFT JOIN users u ON u.user_id = s.user_id "
            "WHERE q.status = 'open' ORDER BY q.created_at ASC"
        ).fetchall()

    tickets = []
    for r in rows:
        d = dict(r)
        d["summary"] = json.loads(d.pop("summary_json"))
        tickets.append(d)
    return tickets


def resolve_ticket(ticket_id: str) -> str:
    """Returns the session_id, so callers can also reset LangGraph's
    checkpointed state for that thread (see agent_resolve() in main.py —
    this function alone only clears the SQL-level conversation_mode,
    which isn't the only place handover state lives)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT session_id FROM agent_queue WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"No ticket found with id {ticket_id}")

        conn.execute("UPDATE agent_queue SET status = 'resolved' WHERE ticket_id = ?", (ticket_id,))
        conn.execute(
            "UPDATE session_state SET conversation_mode = 'ai', updated_at = ? WHERE session_id = ?",
            (_now(), row["session_id"]),
        )
        return row["session_id"]


if __name__ == "__main__":
    init_schema()
    print(get_or_create_session("sess_smoketest", "USR-4401"))
    append_message("sess_smoketest", "user", "What's my balance?")
    append_message("sess_smoketest", "assistant", "Your checking balance is $4,250.50.")
    print(get_messages_since("sess_smoketest"))

    ticket = create_ticket(
        "sess_smoketest",
        "fraud_flag",
        {
            "issue": "Customer disputes a $1,800 charge",
            "context": "TXN-1003, flagged 2 hours ago",
            "attempted_resolution": "AI confirmed the flag, offered to lock the card",
            "suggested_next_step": "Verify identity, file fraud claim",
        },
    )
    print(ticket)
    print("open tickets:", list_open_tickets())
    set_conversation_mode("sess_smoketest", "human")
    print("mode after handover:", get_conversation_mode("sess_smoketest"))
    resolve_ticket(ticket["ticket_id"])
    print("mode after resolve:", get_conversation_mode("sess_smoketest"))
    print("open tickets after resolve:", list_open_tickets())