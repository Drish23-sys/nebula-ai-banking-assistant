"""
backend/agent/guardrails.py

Responsible-AI boundary checks that run BEFORE intent classification
(PRD's Responsible AI layer). Two concerns, kept separate on purpose:

1. Out-of-scope detection — this is a banking assistant, not a general
   chatbot. Answering "what's the weather" or writing someone's essay
   isn't just unhelpful, it's a trust problem: it blurs what the
   assistant is actually authorized to do with a customer's account.
2. Prompt injection detection — basic pattern matching for attempts to
   override the system prompt ("ignore previous instructions", "you are
   now..."). Not a substitute for a real jailbreak-resistant model, but
   a first line of defense worth having.

Same heuristic/keyword approach as intent_node's classifier, and same
caveat: this is a placeholder for a real LLM-based classifier, kept
rule-based so it's fast, free, and fully testable without a live model.
The function signatures are the contract to preserve when that swap
happens.
"""

BANKING_KEYWORDS = [
    "account", "balance", "card", "transfer", "loan", "emi", "transaction",
    "fee", "bank", "payment", "deposit", "withdraw", "fraud", "dispute",
    "limit", "statement", "interest", "credit", "debit", "overdraft",
    "wire", "atm", "savings", "checking",
]

# Deliberately common, unambiguous off-topic requests — kept conservative
# (few false positives) rather than exhaustive, since a false positive
# here blocks a genuine banking question and a false negative just means
# one off-topic question slips through to a normal (harmless) RAG miss.
OUT_OF_SCOPE_KEYWORDS = [
    "weather", "tell me a joke", "write me a poem", "write a poem",
    "recipe for", "write me an essay", "write my essay", "write code",
    "python script", "who won the game", "sports score", "capital of",
    "president of", "movie recommendation", "song lyrics", "meaning of life",
    "translate this", "homework help", "write my resume",
]

PROMPT_INJECTION_PATTERNS = [
    "ignore previous instructions", "ignore all previous", "disregard previous",
    "disregard all previous", "you are now", "new instructions:", "system prompt",
    "reveal your prompt", "reveal your system", "act as if you", "jailbreak",
    "pretend you are not", "forget your instructions",
]


def is_out_of_scope(text: str) -> bool:
    """
    True only if the message matches a clear off-topic pattern AND
    contains no banking keywords — a message that mixes both ("what's
    the weather doing to interest rates on my loan?") is left alone,
    since it's plausibly still a real banking question.
    """
    lowered = text.lower()
    has_banking_kw = any(kw in lowered for kw in BANKING_KEYWORDS)
    has_off_topic_kw = any(kw in lowered for kw in OUT_OF_SCOPE_KEYWORDS)
    return has_off_topic_kw and not has_banking_kw


def is_prompt_injection(text: str) -> bool:
    lowered = text.lower()
    return any(pattern in lowered for pattern in PROMPT_INJECTION_PATTERNS)


def check(text: str) -> "tuple[bool, str | None]":
    """
    Single entry point for guardrail_node. Returns (blocked, reason)
    where reason is "prompt_injection", "out_of_scope", or None.
    Injection is checked first — a message can match both (e.g. an
    off-topic injection attempt), and injection is the more specific,
    more important classification to surface.
    """
    if is_prompt_injection(text):
        return True, "prompt_injection"
    if is_out_of_scope(text):
        return True, "out_of_scope"
    return False, None