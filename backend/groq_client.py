"""
backend/groq_client.py

Wraps the Groq Cloud API to generate the 4-part handover summary
(PRD §4.3) when a session escalates to a human agent. Groq is used ONLY
for this — never on the live conversational hot path (that's Ollama,
see llm_client.py) — because it's the one place where the extra latency
of a cloud round-trip is acceptable: a human is about to spend minutes
reading the ticket anyway, so a couple of seconds generating a genuinely
good summary is a fair trade, unlike a live chat reply where every
second is felt directly.

Same fail-soft contract as llm_client.py: returns None on any error
(missing API key, network issue, rate limit, malformed response) —
handover_node falls back to its deterministic template in that case.
A missing/broken Groq key should degrade the ticket summary's quality,
never break the handover flow itself.
"""

import json
import os
import sys
from typing import Any, Dict, List, Optional

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from backend.config import GROQ_API_KEY, GROQ_HANDOVER_MODEL  # noqa: E402

SYSTEM_PROMPT = """You are writing a handover summary for a human banking \
support agent who is about to take over this conversation. Read the \
conversation and any tool actions taken, then respond with ONLY a JSON \
object (no markdown, no code fences, no commentary) with exactly these \
4 keys:

- "issue": one sentence describing what the customer actually needs, in \
  their own terms.
- "context": relevant account/session facts the agent needs (user id, \
  account, transaction, or card details mentioned).
- "attempted_resolution": what the AI already tried or found before \
  escalating, if anything.
- "suggested_next_step": one concrete, actionable next step for the \
  human agent.

Be concise — 1-2 sentences per field. Do not invent details not present \
in the conversation or tool results."""


def _format_messages_for_prompt(messages: List[Any]) -> str:
    lines = []
    for m in messages:
        role = getattr(m, "type", None) or m.__class__.__name__.replace("Message", "").lower()
        role = "customer" if role in ("human", "user") else "assistant"
        content = getattr(m, "content", str(m))
        lines.append(f"{role}: {content}")
    return "\n".join(lines) if lines else "(no conversation history)"


def generate_handover_summary(
    messages: List[Any],
    tool_calls: List[Dict[str, Any]],
    active_intent: str,
    user_id: str,
    model: str = GROQ_HANDOVER_MODEL,
    timeout_seconds: float = 15.0,
) -> Optional[Dict[str, str]]:
    """
    Returns a dict with exactly the 4 keys frozen in
    docs/FRONTEND_HANDOFF.md (issue/context/attempted_resolution/
    suggested_next_step), or None if generation failed for any reason.
    """
    if not GROQ_API_KEY:
        print("[groq_client] No GROQ_API_KEY set — skipping, handover_node will use its template.")
        return None

    try:
        from groq import Groq

        client = Groq(api_key=GROQ_API_KEY, timeout=timeout_seconds)

        conversation_text = _format_messages_for_prompt(messages)
        tool_calls_text = json.dumps(tool_calls, default=str) if tool_calls else "(none)"

        user_prompt = (
            f"USER ID: {user_id}\n"
            f"CLASSIFIED INTENT: {active_intent}\n\n"
            f"CONVERSATION:\n{conversation_text}\n\n"
            f"TOOL CALLS ATTEMPTED:\n{tool_calls_text}"
        )

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content
        parsed = json.loads(raw)

        required_keys = {"issue", "context", "attempted_resolution", "suggested_next_step"}
        if not required_keys.issubset(parsed.keys()):
            print(f"[groq_client] Response missing required keys: {parsed.keys()}. Falling back.")
            return None

        return {k: str(parsed[k]) for k in required_keys}

    except Exception as exc:
        # Covers: missing/invalid API key, network error, rate limit,
        # malformed JSON response — all fail soft to None on purpose.
        print(f"[groq_client] Groq summary generation failed, falling back to template: {exc}")
        return None


if __name__ == "__main__":
    from langchain_core.messages import HumanMessage

    # Smoke test — with no GROQ_API_KEY set, expected to print the
    # "skipping" message and return None. With a real key in .env, this
    # should print a real 4-part JSON summary instead.
    result = generate_handover_summary(
        messages=[HumanMessage(content="I see an unauthorized $1,800 charge, my card was stolen")],
        tool_calls=[],
        active_intent="HANDOVER",
        user_id="USR-4401",
    )
    print(f"\nResult: {result}")