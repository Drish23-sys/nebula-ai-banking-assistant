"""
backend/llm_client.py

Wraps the local Ollama server for natural-language reply generation
(PRD §1.2/§2.2 — Qwen2.5-3B-Instruct as the VRAM-safe primary model).

This is intentionally a thin, fail-soft wrapper: if Ollama isn't
running, isn't reachable, or the model isn't pulled yet, generate_reply()
returns None rather than raising — callers (main.py's _draft_reply)
fall back to the deterministic template in that case. This means the
whole API stays fully testable (as it has been throughout this build)
even in environments without a live Ollama server, like this sandbox.

Grounding discipline: the system prompt explicitly instructs the model
to answer ONLY from the provided tool_result/retrieved_docs context and
say so plainly if the context doesn't cover the question, rather than
inventing banking details. This matters more here than in a generic
chatbot — a hallucinated fee or policy is a real trust failure for a
banking assistant, not just an inconvenience.
"""

import os
import sys
from typing import Any, Dict, List, Optional

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from backend.config import (  # noqa: E402
    OLLAMA_HOST,
    PRIMARY_LLM_MODEL,
    LLM_PROVIDER,
    GROQ_API_KEY,
    GROQ_FALLBACK_MODEL,
)

SYSTEM_PROMPT = """You are Nebula, a banking assistant. Answer the customer's \
message using ONLY the CONTEXT provided below — never invent account details, \
fees, or policy terms that aren't in the context. If the context doesn't \
contain what's needed to answer, say so plainly and offer to connect them \
with a specialist. Keep replies to 2-3 sentences, warm but professional, no \
markdown formatting (this is a chat message, not a document)."""


def _build_context_block(
    tool_result: Optional[Dict[str, Any]], retrieved_docs: Optional[List[Dict[str, Any]]]
) -> str:
    parts = []
    if tool_result:
        parts.append(f"TOOL RESULT:\n{tool_result}")
    if retrieved_docs:
        valid = [d for d in retrieved_docs if isinstance(d, dict) and "text" in d]
        if valid:
            docs_text = "\n\n".join(f"[{d['source']} / {d['section']}]\n{d['text']}" for d in valid)
            parts.append(f"RETRIEVED POLICY TEXT:\n{docs_text}")
    return "\n\n".join(parts) if parts else "(no supporting context available)"


def _history_to_chat_messages(conversation_history: Optional[List[Any]], max_turns: int = 8) -> List[Dict[str, str]]:
    """
    Converts recent LangGraph BaseMessage objects into the plain
    role/content dicts both Ollama and Groq expect, so prior turns
    actually reach the model instead of every reply being generated as
    if the conversation just started. Capped to the last `max_turns`
    messages — enough for real continuity (referring back to something
    said a couple of turns ago, not restating context already given)
    without unbounded prompt growth as a session gets long.
    """
    if not conversation_history:
        return []

    from langchain_core.messages import AIMessage, HumanMessage

    out = []
    for msg in conversation_history[-max_turns:]:
        if isinstance(msg, HumanMessage):
            out.append({"role": "user", "content": msg.content})
        elif isinstance(msg, AIMessage):
            out.append({"role": "assistant", "content": msg.content})
        # Other message types (System, Tool) skipped — SYSTEM_PROMPT
        # already covers the system role, and tool messages aren't
        # meaningful to replay as conversational turns.
    return out


def generate_reply(
    user_message: str,
    tool_result: Optional[Dict[str, Any]] = None,
    retrieved_docs: Optional[List[Dict[str, Any]]] = None,
    conversation_history: Optional[List[Any]] = None,
    model: Optional[str] = None,
    timeout_seconds: float = 30.0,
) -> Optional[str]:
    """
    Returns a generated reply string, or None if the LLM couldn't be
    reached / errored — callers must handle the None case with a
    fallback, never assume this always succeeds.

    Routes to Ollama or Groq based on config.LLM_PROVIDER — see that
    var's comment in config.py. Same system prompt and context-block
    logic either way, so reply quality/grounding behavior is consistent
    regardless of which provider is active.

    conversation_history (recent prior turns from the graph's checkpointed
    `messages`, NOT including the current turn) is threaded through as
    real chat history rather than folded into the text block, so the
    model can actually reference earlier parts of the conversation —
    without this, every reply was generated as if the conversation had
    just started, regardless of what was said a turn or two earlier.
    """
    context_block = _build_context_block(tool_result, retrieved_docs)
    user_prompt = f"CONTEXT:\n{context_block}\n\nCUSTOMER MESSAGE:\n{user_message}"
    history_messages = _history_to_chat_messages(conversation_history)

    if LLM_PROVIDER == "groq":
        return _generate_reply_groq(user_prompt, history_messages, model or GROQ_FALLBACK_MODEL, timeout_seconds)
    return _generate_reply_ollama(user_prompt, history_messages, model or PRIMARY_LLM_MODEL, timeout_seconds)


def _generate_reply_ollama(
    user_prompt: str, history_messages: List[Dict[str, str]], model: str, timeout_seconds: float
) -> Optional[str]:
    try:
        import ollama

        client = ollama.Client(host=OLLAMA_HOST, timeout=timeout_seconds)
        response = client.chat(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                *history_messages,
                {"role": "user", "content": user_prompt},
            ],
            options={"temperature": 0.3},
        )
        text = response.get("message", {}).get("content", "").strip()
        return text or None

    except Exception as exc:
        # Covers: Ollama not running, model not pulled, connection refused,
        # timeout, malformed response — all fail soft to None on purpose.
        print(f"[llm_client] Ollama generation failed, falling back to template: {exc}")
        return None


def _generate_reply_groq(
    user_prompt: str, history_messages: List[Dict[str, str]], model: str, timeout_seconds: float
) -> Optional[str]:
    if not GROQ_API_KEY:
        print("[llm_client] LLM_PROVIDER=groq but no GROQ_API_KEY set — falling back to template.")
        return None

    try:
        from groq import Groq

        client = Groq(api_key=GROQ_API_KEY, timeout=timeout_seconds)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                *history_messages,
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
        )
        text = (response.choices[0].message.content or "").strip()
        return text or None

    except Exception as exc:
        # Same fail-soft contract as the Ollama path and as
        # groq_client.py's handover-summary calls — missing/invalid key,
        # network error, rate limit, malformed response all fall back
        # to the deterministic template rather than raising.
        print(f"[llm_client] Groq generation failed, falling back to template: {exc}")
        return None


if __name__ == "__main__":
    # Smoke test — expected to print the fallback warning and return None
    # in any environment without a live Ollama server (like this sandbox).
    # On a machine with `ollama serve` + `ollama pull qwen2.5:3b-instruct-q4_K_M`
    # running, this should instead print a real generated reply.
    result = generate_reply(
        user_message="What's the fee for an international wire transfer over $10,000?",
        retrieved_docs=[
            {
                "text": "Outbound international wire transfers over $10,000 incur a flat fee of $45.00 plus a 0.2% currency exchange margin.",
                "source": "wire_transfers.md",
                "section": "Section 4.2",
            }
        ],
    )
    print(f"\nResult: {result!r}")