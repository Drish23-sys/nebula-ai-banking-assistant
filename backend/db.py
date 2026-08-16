"""
backend/db.py

Thin connection abstraction so session_store.py can run against either:
  - Turso (libSQL cloud DB) — when TURSO_DATABASE_URL is set, giving
    session/message/ticket/user data that survives Render restarts and
    redeploys (local SQLite alone doesn't — Render wipes local disk on
    every restart of a free-tier service).
  - A local SQLite file — automatic fallback when Turso isn't configured,
    so local dev works without needing a Turso account.

IMPORTANT — not verified against a live Turso instance: this was written
against Turso's documented Python SDK (the `libsql` package, whose API is
designed to closely mirror stdlib sqlite3 — connect/execute/commit/
fetchall), but there was no network path to turso.io available to
actually run it end-to-end while writing this. Run the smoke test at the
bottom of this file against your real TURSO_DATABASE_URL/TURSO_AUTH_TOKEN
before relying on it:

    python backend/db.py

Every function in session_store.py that used to call sqlite3 methods
directly on the connection (`.execute(sql, params).fetchone()`, etc.)
keeps working completely unchanged — this wrapper matches that exact
calling convention regardless of which backend is actually running
underneath, specifically so session_store.py itself needed no rewriting
beyond swapping what get_connection() returns.
"""

import os
from typing import Any, List, Optional

from backend.config import LOCAL_SESSION_DB_PATH, TURSO_AUTH_TOKEN, TURSO_DATABASE_URL

USING_TURSO = bool(TURSO_DATABASE_URL)


class Row(dict):
    """Dict-like row: row["column_name"] works the same as sqlite3.Row,
    regardless of which backend actually produced it."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc


class _CursorResult:
    """Mimics just the sqlite3.Cursor surface this codebase uses —
    .fetchone() / .fetchall() — over a pre-materialized row list, since
    Turso's client may not support lazy cursor iteration identically to
    sqlite3."""

    def __init__(self, rows: List[Row]):
        self._rows = rows

    def fetchone(self) -> Optional[Row]:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> List[Row]:
        return self._rows


def _rows_from_raw_cursor(cursor) -> List[Row]:
    columns = [d[0] for d in cursor.description] if cursor.description else []
    # NOTE: another libsql-vs-sqlite3 behavior gap, found the hard way —
    # for statements with no result set (INSERT/UPDATE/DELETE), libsql's
    # cursor.fetchall() returns None rather than stdlib sqlite3's `[]`.
    # Iterating over that None directly raised "TypeError: 'NoneType'
    # object is not iterable" on every INSERT (signup, append_message,
    # etc.) the first time this ran against a real libsql connection.
    raw_rows = cursor.fetchall() or []
    return [Row(zip(columns, raw_row)) for raw_row in raw_rows]


class Connection:
    """Unified connection wrapper. Supports the exact subset of
    sqlite3.Connection's API session_store.py relies on:
    .execute(sql, params) -> cursor-like result, .commit(), .close(),
    and use as a context manager (commits on clean exit, always closes).
    """

    def __init__(self):
        if USING_TURSO:
            import libsql  # deferred import: only required when actually using Turso

            self._conn = libsql.connect(database=TURSO_DATABASE_URL, auth_token=TURSO_AUTH_TOKEN)
        else:
            import sqlite3

            os.makedirs(os.path.dirname(LOCAL_SESSION_DB_PATH) or ".", exist_ok=True)
            self._conn = sqlite3.connect(LOCAL_SESSION_DB_PATH)
            self._conn.execute("PRAGMA foreign_keys = ON;")

    def execute(self, sql: str, params: Any = ()) -> _CursorResult:
        # NOTE: unlike stdlib sqlite3, libsql's execute() does NOT accept
        # a dict for ":name"-style placeholders — it raises "Expected a
        # list or tuple for parameters". Confirmed the hard way: three
        # call sites in session_store.py (append_message, create_user,
        # create_auth_token) originally used dict params against named
        # placeholders and failed with exactly that error the first time
        # signup was actually exercised against a real libsql connection.
        # All query call sites in this codebase now use positional "?"
        # placeholders with tuple params only — keep it that way.
        cursor = self._conn.execute(sql, params)
        return _CursorResult(_rows_from_raw_cursor(cursor))

    def execute_script(self, statements: List[str]) -> None:
        """Runs multiple CREATE TABLE-style statements. Deliberately not
        using sqlite3's .executescript() convenience method — it's not
        guaranteed present on Turso's client, so schema init is split
        into individually-executed statements for portability."""
        for stmt in statements:
            stmt = stmt.strip()
            if stmt:
                self._conn.execute(stmt)

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Connection":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.commit()
        self.close()


def get_db_connection() -> Connection:
    return Connection()


if __name__ == "__main__":
    # Smoke test — run this directly (`python backend/db.py`) after setting
    # TURSO_DATABASE_URL / TURSO_AUTH_TOKEN to confirm the connection
    # actually works against your real Turso database before trusting it
    # for a demo. Prints which backend it's using either way.
    print(f"Backend: {'Turso (cloud)' if USING_TURSO else f'local SQLite ({LOCAL_SESSION_DB_PATH})'}")
    with get_db_connection() as conn:
        conn.execute_script([
            "CREATE TABLE IF NOT EXISTS _smoke_test (id INTEGER PRIMARY KEY, note TEXT)"
        ])
        conn.execute("INSERT INTO _smoke_test (note) VALUES (?)", ("it works",))
        result = conn.execute("SELECT * FROM _smoke_test").fetchall()
        print(f"Rows: {[dict(r) for r in result]}")
        conn.execute("DROP TABLE _smoke_test")
    print("Smoke test passed.")