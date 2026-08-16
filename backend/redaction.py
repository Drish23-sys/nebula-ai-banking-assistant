"""
backend/redaction.py

Masks sensitive numbers a customer types into chat (card numbers, SSNs,
bank account numbers) before they ever reach storage, the LLM, or an
agent's screen. Applied once, at the very top of /chat — everything
downstream (session_store, the AI graph, the agent dashboard) only ever
sees the redacted version.

Deliberately NOT needed for the AI's own replies: check_balance/lock_card/
etc. (backend/sandbox/tools.py) never expose a full card number in the
first place — only last4 is stored anywhere in the schema. This module
only has to worry about what a *customer* might paste in.
"""

import re

# Card numbers: 13-19 digits, optionally grouped with spaces or dashes
# (covers "4111111111111111", "4111 1111 1111 1111", "4111-1111-1111-1111").
_CARD_NUMBER_RE = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")

# US SSN pattern: XXX-XX-XXXX, or 9 bare digits in that shape.
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

# Catch-all: any other long bare digit run (9+ digits) not already caught
# above — covers bank account numbers, which don't have a fixed format.
_LONG_DIGIT_RUN_RE = re.compile(r"\b\d{9,}\b")


def _mask_keep_last4(match: re.Match) -> str:
    digits_only = re.sub(r"[ -]", "", match.group())
    if len(digits_only) <= 4:
        return match.group()  # too short to be what we're worried about; leave it
    return "•" * (len(digits_only) - 4) + digits_only[-4:]


def redact_sensitive(text: str) -> str:
    """
    Returns `text` with card numbers, SSNs, and long bank-account-style
    digit runs masked to only their last 4 digits — enough for the
    customer/agent to recognize which number was meant, without the full
    value ever being stored, logged, or sent to an LLM.
    """
    text = _CARD_NUMBER_RE.sub(_mask_keep_last4, text)
    text = _SSN_RE.sub(_mask_keep_last4, text)
    text = _LONG_DIGIT_RUN_RE.sub(_mask_keep_last4, text)
    return text


if __name__ == "__main__":
    tests = [
        "my card number is 4111111111111111 please lock it",
        "here it is: 4111 1111 1111 1111",
        "ssn is 123-45-6789",
        "my account number 88293471056 has an issue",
        "what is my balance",  # should pass through untouched
        "i have 2 cards",  # short number, should pass through untouched
    ]
    for t in tests:
        print(f"{t!r}\n  -> {redact_sensitive(t)!r}\n")