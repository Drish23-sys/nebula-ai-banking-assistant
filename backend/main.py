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
import time
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/..")
from backend import auth, session_store  # noqa: E402
from backend.redaction import redact_sensitive  # noqa: E402
from backend.agent.graph import compiled_graph, TOOL_INTENTS  # noqa: E402
from backend.agent.state import new_state  # noqa: E402
from backend.config import API_HOST, API_PORT  # noqa: E402
from backend.llm_client import generate_reply  # noqa: E402

app = FastAPI(title="Nebula AI Banking Assistant")

# CORS: the customer-chat React app and the agent-dashboard React app run
# as separate origins (different dev ports locally, different Vercel
# domains in prod), and the browser blocks cross-origin fetches unless
# the server explicitly allows them. ALLOWED_ORIGINS is env-driven so
# prod domains can be added without touching code — see backend/config.py.
from backend.config import ALLOWED_ORIGINS  # noqa: E402

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    session_store.init_schema()

    from backend.sandbox.database import backfill_missing_bank_profiles, init_schema as init_sandbox_schema
    init_sandbox_schema()
    backfilled = backfill_missing_bank_profiles()
    if backfilled:
        print(f"[startup] Backfilled bank profiles for {backfilled} account(s) that were missing one.")

    # Ollama loads the model into memory/VRAM on its first request
    # (~15-18s observed), then stays fast (~3.5-4.5s) for subsequent
    # calls as long as it stays loaded — paying that cost here, once, at
    # startup means the first real user never eats it. When
    # LLM_PROVIDER=groq (deployment), there's no local model to warm —
    # this becomes a plain connectivity check instead, and stays fast.
    from backend.config import LLM_PROVIDER
    label = "Ollama model" if LLM_PROVIDER == "ollama" else "Groq connectivity"
    print(f"[startup] Warming up {label}...")
    start = time.time()
    result = generate_reply(user_message="Hello", tool_result={"status": "success", "message": "warm-up"})
    elapsed = time.time() - start
    if result:
        print(f"[startup] {label} ready ({elapsed:.1f}s).")
    else:
        hint = "Ollama may not be running" if LLM_PROVIDER == "ollama" else "check GROQ_API_KEY"
        print(f"[startup] Warm-up call failed after {elapsed:.1f}s — {hint}. "
              f"/chat will fall back to template replies until it's available.")


# ---------------------------------------------------------------------------
# Request/response models — mirror docs/FRONTEND_HANDOFF.md §1 exactly.
# ---------------------------------------------------------------------------
class SignupRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class AuthResponse(BaseModel):
    user_id: str
    email: str
    token: str
    session_id: str  # derived deterministically from user_id — see require_user()


def _session_id_for_user(user_id: str) -> str:
    """One persistent conversation per account, so a returning customer
    resumes their same thread automatically rather than starting fresh
    every login. Deterministic on purpose — no need to store this
    mapping separately."""
    return f"sess_{user_id}"


def require_user(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    """FastAPI dependency for routes that need a logged-in customer.
    Expects `Authorization: Bearer <token>`. Raises 401 on anything
    missing/invalid — deliberately the same error for both, same
    reasoning as auth.login()'s generic message."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header.")
    token = authorization.removeprefix("Bearer ").strip()
    user = auth.get_current_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired session — please log in again.")
    return user


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
def _draft_reply(result: Dict[str, Any], user_message: str) -> str:
    """
    Tries a real Ollama-generated reply first, grounded in this turn's
    tool_result / retrieved_docs. Falls back to the deterministic
    template (same one used since Day 3) if Ollama isn't reachable —
    generate_reply() never raises, it returns None on any failure, so
    this function always returns *something* sensible either way.
    """
    intent = result.get("active_intent", "")

    if intent == "OUT_OF_SCOPE":
        return ("I'm the Nebula banking assistant, so I can only help with account, card, "
                "transfer, and banking policy questions. What can I help you with on that front?")

    if intent == "SMALL_TALK":
        # Skips the LLM entirely rather than routing through generate_reply()
        # — that call's system prompt is deliberately strict about only
        # answering from provided context (to prevent hallucinated banking
        # details), and a greeting/thanks has no context by design. Asking
        # it to improvise a reply anyway risks a confused "I don't have
        # information about that" response to a plain "hi".
        lowered = user_message.lower()
        if any(kw in lowered for kw in ("thank", "thnq", "tysm")):
            return "You're welcome! Let me know if there's anything else I can help with."
        if any(kw in lowered for kw in ("bye", "goodbye", "cya", "see you")):
            return "Take care! Reach out anytime you need help with your account."
        return ("Hi there! I'm Nebula, your banking assistant. I can help with your "
                "balance, cards, transfers, or account policies — what can I do for you?")

    if intent == "HANDOVER":
        return "I'm connecting you with a human support specialist who can help with this right away."

    tool_calls = result.get("tool_calls", [])
    # Bug fix: tool_calls is an intentionally-accumulating history (used
    # later for the handover summary's "attempted N tool calls" count),
    # never cleared between turns — so tool_calls[-1] is only genuinely
    # *this turn's* result when a tool actually ran this turn. Without
    # this guard, a RAG_QUERY or SMALL_TALK turn several messages after
    # a tool call would silently reuse that old result (or worse, an old
    # *error*) as if it were the answer to the current, unrelated question.
    last_tool_result = tool_calls[-1]["result"] if (tool_calls and intent in TOOL_INTENTS) else None
    retrieved_docs = result.get("retrieved_docs", [])

    # A failed tool call (e.g. Pydantic validation error, no account
    # found) shouldn't be dressed up by the LLM — surface it directly
    # and plainly rather than risking a generated response that papers
    # over the error.
    if last_tool_result and last_tool_result.get("status") == "error":
        return last_tool_result.get("message", "I ran into an issue completing that — let me connect you with support.")

    generated = generate_reply(
        user_message=user_message,
        tool_result=last_tool_result,
        retrieved_docs=retrieved_docs,
        # Exclude the last entry — that's the current turn's own message,
        # already included as user_message above; everything before it is
        # genuine prior-turn history.
        conversation_history=(result.get("messages") or [])[:-1],
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


# Below this similarity, a retrieved chunk is noise, not a real citation
# — e.g. for the $10k+ wire fee question, top-3 retrieval also pulls in
# "Domestic Wire Transfers" (0.785) and "Daily Transfer Limits" (0.66),
# which are topically adjacent but not what actually answered the
# question. Only the top match (0.86) should be cited.
CITATION_MIN_SIMILARITY = 0.75


def _build_citations(result: Dict[str, Any]) -> List[Dict[str, str]]:
    retrieved_docs = result.get("retrieved_docs", []) or []
    return [
        {"source": d["source"], "section": d["section"]}
        for d in retrieved_docs
        if isinstance(d, dict)
        and "source" in d
        and "section" in d
        and d.get("similarity", 0) >= CITATION_MIN_SIMILARITY
    ]


def _build_quick_actions(decision: Optional[str]) -> List[str]:
    if decision == "clarify":
        return ["Yes, that's right", "No, something else"]
    return []


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.post("/auth/signup", response_model=AuthResponse)
def signup(req: SignupRequest) -> AuthResponse:
    try:
        result = auth.signup(req.email, req.password)
    except auth.AuthError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return AuthResponse(**result, session_id=_session_id_for_user(result["user_id"]))


@app.post("/auth/login", response_model=AuthResponse)
def login(req: LoginRequest) -> AuthResponse:
    try:
        result = auth.login(req.email, req.password)
    except auth.AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    return AuthResponse(**result, session_id=_session_id_for_user(result["user_id"]))


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, user: Dict[str, Any] = Depends(require_user)) -> ChatResponse:
    # user_id and session_id are derived from the authenticated token,
    # NOT trusted from the request body — otherwise any logged-in
    # customer could read/write another customer's session just by
    # changing the JSON they send. req.session_id / req.user_id are
    # still accepted for backward compatibility with the request shape
    # but intentionally unused for identity.
    user_id = user["user_id"]
    session_id = _session_id_for_user(user_id)

    # Mask card numbers / SSNs / long account-style digit runs before this
    # message ever touches storage, the AI graph, or an agent's screen —
    # applied once, here, so every downstream consumer (session_store,
    # LangGraph, generate_reply's conversation history, the agent
    # dashboard's thread view) only ever sees the redacted version. None
    # of the tool functions need a customer-typed full number anyway —
    # lock_card only ever needs last4, which stays visible.
    message = redact_sensitive(req.message)

    session_store.purge_expired_messages()  # opportunistic 72h retention housekeeping

    session = session_store.get_or_create_session(session_id, user_id)
    session_store.append_message(session_id, "user", message)

    if session["conversation_mode"] == "human":
        # A live agent owns this session — don't invoke the AI graph at
        # all (§4.5). The message is logged; Tab 2 will show it via polling.
        return ChatResponse(
            session_id=session_id,
            reply="",
            conversation_mode="human",
            handover_triggered=False,
        )

    config = {"configurable": {"thread_id": session_id}}

    # CRITICAL: only build a brand-new AgentState on this thread's first
    # turn. On every later turn, pass just the new message and let
    # MemorySaver's checkpoint supply everything else (topic_stack,
    # out_of_scope_attempts, unclear_attempts, active_intent, ...).
    #
    # Bug history: this used to call new_state(...) on every single
    # request, which — since AgentState is a plain TypedDict with no
    # custom reducers except `messages` — overwrote every channel back
    # to its default each turn. Topic-stack resume and out-of-scope
    # escalation counting were both silently broken as a result (each
    # appeared to "work" in early manual tests purely by accident, e.g.
    # a resume phrase that happened to also contain a keyword like
    # "balance"). Caught by a deliberately keyword-free resume-phrase
    # test — see the graph.py smoke test's sibling check in scripts/.
    if not compiled_graph.get_state(config).values:
        graph_state = new_state(session_id=session_id, user_id=user_id)
        graph_state["messages"] = [HumanMessage(content=message)]
    else:
        graph_state = {"messages": [HumanMessage(content=message)]}

    result = compiled_graph.invoke(graph_state, config=config)

    reply_text = _draft_reply(result, message)
    handover_triggered = bool(result.get("is_handover_active"))

    # Bug fix: handover can trigger two ways — an explicit HANDOVER intent
    # (keyword like "fraud"), which _draft_reply already handles, OR a
    # low-confidence RAG_QUERY turn (weak retrieval similarity on an
    # off-topic/unmatched question) that _draft_reply has no special case
    # for. In that second case it was generating a normal, coherent-
    # sounding Ollama answer while the session silently flipped to human
    # mode underneath it — customer sees "You're welcome!" one second and
    # "connected to a live agent" the next, with no acknowledgment either
    # message was a handoff. Always show the handoff message when
    # handover actually triggered, regardless of which path caused it.
    if handover_triggered and result.get("active_intent") != "HANDOVER":
        reply_text = "I'm not fully confident in my answer here — connecting you with a human support specialist who can help."

    if handover_triggered:
        trigger_reason = result.get("handover_reason") or "explicit_request"
        session_store.create_ticket(
            session_id, trigger_reason, result.get("handover_summary") or {}
        )
        session_store.set_conversation_mode(session_id, "human")

    session_store.append_message(session_id, "assistant", reply_text)

    confidence = None
    if result.get("confidence_breakdown"):
        confidence = {
            **result["confidence_breakdown"],
            "composite": result.get("confidence_score"),
            "decision": result.get("decision"),
        }

    return ChatResponse(
        session_id=session_id,
        reply=reply_text,
        conversation_mode="human" if handover_triggered else "ai",
        confidence=confidence,
        citations=_build_citations(result),
        quick_actions=_build_quick_actions(result.get("decision")),
        handover_triggered=handover_triggered,
    )


@app.post("/chat/reset")
def chat_reset(user: Dict[str, Any] = Depends(require_user)) -> Dict[str, str]:
    """
    The real "start new conversation" — unlike the frontend's old Reset
    button (which only cleared what was on screen), this actually moves
    the conversation_started_at boundary forward server-side, so old
    messages stop being shown by default (they're not deleted — still
    subject to the normal 72h retention window on their own timeline),
    forces conversation_mode back to 'ai', and auto-resolves any open
    ticket the customer was mid-handover on rather than leaving it
    stranded in the agent queue.

    Also resets LangGraph's own checkpointed reasoning state for this
    thread — same idea as agent_resolve()'s fix, but more complete,
    since a customer-initiated fresh start should clear everything
    (topic_stack, out-of-scope/unclear attempt counters, retrieved_docs)
    rather than just the handover flags.
    """
    session_id = _session_id_for_user(user["user_id"])
    session_store.reset_conversation(session_id)

    try:
        config = {"configurable": {"thread_id": session_id}}
        compiled_graph.update_state(
            config,
            {
                "is_handover_active": False,
                "unclear_attempts": 0,
                "out_of_scope_attempts": 0,
                "handover_reason": None,
                "handover_summary": None,
                "topic_stack": [],
                "active_intent": "",
                "retrieved_docs": [],
            },
        )
    except Exception as exc:
        print(f"[chat_reset] Failed to reset graph state for {session_id}: {exc}")

    return {"status": "reset", "conversation_mode": "ai"}


@app.get("/chat/{session_id}/status")
def chat_status(session_id: str, since: Optional[str] = None, user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
    # Same identity rule as /chat: derive the real session_id from the
    # authenticated token rather than trusting the URL's session_id —
    # otherwise any logged-in customer could poll another customer's
    # chat just by guessing/changing the path parameter.
    real_session_id = _session_id_for_user(user["user_id"])
    mode = session_store.get_conversation_mode(real_session_id)
    new_messages = session_store.get_messages_since(real_session_id, since_timestamp=since)
    return {
        "conversation_mode": mode,
        "new_messages": [
            {"message_id": m["message_id"], "role": m["role"], "text": m["text"], "timestamp": m["timestamp"]}
            for m in new_messages
        ],
    }


@app.get("/agent/session/{session_id}/messages")
def agent_session_messages(session_id: str, since: Optional[str] = None) -> Dict[str, Any]:
    """
    Agent-facing equivalent of /chat/{session_id}/status — deliberately
    separate rather than reused, because that customer route requires a
    customer's login token and derives session_id from THEIR identity
    (correct for self-service polling, see require_user()). The agent
    dashboard has no login of its own and legitimately needs to view any
    customer's session by session_id directly — reusing the customer
    route meant every poll silently 401'd and tickets showed "No
    messages yet." forever, with no visible error. Unauthenticated like
    the rest of /agent/*, consistent with this whole namespace's existing
    internal-tool trust model (agent_queue, agent_reply, agent_resolve).
    """
    mode = session_store.get_conversation_mode(session_id)
    new_messages = session_store.get_messages_since(session_id, since_timestamp=since)
    return {
        "conversation_mode": mode,
        "new_messages": [
            {"message_id": m["message_id"], "role": m["role"], "text": m["text"], "timestamp": m["timestamp"]}
            for m in new_messages
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
        session_id = session_store.resolve_ticket(ticket_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    # Bug fix: resolve_ticket() only clears the SQL-level conversation_mode.
    # handover_node separately sets is_handover_active=True as a field in
    # the LangGraph checkpoint for this thread_id — and since no node ever
    # resets it back to False, that flag persisted forever across every
    # future turn once a session had been handed over even once. Every
    # message after resolve was reading that stale True and immediately
    # re-triggering handover, regardless of the new message's actual
    # content or confidence. update_state patches just these fields
    # without touching message history or topic_stack.
    try:
        config = {"configurable": {"thread_id": session_id}}
        compiled_graph.update_state(
            config,
            {
                "is_handover_active": False,
                "unclear_attempts": 0,
                "handover_reason": None,
                "handover_summary": None,
            },
        )
    except Exception as exc:
        print(f"[agent_resolve] Failed to reset graph state for {session_id}: {exc}")

    return {"status": "resolved", "conversation_mode": "ai"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.main:app", host=API_HOST, port=API_PORT, reload=True)