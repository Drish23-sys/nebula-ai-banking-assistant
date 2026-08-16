"""
backend/session_store.py

Durable session + handover-queue + user-account persistence (PRD §4.5,
and the exact schema promised to the frontend in docs/FRONTEND_HANDOFF.md
§2).

This is the source of truth for `conversation_mode` ("ai" | "human") —
checked by main.py *before* the LangGraph graph is invoked at all, so a
session mid-handover never gets routed back through the AI agent by
accident. Don't conflate this with AgentState's per-invocation graph
state (see the note in backend/agent/state.py).

Storage backend: Turso (cloud) when configured, local SQLite file
otherwise — see backend/db.py for that switch. This module's own code
doesn't care which one is actually running underneath; get_connection()
here just delegates to db.py.
"""

import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from backend import db  # noqa: E402
from backend.config import MESSAGE_RETENTION_HOURS  # noqa: E402

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS session_state (
        session_id              TEXT PRIMARY KEY,
        user_id                 TEXT NOT NULL,
        conversation_mode       TEXT NOT NULL DEFAULT 'ai' CHECK (conversation_mode IN ('ai', 'human')),
        conversation_started_at TEXT NOT NULL,
        created_at              TEXT NOT NULL,
        updated_at              TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS messages (
        message_id   TEXT PRIMARY KEY,
        session_id   TEXT NOT NULL REFERENCES session_state(session_id),
        role         TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'agent', 'system')),
        text         TEXT NOT NULL,
        timestamp    TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_queue (
        ticket_id     TEXT PRIMARY KEY,
        session_id    TEXT NOT NULL REFERENCES session_state(session_id),
        trigger_reason TEXT NOT NULL CHECK (
            trigger_reason IN ('fraud_flag', 'low_confidence_repeated', 'explicit_request', 'out_of_scope')
        ),
        summary_json  TEXT NOT NULL,
        status        TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
        created_at    TEXT NOT NULL
    )
    """,
    # --- Auth: email+password accounts, so a returning customer's chat
    # history and bank details stay tied to them across visits, rather
    # than resetting to the shared demo user on every new browser tab. ---
    """
    CREATE TABLE IF NOT EXISTS users (
        user_id        TEXT PRIMARY KEY,
        email          TEXT NOT NULL UNIQUE,
        password_hash  TEXT NOT NULL,
        created_at     TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS auth_tokens (
        token       TEXT PRIMARY KEY,
        user_id     TEXT NOT NULL REFERENCES users(user_id),
        created_at  TEXT NOT NULL,
        expires_at  TEXT NOT NULL
    )
    """,
]


def get_connection() -> db.Connection:
    return db.get_db_connection()


def init_schema() -> None:
    with get_connection() as conn:
        conn.execute_script(SCHEMA_STATEMENTS)


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
            "INSERT INTO session_state (session_id, user_id, conversation_mode, conversation_started_at, created_at, updated_at) "
            "VALUES (?, ?, 'ai', ?, ?, ?)",
            (session_id, user_id, now, now, now),
        )
        return {
            "session_id": session_id,
            "user_id": user_id,
            "conversation_mode": "ai",
            "conversation_started_at": now,
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


def reset_conversation(session_id: str) -> None:
    """
    The real "start new conversation" — bumps conversation_started_at so
    get_messages_since() stops surfacing anything before this point (old
    messages stay in storage, still subject to normal 72h retention —
    this isn't a delete, just a "stop showing this by default" boundary),
    and forces conversation_mode back to 'ai' in case the customer resets
    while still waiting on a human agent.

    Does NOT touch LangGraph's own checkpointed state (topic_stack,
    is_handover_active, etc.) — that's a separate concern, reset by the
    caller in main.py via compiled_graph.update_state(), the same way
    agent_resolve() already does.
    """
    now = _now()
    with get_connection() as conn:
        conn.execute(
            "UPDATE session_state SET conversation_mode = 'ai', conversation_started_at = ?, updated_at = ? "
            "WHERE session_id = ?",
            (now, now, session_id),
        )
        # If the customer resets while an agent still has an open ticket
        # for them, don't leave it stranded in the queue forever — the
        # customer walked away from that issue, so close it out rather
        # than making an agent discover a conversation that's moved on.
        conn.execute(
            "UPDATE agent_queue SET status = 'resolved' WHERE session_id = ? AND status = 'open'",
            (session_id,),
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
            "VALUES (?, ?, ?, ?, ?)",
            (message["message_id"], message["session_id"], message["role"], message["text"], message["timestamp"]),
        )
    return message


def get_messages_since(session_id: str, since_timestamp: Optional[str] = None) -> List[Dict[str, Any]]:
    # Three possible floors on what counts as "visible history" — the
    # latest (most restrictive) one wins:
    #   1. since_timestamp    — incremental polling cursor
    #   2. retention_cutoff   — 72h auto-expiry
    #   3. conversation_started_at — customer explicitly reset (see
    #      reset_conversation()); old messages still exist in storage
    #      (still subject to normal 72h retention) but stop being shown
    #      by default once a fresh start has been requested.
    retention_cutoff = (datetime.now(timezone.utc) - timedelta(hours=MESSAGE_RETENTION_HOURS)).isoformat()

    with get_connection() as conn:
        session_row = conn.execute(
            "SELECT conversation_started_at FROM session_state WHERE session_id = ?", (session_id,)
        ).fetchone()
        floors = [retention_cutoff]
        if since_timestamp:
            floors.append(since_timestamp)
        if session_row and session_row["conversation_started_at"]:
            floors.append(session_row["conversation_started_at"])
        effective_since = max(floors)

        rows = conn.execute(
            "SELECT * FROM messages WHERE session_id = ? AND timestamp > ? ORDER BY timestamp ASC",
            (session_id, effective_since),
        ).fetchall()
    return [dict(r) for r in rows]


def purge_expired_messages() -> int:
    """Deletes messages older than MESSAGE_RETENTION_HOURS across all
    sessions. get_messages_since() already filters these out at read
    time regardless, so this isn't required for correctness — it's
    housekeeping, to stop old rows accumulating forever. Called
    opportunistically (see main.py's /chat route) rather than on a
    schedule, since Render's free tier has no built-in cron and this
    keeps the implementation simple. Returns the number of rows deleted.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=MESSAGE_RETENTION_HOURS)).isoformat()
    with get_connection() as conn:
        deleted = conn.execute("SELECT message_id FROM messages WHERE timestamp <= ?", (cutoff,)).fetchall()
        conn.execute("DELETE FROM messages WHERE timestamp <= ?", (cutoff,))
    return len(deleted)


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
            "SELECT q.*, s.user_id, u.email AS user_name FROM agent_queue q "
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


# ---------------------------------------------------------------------------
# users / auth_tokens
# ---------------------------------------------------------------------------
def create_user(email: str, password_hash: str) -> Dict[str, Any]:
    """Raises sqlite3/libsql's integrity error if the email is already
    taken — caller (backend/auth.py) is expected to catch and translate
    that into a clean 409 response rather than a raw 500."""
    user = {
        "user_id": f"USR-{uuid.uuid4().hex[:8].upper()}",
        "email": email.lower().strip(),
        "password_hash": password_hash,
        "created_at": _now(),
    }
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO users (user_id, email, password_hash, created_at) "
            "VALUES (?, ?, ?, ?)",
            (user["user_id"], user["email"], user["password_hash"], user["created_at"]),
        )
    return user


def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email.lower().strip(),)
        ).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def create_auth_token(user_id: str, token: str, ttl_days: int = 30) -> Dict[str, Any]:
    record = {
        "token": token,
        "user_id": user_id,
        "created_at": _now(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=ttl_days)).isoformat(),
    }
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO auth_tokens (token, user_id, created_at, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (record["token"], record["user_id"], record["created_at"], record["expires_at"]),
        )
    return record


def get_user_by_token(token: str) -> Optional[Dict[str, Any]]:
    """Returns the user dict if the token exists and hasn't expired,
    else None — main.py's auth dependency treats None as 401."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT u.* FROM auth_tokens t JOIN users u ON u.user_id = t.user_id "
            "WHERE t.token = ? AND t.expires_at > ?",
            (token, _now()),
        ).fetchone()
    return dict(row) if row else None


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