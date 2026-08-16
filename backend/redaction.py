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

# CVV/PIN/password have no fixed shape to pattern-match on their own (a
# bare "1234" could be an amount, a date, anything) — so these are only
# masked when they follow one of these trigger phrases, e.g. "cvv is
# 123", "my pin: 4821", "password is hunter2". Keeps the whole *value*
# hidden, not just a last-4 tail like the number patterns above, since
# there's no legitimate reason to ever show part of a CVV/PIN/password
# back — unlike a card number, a partial CVV isn't something anyone
# needs to visually confirm.
_KEYWORD_SECRET_RE = re.compile(
    r"\b(cvv|cvc|pin|password|passcode)\b\s*(?:is|:|=)?\s*(\S+)",
    re.IGNORECASE,
)


def _mask_keep_last4(match: re.Match) -> str:
    digits_only = re.sub(r"[ -]", "", match.group())
    if len(digits_only) <= 4:
        return match.group()  # too short to be what we're worried about; leave it
    return "•" * (len(digits_only) - 4) + digits_only[-4:]


def redact_sensitive(text: str) -> str:
    """
    Returns `text` with card numbers, SSNs, long bank-account-style digit
    runs, and keyword-flagged secrets (CVV/PIN/password) masked — enough
    for the customer/agent to recognize which value was meant (for the
    number patterns; keyword secrets are hidden in full), without the
    real value ever being stored, logged, or sent to an LLM.
    """
    text = _CARD_NUMBER_RE.sub(_mask_keep_last4, text)
    text = _SSN_RE.sub(_mask_keep_last4, text)
    text = _LONG_DIGIT_RUN_RE.sub(_mask_keep_last4, text)
    text = _KEYWORD_SECRET_RE.sub(lambda m: f"{m.group(1)} [hidden]", text)
    return text


if __name__ == "__main__":
    tests = [
        "my card number is 4111111111111111 please lock it",
        "here it is: 4111 1111 1111 1111",
        "ssn is 123-45-6789",
        "my account number 88293471056 has an issue",
        "what is my balance",  # should pass through untouched
        "i have 2 cards",  # short number, should pass through untouched
        "my cvv is 123",
        "pin: 4821",
        "password is hunter2",
        "my CVC=482",
    ]
    for t in tests:
        print(f"{t!r}\n  -> {redact_sensitive(t)!r}\n")