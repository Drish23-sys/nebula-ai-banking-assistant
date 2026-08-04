"""
backend/main.py

FastAPI app implementing the exact contract frozen in
docs/FRONTEND_HANDOFF.md §1. Run with:

    uvicorn backend.main:app --reload --port 8000

Request flow for POST /chat:
    1. Check session_store.get_conversation_mode() FIRST, before touching
       the graph at all (PRD §4.5 — durable routing flag is the source
       of truth, not any in-graph state).
    2. If "human": just log the message, don't invoke the agent.
    3. If "ai": invoke the compiled LangGraph graph, translate its
       AgentState result into the frozen response shape, and if this
       turn triggered a handover, create a ticket + flip conversation_mode.
"""

import os
import sys
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/..")
from backend import session_store  # noqa: E402
from backend.agent.graph import compiled_graph  # noqa: E402
from backend.agent.state import new_state  # noqa: E402
from backend.config import API_HOST, API_PORT  # noqa: E402
from backend.llm_client import generate_reply  # noqa: E402

app = FastAPI(title="Nebula AI Banking Assistant")


@app.on_event("startup")
def _startup() -> None:
    session_store.init_schema()


# ---------------------------------------------------------------------------
# Request/response models — mirror docs/FRONTEND_HANDOFF.md §1 exactly.
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    session_id: str
    user_id: str
    message: str


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    conversation_mode: str
    confidence: Optional[Dict[str, Any]] = None
    citations: List[Dict[str, str]] = []
    quick_actions: List[str] = []
    handover_triggered: bool = False


class AgentReplyRequest(BaseModel):
    message: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _infer_trigger_reason(active_intent: str, last_user_text: str) -> str:
    """
    Maps a handover-triggering turn to one of the 4 reasons from
    docs/FRONTEND_HANDOFF.md's `trigger_reason` enum. Heuristic today
    (matches nodes.py's HANDOVER_KEYWORDS); TODO(later): have
    handover_node itself classify this from the actual escalation path
    instead of re-deriving it here from raw text.
    """
    lowered = last_user_text.lower()
    fraud_words = ("fraud", "unauthorized", "stolen", "hacked")
    if active_intent == "HANDOVER" and any(w in lowered for w in fraud_words):
        return "fraud_flag"
    if active_intent == "HANDOVER":
        return "explicit_request"
    return "low_confidence_repeated"


def _draft_reply(result: Dict[str, Any], user_message: str) -> str:
    """
    Tries a real Ollama-generated reply first, grounded in this turn's
    tool_result / retrieved_docs. Falls back to the deterministic
    template (same one used since Day 3) if Ollama isn't reachable —
    generate_reply() never raises, it returns None on any failure, so
    this function always returns *something* sensible either way.
    """
    intent = result.get("active_intent", "")

    if intent == "HANDOVER":
        return "I'm connecting you with a human support specialist who can help with this right away."

    tool_calls = result.get("tool_calls", [])
    last_tool_result = tool_calls[-1]["result"] if tool_calls else None
    retrieved_docs = result.get("retrieved_docs", [])

    # A failed tool call (e.g. Pydantic validation error, no account
    # found) shouldn't be dressed up by the LLM — surface it directly
    # and plainly rather than risking a generated response that papers
    # over the error.
    if last_tool_result and last_tool_result.get("status") == "error":
        return last_tool_result.get("message", "I ran into an issue completing that — let me connect you with support.")

    generated = generate_reply(
        user_message=user_message, tool_result=last_tool_result, retrieved_docs=retrieved_docs
    )
    if generated:
        return generated

    # --- Fallback template (Ollama unreachable) ---
    if last_tool_result:
        return last_tool_result.get("message", "Done.")

    valid_docs = [d for d in retrieved_docs if isinstance(d, dict) and "text" in d]
    if valid_docs:
        return valid_docs[0]["text"]

    return "Could you tell me a bit more about what you need help with?"


def _build_citations(result: Dict[str, Any]) -> List[Dict[str, str]]:
    retrieved_docs = result.get("retrieved_docs", []) or []
    return [
        {"source": d["source"], "section": d["section"]}
        for d in retrieved_docs
        if isinstance(d, dict) and "source" in d and "section" in d
    ]


def _build_quick_actions(decision: Optional[str]) -> List[str]:
    if decision == "clarify":
        return ["Yes, that's right", "No, something else"]
    return []


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    session = session_store.get_or_create_session(req.session_id, req.user_id)
    session_store.append_message(req.session_id, "user", req.message)

    if session["conversation_mode"] == "human":
        # A live agent owns this session — don't invoke the AI graph at
        # all (§4.5). The message is logged; Tab 2 will show it via polling.
        return ChatResponse(
            session_id=req.session_id,
            reply="",
            conversation_mode="human",
            handover_triggered=False,
        )

    graph_state = new_state(session_id=req.session_id, user_id=req.user_id)
    graph_state["messages"] = [HumanMessage(content=req.message)]

    config = {"configurable": {"thread_id": req.session_id}}
    result = compiled_graph.invoke(graph_state, config=config)

    reply_text = _draft_reply(result, req.message)
    handover_triggered = bool(result.get("is_handover_active"))

    if handover_triggered:
        trigger_reason = _infer_trigger_reason(result.get("active_intent", ""), req.message)
        session_store.create_ticket(
            req.session_id, trigger_reason, result.get("handover_summary") or {}
        )
        session_store.set_conversation_mode(req.session_id, "human")

    session_store.append_message(req.session_id, "assistant", reply_text)

    confidence = None
    if result.get("confidence_breakdown"):
        confidence = {
            **result["confidence_breakdown"],
            "composite": result.get("confidence_score"),
            "decision": result.get("decision"),
        }

    return ChatResponse(
        session_id=req.session_id,
        reply=reply_text,
        conversation_mode="human" if handover_triggered else "ai",
        confidence=confidence,
        citations=_build_citations(result),
        quick_actions=_build_quick_actions(result.get("decision")),
        handover_triggered=handover_triggered,
    )


@app.get("/chat/{session_id}/status")
def chat_status(session_id: str, since: Optional[str] = None) -> Dict[str, Any]:
    mode = session_store.get_conversation_mode(session_id)
    new_messages = session_store.get_messages_since(session_id, since_timestamp=since)
    return {
        "conversation_mode": mode,
        "new_messages": [
            {"role": m["role"], "text": m["text"], "timestamp": m["timestamp"]} for m in new_messages
        ],
    }


@app.get("/agent/queue")
def agent_queue() -> Dict[str, Any]:
    tickets_raw = session_store.list_open_tickets()
    tickets = [
        {
            "ticket_id": t["ticket_id"],
            "session_id": t["session_id"],
            "user_id": t["user_id"],
            "user_name": t.get("user_name") or t["user_id"],
            "trigger_reason": t["trigger_reason"],
            "created_at": t["created_at"],
            "summary": t["summary"],
        }
        for t in tickets_raw
    ]
    return {"tickets": tickets}


@app.post("/agent/{ticket_id}/reply")
def agent_reply(ticket_id: str, req: AgentReplyRequest) -> Dict[str, str]:
    tickets = {t["ticket_id"]: t for t in session_store.list_open_tickets()}
    ticket = tickets.get(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found or already resolved.")

    session_store.append_message(ticket["session_id"], "agent", req.message)
    return {"status": "sent"}


@app.post("/agent/{ticket_id}/resolve")
def agent_resolve(ticket_id: str) -> Dict[str, str]:
    try:
        session_store.resolve_ticket(ticket_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"status": "resolved", "conversation_mode": "ai"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.main:app", host=API_HOST, port=API_PORT, reload=True)