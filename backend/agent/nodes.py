"""
backend/agent/nodes.py

LangGraph node functions (PRD §3.1, Day 2 + Day 3 scope).

Day 2 scope (this file, functional):
    - intent_node   : classifies user input -> TOOL_CALL | RAG_QUERY | HANDOVER
    - tool_node      : executes sandbox tools via Pydantic-validated calls
    - rag_node       : ChromaDB similarity search, returns top chunks

Day 3 scope (stubbed here, filled in next):
    - confidence_node : computes C = 0.4*retrieval + 0.4*grounding + 0.2*intent
    - handover_node    : builds the 4-part Groq summary, sets is_handover_active

Each node takes the full AgentState and returns a partial dict of the
fields it updates — this is the standard LangGraph node contract, NOT a
new state object. LangGraph merges the returned dict into state for you.
"""

import os
import re
import sys
from typing import Any, Dict

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from backend.agent.state import AgentState  # noqa: E402
from backend.config import (  # noqa: E402
    CHROMA_COLLECTION_NAME,
    CHROMA_PERSIST_DIR,
    CONFIDENCE_AUTO_EXECUTE_THRESHOLD,
    CONFIDENCE_CLARIFY_THRESHOLD,
    CONFIDENCE_WEIGHTS,
    UNCLEAR_ATTEMPTS_HANDOVER_LIMIT,
)

# ---------------------------------------------------------------------------
# Keyword tables — placeholder for the real Pydantic-parsed intent
# classifier that will call the local LLM (Ollama). Kept rule-based for
# Day 2 so the graph shape and every downstream node can be built and
# tested without a live Ollama server. Swap the body of intent_node for
# an LLM call once Ollama is wired up — the return contract stays the same.
# ---------------------------------------------------------------------------
TOOL_INTENT_KEYWORDS = {
    "CHECK_BALANCE": ["balance", "how much do i have", "how much money"],
    "LOCK_CARD": ["lock my card", "lock card", "freeze my card", "stolen card", "lost card"],
    "TRANSACTION_HISTORY": ["transaction history", "recent transactions", "recent charges"],
    "CHECK_TRANSFER_LIMIT": ["transfer limit", "how much can i transfer", "how much can i send"],
    "CALCULATE_LOAN_EMI": ["loan emi", "monthly payment", "emi for"],
}

HANDOVER_KEYWORDS = [
    "unauthorized transaction", "unauthorized charge", "stolen card", "fraud",
    "account hacked", "bereavement", "talk to human", "talk to a human",
    "representative", "human agent", "speak to agent", "operator",
]


def _classify_intent(text: str) -> str:
    lowered = text.lower()

    if any(kw in lowered for kw in HANDOVER_KEYWORDS):
        return "HANDOVER"

    for intent_name, keywords in TOOL_INTENT_KEYWORDS.items():
        if any(kw in lowered for kw in keywords):
            return intent_name

    return "RAG_QUERY"


def intent_node(state: AgentState) -> Dict[str, Any]:
    """
    Classifies the latest user message into an active_intent, and applies
    the Topic Stack Algorithm (PRD §4.1): if the new intent differs from
    the previous one and the previous one was incomplete, push it onto
    topic_stack before switching.
    """
    if not state["messages"]:
        return {"active_intent": ""}

    latest_message = state["messages"][-1]
    text = getattr(latest_message, "content", str(latest_message))

    new_intent = _classify_intent(text)
    previous_intent = state.get("active_intent", "")

    topic_stack = list(state.get("topic_stack", []))

    # Step 1-2 of the Topic Stack Algorithm: push the old intent if we're
    # shifting away from it before it's finished. "Incomplete" here is a
    # placeholder heuristic (any non-empty, non-RAG_QUERY intent counts as
    # in-progress) — Day 3 will refine this once tool_node can report
    # completion state explicitly.
    #
    # HANDOVER is excluded on both sides: it's a terminal escalation, not
    # a resumable topic, so it should never itself be pushed onto the
    # stack, and nothing should be pushed while it's the active intent
    # (a live handover shouldn't accumulate a queue of "resume later"
    # topics — those get surfaced to the human agent instead, via
    # handover_summary, not silently replayed to the AI afterward).
    if (
        previous_intent
        and new_intent != previous_intent
        and previous_intent not in ("RAG_QUERY", "HANDOVER")
        and new_intent != "HANDOVER"
    ):
        topic_stack.append(previous_intent)

    return {
        "active_intent": new_intent,
        "topic_stack": topic_stack,
    }


def tool_node(state: AgentState) -> Dict[str, Any]:
    """
    Executes the sandbox tool matching active_intent via
    backend/sandbox/tools.py's execute_tool dispatch (Pydantic-validated
    per tool, per PRD §4.2).
    """
    from backend.sandbox.tools import execute_tool  # local import: keeps nodes.py

    intent = state.get("active_intent", "")
    result = execute_tool(intent, state["user_id"])

    call_record = {"intent": intent, "user_id": state["user_id"], "result": result}

    return {"tool_calls": state.get("tool_calls", []) + [call_record]}


def rag_node(state: AgentState) -> Dict[str, Any]:
    """
    Similarity search against the ChromaDB policy knowledge base.
    Returns top-k chunks with their retrieval (cosine similarity) score —
    this score is S_retrieval, one of the 3 terms in the confidence
    formula (PRD §4.2), computed properly in confidence_node.
    """
    if not state["messages"]:
        return {"retrieved_docs": []}

    latest_message = state["messages"][-1]
    query_text = getattr(latest_message, "content", str(latest_message))

    try:
        import chromadb
        from fastembed import TextEmbedding

        client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
        collection = client.get_collection(CHROMA_COLLECTION_NAME)

        embedder = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
        query_embedding = list(embedder.embed([query_text]))[0].tolist()

        results = collection.query(query_embeddings=[query_embedding], n_results=3)

        retrieved_docs = [
            {
                "text": doc,
                "source": meta.get("source"),
                "section": meta.get("section"),
                "similarity": 1 - dist,  # cosine distance -> similarity
            }
            for doc, meta, dist in zip(
                results["documents"][0], results["metadatas"][0], results["distances"][0]
            )
        ]
    except Exception as exc:
        # Collection not built yet, or fastembed can't reach the network
        # in this environment — fail soft so graph wiring can still be
        # tested without a live ChromaDB collection.
        retrieved_docs = [{"error": str(exc)}]

    return {"retrieved_docs": retrieved_docs}


# ---------------------------------------------------------------------------
# Day 3: real confidence math + handover.
# ---------------------------------------------------------------------------

# S_intent proxy values. Today's intent_node is a rule-based keyword
# matcher, not an LLM classifier, so there's no native confidence number
# to read — these stand in for "how sure are we this intent label is
# right" until intent_node is swapped for an LLM call with real logprob-
# or self-reported confidence (contract on intent_node's return stays
# the same, so that swap won't require changes here).
INTENT_CONFIDENCE_MATCHED_TOOL = 0.95   # matched a specific TOOL_INTENT_KEYWORDS entry
INTENT_CONFIDENCE_RAG_FALLBACK = 0.75   # fell through to the RAG_QUERY catch-all
INTENT_CONFIDENCE_HANDOVER = 1.0        # explicit handover keyword — unambiguous by design


def confidence_node(state: AgentState) -> Dict[str, Any]:
    """
    Computes C = 0.4*S_retrieval + 0.4*S_grounding + 0.2*S_intent (PRD §4.2),
    then applies the decision matrix from config.py's thresholds:
        C >= CONFIDENCE_AUTO_EXECUTE_THRESHOLD (0.65)  -> auto_respond
        CONFIDENCE_CLARIFY_THRESHOLD <= C < 0.65 (0.50) -> clarify
        C < 0.50                                        -> handover
    Also enforces the "2 consecutive low-confidence turns" handover
    trigger (§4.3) via unclear_attempts, independent of the single-turn
    score — e.g. two 0.55 turns in a row escalate even though neither
    alone crosses the 0.50 hard floor.

    TODO(Day 4): S_grounding is currently proxied by retrieval similarity
    (best top-1 chunk match). The real grounding check — does the LLM's
    *generated* answer's claims actually appear in the retrieved chunks —
    needs the live Ollama answer-generation step, which isn't wired yet.
    Swap the `s_grounding = s_retrieval` line below once that lands.
    """
    intent = state.get("active_intent", "")
    retrieved_docs = state.get("retrieved_docs", []) or []

    # --- S_intent ---
    if intent == "HANDOVER":
        s_intent = INTENT_CONFIDENCE_HANDOVER
    elif intent and intent != "RAG_QUERY":
        s_intent = INTENT_CONFIDENCE_MATCHED_TOOL
    elif intent == "RAG_QUERY":
        s_intent = INTENT_CONFIDENCE_RAG_FALLBACK
    else:
        s_intent = 0.0

    # --- S_retrieval / S_grounding ---
    valid_docs = [d for d in retrieved_docs if isinstance(d, dict) and "similarity" in d]
    if valid_docs:
        s_retrieval = max(d["similarity"] for d in valid_docs)
        s_grounding = s_retrieval  # proxy — see TODO above
    else:
        # No RAG performed on this turn (tool-call or handover branch) —
        # this dimension isn't applicable, so don't let it drag the
        # composite down. Treated as fully satisfied rather than unknown.
        s_retrieval = 1.0
        s_grounding = 1.0

    weights = CONFIDENCE_WEIGHTS
    composite = (
        weights["retrieval"] * s_retrieval
        + weights["grounding"] * s_grounding
        + weights["intent"] * s_intent
    )
    composite = round(composite, 4)

    # HANDOVER intent already routes straight to handover_node in the
    # graph (see route_after_intent) — confidence_node still runs after
    # it via the shared edge, but the decision label here is informational
    # only in that case, not used for further routing.
    if intent == "HANDOVER":
        decision = "handover"
        unclear_attempts = state.get("unclear_attempts", 0)
    elif composite < CONFIDENCE_CLARIFY_THRESHOLD:
        decision = "handover"
        unclear_attempts = state.get("unclear_attempts", 0) + 1
    elif composite < CONFIDENCE_AUTO_EXECUTE_THRESHOLD:
        unclear_attempts = state.get("unclear_attempts", 0) + 1
        decision = (
            "handover" if unclear_attempts >= UNCLEAR_ATTEMPTS_HANDOVER_LIMIT else "clarify"
        )
    else:
        decision = "auto_respond"
        unclear_attempts = 0  # reset the streak on a confident turn

    return {
        "confidence_score": composite,
        "confidence_breakdown": {
            "retrieval": s_retrieval,
            "grounding": s_grounding,
            "intent": s_intent,
        },
        "decision": decision,
        "unclear_attempts": unclear_attempts,
    }


def handover_node(state: AgentState) -> Dict[str, Any]:
    """
    Builds the handover summary. Field names match the exact 4-part
    shape already promised to the frontend team in
    docs/FRONTEND_HANDOFF.md §1 (`GET /agent/queue` -> ticket.summary):
        issue, context, attempted_resolution, suggested_next_step

    TODO(Day 4/later today): replace this deterministic template with a
    real Groq call (PRD §4.3) that reads the full conversation and
    tool_calls history to write a genuinely useful summary. Keeping the
    field names fixed now means that swap is a body-only change here —
    main.py and the frontend don't need to change at all when it lands.
    """
    intent = state.get("active_intent", "UNKNOWN")
    tool_calls = state.get("tool_calls", [])
    messages = state.get("messages", [])

    last_user_text = ""
    for m in reversed(messages):
        if getattr(m, "type", None) == "human" or m.__class__.__name__ == "HumanMessage":
            last_user_text = getattr(m, "content", "")
            break

    attempted = (
        f"AI attempted {len(tool_calls)} tool call(s) before escalating."
        if tool_calls
        else "AI had not yet taken any action before escalating."
    )

    summary = {
        "issue": last_user_text or f"Customer request classified as {intent}.",
        "context": f"Session for user {state['user_id']}, classified intent: {intent}.",
        "attempted_resolution": attempted,
        "suggested_next_step": "Review conversation history and verify customer identity before proceeding.",
    }

    return {
        "is_handover_active": True,
        "handover_summary": summary,
    }
