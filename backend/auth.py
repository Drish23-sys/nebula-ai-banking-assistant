"""
backend/auth.py

Email+password accounts for customers, so a returning customer's chat
history and bank details stay tied to them across visits/devices rather
than resetting to the shared demo user every time. Opaque bearer tokens
(not JWT) — simpler for this scope: no signing-key management, and
revocation is just deleting a row rather than needing a blocklist.
"""

import secrets
from typing import Any, Dict, Optional

import bcrypt

from backend import session_store
from backend.sandbox.database import provision_demo_account


class AuthError(Exception):
    """Raised for expected auth failures (bad credentials, email taken)
    — main.py catches this and returns a clean 4xx, distinct from a
    genuine 500 for anything unexpected."""


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


def signup(email: str, password: str) -> Dict[str, Any]:
    if not email or "@" not in email:
        raise AuthError("Enter a valid email address.")
    if not password or len(password) < 8:
        raise AuthError("Password must be at least 8 characters.")

    # Checked proactively rather than relying on catching a DB-level
    # UNIQUE-constraint exception — sqlite3 and Turso's libsql client
    # aren't guaranteed to raise the same exception type for that, so
    # this is the more portable check.
    if session_store.get_user_by_email(email):
        raise AuthError("An account with that email already exists.")

    user = session_store.create_user(email=email, password_hash=hash_password(password))

    # New signups have no seeded bank data by default (the mock sandbox
    # DB only ever had the one hardcoded demo user) — without this,
    # every tool call (balance, cards, etc.) would fail with "no account
    # found" for anyone who actually signs up. Gives every new customer
    # the same demo-quality starting data as the original hardcoded user.
    provision_demo_account(user["user_id"])

    token = session_store.create_auth_token(user["user_id"], _generate_token())
    return {"user_id": user["user_id"], "email": user["email"], "token": token["token"]}


def login(email: str, password: str) -> Dict[str, Any]:
    user = session_store.get_user_by_email(email)
    if not user or not verify_password(password, user["password_hash"]):
        # Deliberately the same message for "no such email" and "wrong
        # password" — distinguishing them lets an attacker enumerate
        # which emails have accounts.
        raise AuthError("Invalid email or password.")

    token = session_store.create_auth_token(user["user_id"], _generate_token())
    return {"user_id": user["user_id"], "email": user["email"], "token": token["token"]}


def get_current_user(token: Optional[str]) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    return session_store.get_user_by_token(token)