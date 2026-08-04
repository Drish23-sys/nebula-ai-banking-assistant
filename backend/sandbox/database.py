"""
backend/sandbox/database.py

Dummy Banking Execution Sandbox (PRD §4.4).
Sets up an in-file SQLite database with mock tables: users, accounts,
transactions, cards. This is the backing store for the 5 sandbox tools
in backend/sandbox/tools.py (Day 2).

Run directly to (re)create and seed the sandbox DB:
    python backend/sandbox/database.py
"""

import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from backend.config import SANDBOX_DB_PATH  # noqa: E402


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id      TEXT PRIMARY KEY,
    full_name    TEXT NOT NULL,
    email        TEXT NOT NULL,
    verified     INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
    account_id      TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES users(user_id),
    account_type    TEXT NOT NULL CHECK (account_type IN ('checking', 'savings')),
    balance         REAL NOT NULL DEFAULT 0.0,
    currency        TEXT NOT NULL DEFAULT 'USD',
    daily_transfer_limit REAL NOT NULL DEFAULT 5000.0
);

CREATE TABLE IF NOT EXISTS cards (
    card_id      TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(user_id),
    account_id   TEXT NOT NULL REFERENCES accounts(account_id),
    card_type    TEXT NOT NULL CHECK (card_type IN ('debit', 'credit')),
    last4        TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'locked')),
    lock_reference TEXT
);

CREATE TABLE IF NOT EXISTS transactions (
    transaction_id  TEXT PRIMARY KEY,
    account_id      TEXT NOT NULL REFERENCES accounts(account_id),
    description     TEXT NOT NULL,
    amount          REAL NOT NULL,
    direction       TEXT NOT NULL CHECK (direction IN ('debit', 'credit')),
    occurred_at     TEXT NOT NULL,
    flagged         INTEGER NOT NULL DEFAULT 0
);
"""


@contextmanager
def get_connection(db_path: str = SANDBOX_DB_PATH):
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_schema(db_path: str = SANDBOX_DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA)


def _now(offset_hours: float = 0.0) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=offset_hours)).isoformat()


def seed_demo_data(db_path: str = SANDBOX_DB_PATH, reset: bool = True) -> None:
    """
    Seeds data matching the PRD §6 sample transcripts:
    - John Doe (#USR-4401), Checking Account ending 4821, balance $4,250.50
    - Visa Credit Card ending 9921 (active, will be locked in Scenario 1)
    - A flagged $1,800 transaction 2 hours ago (Scenario 3 fraud trigger)
    """
    with get_connection(db_path) as conn:
        if reset:
            conn.executescript(
                "DELETE FROM transactions; DELETE FROM cards; "
                "DELETE FROM accounts; DELETE FROM users;"
            )

        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, full_name, email, verified, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("USR-4401", "John Doe", "john.doe@example.com", 1, _now(offset_hours=24 * 400)),
        )

        conn.execute(
            "INSERT OR IGNORE INTO accounts "
            "(account_id, user_id, account_type, balance, currency, daily_transfer_limit) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("CHK-4821", "USR-4401", "checking", 4250.50, "USD", 5000.0),
        )
        conn.execute(
            "INSERT OR IGNORE INTO accounts "
            "(account_id, user_id, account_type, balance, currency, daily_transfer_limit) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("SAV-4822", "USR-4401", "savings", 12800.00, "USD", 2000.0),
        )

        conn.execute(
            "INSERT OR IGNORE INTO cards "
            "(card_id, user_id, account_id, card_type, last4, status, lock_reference) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("CARD-9921", "USR-4401", "CHK-4821", "credit", "9921", "active", None),
        )
        conn.execute(
            "INSERT OR IGNORE INTO cards "
            "(card_id, user_id, account_id, card_type, last4, status, lock_reference) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("CARD-5510", "USR-4401", "CHK-4821", "debit", "5510", "active", None),
        )

        conn.execute(
            "INSERT OR IGNORE INTO transactions "
            "(transaction_id, account_id, description, amount, direction, occurred_at, flagged) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("TXN-1001", "CHK-4821", "Grocery Store Purchase", 84.32, "debit", _now(offset_hours=30), 0),
        )
        conn.execute(
            "INSERT OR IGNORE INTO transactions "
            "(transaction_id, account_id, description, amount, direction, occurred_at, flagged) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("TXN-1002", "CHK-4821", "Payroll Deposit", 2500.00, "credit", _now(offset_hours=48), 0),
        )
        conn.execute(
            "INSERT OR IGNORE INTO transactions "
            "(transaction_id, account_id, description, amount, direction, occurred_at, flagged) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("TXN-1003", "CHK-4821", "Unknown Merchant Charge", 1800.00, "debit", _now(offset_hours=2), 1),
        )


def print_summary(db_path: str = SANDBOX_DB_PATH) -> None:
    with get_connection(db_path) as conn:
        for table in ("users", "accounts", "cards", "transactions"):
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            print(f"\n[{table}] ({len(rows)} rows)")
            for row in rows:
                print("  ", dict(row))


if __name__ == "__main__":
    init_schema()
    seed_demo_data()
    print(f"Sandbox DB initialized and seeded at: {SANDBOX_DB_PATH}")
    print_summary()
