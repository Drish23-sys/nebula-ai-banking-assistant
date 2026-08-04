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
from backend.config import OLLAMA_HOST, PRIMARY_LLM_MODEL  # noqa: E402

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


def generate_reply(
    user_message: str,
    tool_result: Optional[Dict[str, Any]] = None,
    retrieved_docs: Optional[List[Dict[str, Any]]] = None,
    model: str = PRIMARY_LLM_MODEL,
    timeout_seconds: float = 15.0,
) -> Optional[str]:
    """
    Returns a generated reply string, or None if Ollama couldn't be
    reached / errored — callers must handle the None case with a
    fallback, never assume this always succeeds.
    """
    try:
        import ollama

        client = ollama.Client(host=OLLAMA_HOST, timeout=timeout_seconds)
        context_block = _build_context_block(tool_result, retrieved_docs)

        response = client.chat(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"CONTEXT:\n{context_block}\n\nCUSTOMER MESSAGE:\n{user_message}"},
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