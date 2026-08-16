"""
backend/sandbox/database.py

Dummy Banking Execution Sandbox (PRD §4.4).
Backing store for the 5 sandbox tools in backend/sandbox/tools.py.

Run directly to (re)create and seed the sandbox DB:
    python backend/sandbox/database.py

Uses the same Turso-backed connection layer as session_store.py
(backend/db.py) rather than a plain local sqlite3 file. This used to be
local-only, on the reasoning that it's just static seed data rebuilt at
every deploy anyway — that stopped being true once provision_demo_account()
started writing NEW per-customer bank profiles into it at signup time
(a runtime write, not build-time seeding). On Render's free tier, a local
file doesn't survive a restart/redeploy, so every signed-up customer's
bank profile was silently vanishing the next time the service restarted,
even though their login (on Turso) survived fine — they could log back
in, but "No accounts found" on every tool call. Moving this onto the
same persistent connection as everything else fixes that.
"""

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from backend import db  # noqa: E402
from backend.config import SANDBOX_DB_PATH  # noqa: E402  (kept for the __main__ print message only)


SCHEMA_STATEMENTS = [
    # Named `bank_customers`, not `users` — session_store.py's SCHEMA_STATEMENTS
    # already defines a `users` table (login credentials: email,
    # password_hash) in the SAME Turso database now that both files share
    # backend/db.py's connection layer. `CREATE TABLE IF NOT EXISTS users`
    # here would have been a silent no-op against the wrong schema
    # (whichever file's init_schema() happened to run first would "win"),
    # breaking either login or bank-profile provisioning depending on
    # startup order. Distinct table names sidestep the collision entirely.
    """
    CREATE TABLE IF NOT EXISTS bank_customers (
        user_id      TEXT PRIMARY KEY,
        full_name    TEXT NOT NULL,
        email        TEXT NOT NULL,
        verified     INTEGER NOT NULL DEFAULT 0,
        created_at   TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS accounts (
        account_id      TEXT PRIMARY KEY,
        user_id         TEXT NOT NULL REFERENCES bank_customers(user_id),
        account_type    TEXT NOT NULL CHECK (account_type IN ('checking', 'savings')),
        balance         REAL NOT NULL DEFAULT 0.0,
        currency        TEXT NOT NULL DEFAULT 'USD',
        daily_transfer_limit REAL NOT NULL DEFAULT 5000.0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cards (
        card_id      TEXT PRIMARY KEY,
        user_id      TEXT NOT NULL REFERENCES bank_customers(user_id),
        account_id   TEXT NOT NULL REFERENCES accounts(account_id),
        card_type    TEXT NOT NULL CHECK (card_type IN ('debit', 'credit')),
        last4        TEXT NOT NULL,
        status       TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'locked')),
        lock_reference TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS transactions (
        transaction_id  TEXT PRIMARY KEY,
        account_id      TEXT NOT NULL REFERENCES accounts(account_id),
        description     TEXT NOT NULL,
        amount          REAL NOT NULL,
        direction       TEXT NOT NULL CHECK (direction IN ('debit', 'credit')),
        occurred_at     TEXT NOT NULL,
        flagged         INTEGER NOT NULL DEFAULT 0
    )
    """,
]


def get_connection() -> db.Connection:
    return db.get_db_connection()


def init_schema() -> None:
    with get_connection() as conn:
        conn.execute_script(SCHEMA_STATEMENTS)


def _now(offset_hours: float = 0.0) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=offset_hours)).isoformat()


def seed_demo_data(reset: bool = False) -> None:
    """
    Seeds data matching the PRD §6 sample transcripts:
    - John Doe (#USR-4401), Checking Account ending 4821, balance $4,250.50
    - Visa Credit Card ending 9921 (active, will be locked in Scenario 1)
    - A flagged $1,800 transaction 2 hours ago (Scenario 3 fraud trigger)

    reset defaults to False now — this used to default to True back when
    the sandbox DB was a local file rebuilt fresh on every deploy anyway
    (nothing real to lose). Now that it's on the same persistent Turso
    database as everything else, a reset wipes every real signed-up
    customer's bank profile too, not just the demo user. The Render build
    command runs this on every deploy (`python backend/sandbox/database.py`)
    — with the old default, that would've deleted every customer account
    on every redeploy. INSERT OR IGNORE already makes the demo-user seed
    idempotent without needing a destructive reset first; pass
    reset=True explicitly only for a deliberate full manual wipe.
    """
    with get_connection() as conn:
        if reset:
            conn.execute_script(
                [
                    "DELETE FROM transactions",
                    "DELETE FROM cards",
                    "DELETE FROM accounts",
                    "DELETE FROM bank_customers",
                ]
            )

        conn.execute(
            "INSERT OR IGNORE INTO bank_customers (user_id, full_name, email, verified, created_at) "
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


def provision_demo_account(user_id: str, full_name: str = "New Customer", email: str = "") -> None:
    """
    Called from backend/auth.py right after a new signup, since the
    sandbox DB only ever had the one hardcoded demo user (USR-4401) —
    without this, every tool call (balance, cards, transfers) would
    fail with "no account found" for anyone who actually creates a real
    account. Gives every new signup the same demo-quality starting data
    (one checking + one savings account, one debit card) as the
    original hardcoded demo user, just with fresh IDs and zero
    transaction history.
    """
    short = uuid.uuid4().hex[:6].upper()
    checking_id = f"CHK-{short}"
    savings_id = f"SAV-{short}"
    card_id = f"CARD-{short}"

    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO bank_customers (user_id, full_name, email, verified, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, full_name, email, 1, _now()),
        )
        conn.execute(
            "INSERT OR IGNORE INTO accounts "
            "(account_id, user_id, account_type, balance, currency, daily_transfer_limit) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (checking_id, user_id, "checking", 1000.00, "USD", 5000.0),
        )
        conn.execute(
            "INSERT OR IGNORE INTO accounts "
            "(account_id, user_id, account_type, balance, currency, daily_transfer_limit) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (savings_id, user_id, "savings", 500.00, "USD", 2000.0),
        )
        conn.execute(
            "INSERT OR IGNORE INTO cards "
            "(card_id, user_id, account_id, card_type, last4, status, lock_reference) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (card_id, user_id, checking_id, "debit", short[-4:], "active", None),
        )


def print_summary() -> None:
    with get_connection() as conn:
        for table in ("bank_customers", "accounts", "cards", "transactions"):
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            print(f"\n[{table}] ({len(rows)} rows)")
            for row in rows:
                print("  ", dict(row))


if __name__ == "__main__":
    init_schema()
    seed_demo_data()
    backend_label = "Turso (cloud)" if db.USING_TURSO else f"local file ({SANDBOX_DB_PATH})"
    print(f"Sandbox DB initialized and seeded — backend: {backend_label}")
    print_summary()