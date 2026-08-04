"""
backend/sandbox/tools.py

The 5 sandbox tool functions referenced by backend/agent/nodes.py's
tool_node. Each function is a thin, Pydantic-validated wrapper around
backend/sandbox/database.py's SQLite tables — deterministic execution,
no LLM involved, matching PRD §4.4's "Dummy Banking Execution Sandbox."

Dispatch table keys match backend/agent/nodes.py's TOOL_INTENT_KEYWORDS
exactly (CHECK_BALANCE, LOCK_CARD, TRANSACTION_HISTORY,
CHECK_TRANSFER_LIMIT, CALCULATE_LOAN_EMI) — tool_node calls
execute_tool(intent, ...) and doesn't need to know the individual
function names.

Every tool returns the same envelope shape so tool_node's downstream
handling (and eventually the API response) doesn't need per-tool
branching:
    {"status": "success" | "error", "data": {...}, "message": str}
"""

import os
import sys
import uuid
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, field_validator

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from backend.sandbox.database import get_connection  # noqa: E402


def _envelope(status: str, data: Optional[Dict[str, Any]] = None, message: str = "") -> Dict[str, Any]:
    return {"status": status, "data": data or {}, "message": message}


# ---------------------------------------------------------------------------
# 1. CHECK_BALANCE
# ---------------------------------------------------------------------------
def check_balance(user_id: str) -> Dict[str, Any]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT account_id, account_type, balance, currency FROM accounts WHERE user_id = ?",
            (user_id,),
        ).fetchall()

    if not rows:
        return _envelope("error", message=f"No accounts found for user {user_id}.")

    accounts = [dict(r) for r in rows]
    return _envelope(
        "success",
        data={"accounts": accounts},
        message=f"Found {len(accounts)} account(s) for {user_id}.",
    )


# ---------------------------------------------------------------------------
# 2. LOCK_CARD
# ---------------------------------------------------------------------------
class LockCardRequest(BaseModel):
    """Pydantic-validated request shape (PRD §4.2: strict JSON schema
    contracts for every tool call)."""
    user_id: str
    last4: Optional[str] = Field(
        default=None, description="Last 4 digits of the card to lock. If omitted, locks the user's first active card."
    )

    @field_validator("last4")
    @classmethod
    def validate_last4(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and (not v.isdigit() or len(v) != 4):
            raise ValueError("last4 must be exactly 4 digits")
        return v


def lock_card(user_id: str, last4: Optional[str] = None) -> Dict[str, Any]:
    req = LockCardRequest(user_id=user_id, last4=last4)  # raises on bad input

    with get_connection() as conn:
        if req.last4:
            row = conn.execute(
                "SELECT card_id, status FROM cards WHERE user_id = ? AND last4 = ?",
                (req.user_id, req.last4),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT card_id, status FROM cards WHERE user_id = ? AND status = 'active' LIMIT 1",
                (req.user_id,),
            ).fetchone()

        if not row:
            return _envelope("error", message="No matching active card found to lock.")

        if row["status"] == "locked":
            return _envelope(
                "success",
                data={"card_id": row["card_id"], "status": "locked", "already_locked": True},
                message="Card was already locked.",
            )

        lock_reference = f"LOCK-{uuid.uuid4().hex[:8].upper()}"
        conn.execute(
            "UPDATE cards SET status = 'locked', lock_reference = ? WHERE card_id = ?",
            (lock_reference, row["card_id"]),
        )

    return _envelope(
        "success",
        data={"card_id": row["card_id"], "status": "locked", "lock_reference": lock_reference},
        message=f"Card locked successfully. Reference: {lock_reference}.",
    )


# ---------------------------------------------------------------------------
# 3. TRANSACTION_HISTORY
# ---------------------------------------------------------------------------
def get_transaction_history(user_id: str, limit: int = 5) -> Dict[str, Any]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT t.transaction_id, t.account_id, t.description, t.amount,
                   t.direction, t.occurred_at, t.flagged
            FROM transactions t
            JOIN accounts a ON a.account_id = t.account_id
            WHERE a.user_id = ?
            ORDER BY t.occurred_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()

    transactions = [dict(r) for r in rows]
    return _envelope(
        "success",
        data={"transactions": transactions},
        message=f"Returned {len(transactions)} most recent transaction(s).",
    )


# ---------------------------------------------------------------------------
# 4. CHECK_TRANSFER_LIMIT
# ---------------------------------------------------------------------------
def check_transfer_limit(user_id: str, account_type: Optional[str] = None) -> Dict[str, Any]:
    with get_connection() as conn:
        if account_type:
            rows = conn.execute(
                "SELECT account_id, account_type, daily_transfer_limit FROM accounts "
                "WHERE user_id = ? AND account_type = ?",
                (user_id, account_type),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT account_id, account_type, daily_transfer_limit FROM accounts WHERE user_id = ?",
                (user_id,),
            ).fetchall()

    if not rows:
        return _envelope("error", message="No matching account found.")

    limits = [dict(r) for r in rows]
    return _envelope("success", data={"limits": limits}, message=f"Found limits for {len(limits)} account(s).")


# ---------------------------------------------------------------------------
# 5. CALCULATE_LOAN_EMI
# ---------------------------------------------------------------------------
class LoanEmiRequest(BaseModel):
    """Standard reducing-balance EMI formula. No DB access — pure
    calculation, useful for e.g. loan pre-qualification chat flows."""
    principal: float = Field(gt=0)
    annual_rate_percent: float = Field(ge=0, le=100)
    tenure_months: int = Field(gt=0, le=480)


def calculate_loan_emi(principal: float, annual_rate_percent: float, tenure_months: int) -> Dict[str, Any]:
    req = LoanEmiRequest(
        principal=principal, annual_rate_percent=annual_rate_percent, tenure_months=tenure_months
    )

    monthly_rate = req.annual_rate_percent / 12 / 100
    if monthly_rate == 0:
        emi = req.principal / req.tenure_months
    else:
        emi = (
            req.principal
            * monthly_rate
            * (1 + monthly_rate) ** req.tenure_months
            / ((1 + monthly_rate) ** req.tenure_months - 1)
        )

    total_payment = emi * req.tenure_months
    total_interest = total_payment - req.principal

    return _envelope(
        "success",
        data={
            "emi": round(emi, 2),
            "total_payment": round(total_payment, 2),
            "total_interest": round(total_interest, 2),
        },
        message=f"Estimated monthly EMI: {round(emi, 2)}.",
    )


# ---------------------------------------------------------------------------
# Dispatch table — tool_node's single entry point
# ---------------------------------------------------------------------------
def execute_tool(intent: str, user_id: str, **kwargs) -> Dict[str, Any]:
    """
    Routes a classified intent to its tool function. kwargs are whatever
    slots the (future) LLM-based intent/slot-filling step extracts —
    today's rule-based intent_node doesn't extract slots yet, so calls
    here mostly use defaults (e.g. lock_card's `last4=None` locks the
    user's first active card, calculate_loan_emi needs slots that don't
    exist yet and will error informatively until slot-filling lands).
    """
    dispatch = {
        "CHECK_BALANCE": lambda: check_balance(user_id),
        "LOCK_CARD": lambda: lock_card(user_id, last4=kwargs.get("last4")),
        "TRANSACTION_HISTORY": lambda: get_transaction_history(user_id, limit=kwargs.get("limit", 5)),
        "CHECK_TRANSFER_LIMIT": lambda: check_transfer_limit(user_id, account_type=kwargs.get("account_type")),
        "CALCULATE_LOAN_EMI": lambda: calculate_loan_emi(
            principal=kwargs["principal"],
            annual_rate_percent=kwargs["annual_rate_percent"],
            tenure_months=kwargs["tenure_months"],
        )
        if all(k in kwargs for k in ("principal", "annual_rate_percent", "tenure_months"))
        else _envelope("error", message="Missing loan details — need principal, annual_rate_percent, tenure_months."),
    }

    handler = dispatch.get(intent)
    if handler is None:
        return _envelope("error", message=f"Unknown tool intent: {intent}")

    try:
        return handler()
    except Exception as exc:  # Pydantic ValidationError, sqlite errors, etc.
        return _envelope("error", message=f"Tool execution failed: {exc}")


if __name__ == "__main__":
    # Smoke test against the seeded demo data (John Doe / USR-4401).
    from backend.sandbox.database import init_schema, seed_demo_data

    init_schema()
    seed_demo_data()

    print("check_balance:", execute_tool("CHECK_BALANCE", "USR-4401"))
    print("lock_card:", execute_tool("LOCK_CARD", "USR-4401", last4="9921"))
    print("transaction_history:", execute_tool("TRANSACTION_HISTORY", "USR-4401", limit=3))
    print("transfer_limit:", execute_tool("CHECK_TRANSFER_LIMIT", "USR-4401"))
    print(
        "loan_emi:",
        execute_tool(
            "CALCULATE_LOAN_EMI", "USR-4401", principal=500000, annual_rate_percent=9.5, tenure_months=60
        ),
    )
    print("unknown_intent:", execute_tool("NOT_A_REAL_TOOL", "USR-4401"))
