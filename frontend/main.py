"""
Mock backend server — matches the frozen API contract in FRONTEND_HANDOFF.md exactly.

Purpose: let frontend development proceed without the real backend running.
On Day 5, point the Streamlit app at the real backend URL instead of this one —
if the frontend was built strictly against this contract, nothing else changes.

Run with:
    uvicorn main:app --reload --port 8001

(Using 8001 here so it doesn't collide with the real backend's 8000 if you
ever run both side by side for comparison.)
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timezone
import uuid

app = FastAPI(title="Mock Banking Assistant Backend")

# ---------------------------------------------------------------------------
# In-memory state (resets on server restart — fine for mock purposes)
# ---------------------------------------------------------------------------

# session_id -> conversation_mode ("ai" | "human")
SESSION_MODE: dict[str, str] = {}

# session_id -> list of {"role": ..., "text": ..., "timestamp": ...}
# Used to fake new messages appearing when polling /chat/{session_id}/status
PENDING_AGENT_MESSAGES: dict[str, list[dict]] = {}

# Canned ticket queue for /agent/queue
MOCK_TICKETS = [
    {
        "ticket_id": "tkt_001",
        "session_id": "sess_abc123",
        "user_id": "USR-4401",
        "user_name": "John Doe",
        "trigger_reason": "fraud_flag",
        "created_at": "2026-08-03T10:15:00Z",
        "summary": {
            "issue": "Customer disputes a $1,800 charge from an unrecognized merchant",
            "context": "Checking account CHK-4821, transaction TXN-1003, flagged 2 hours ago",
            "attempted_resolution": "AI confirmed the flag and offered to lock the card, but dispute filing requires human authorization",
            "suggested_next_step": "Verify identity, confirm dispute details, file fraud claim",
        },
    },
    {
        "ticket_id": "tkt_002",
        "session_id": "sess_def456",
        "user_id": "USR-7788",
        "user_name": "Priya Nair",
        "trigger_reason": "low_confidence_repeated",
        "created_at": "2026-08-03T11:02:00Z",
        "summary": {
            "issue": "Customer asking about a fee they don't recognize on a joint account statement",
            "context": "Joint account JNT-2210, statement period July 2026, fee code unclear from KB",
            "attempted_resolution": "AI attempted to answer twice with low confidence (composite < 0.5) and could not ground the fee code in retrieved docs",
            "suggested_next_step": "Look up fee code directly in core banking system, explain to customer",
        },
    },
    {
        "ticket_id": "tkt_003",
        "session_id": "sess_ghi789",
        "user_id": "USR-1123",
        "user_name": "Marcus Chen",
        "trigger_reason": "explicit_request",
        "created_at": "2026-08-03T11:40:00Z",
        "summary": {
            "issue": "Customer explicitly asked to speak with a human agent about a loan modification",
            "context": "Personal loan LN-5567, customer mentioned financial hardship",
            "attempted_resolution": "AI offered general loan modification info but customer requested a human",
            "suggested_next_step": "Discuss hardship options, may need to loop in loan servicing team",
        },
    },
]

TICKET_STATUS: dict[str, str] = {t["ticket_id"]: "open" for t in MOCK_TICKETS}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Request/response models (mirroring the contract's JSON shapes)
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    session_id: str
    user_id: str
    message: str


class Confidence(BaseModel):
    retrieval: float
    grounding: float
    intent: float
    composite: float
    decision: str  # "auto_respond" | "clarify" | "handover"


class Citation(BaseModel):
    source: str
    section: str


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    conversation_mode: str  # "ai" | "human"
    confidence: Confidence
    citations: List[Citation]
    quick_actions: List[str]
    handover_triggered: bool


class AgentReplyRequest(BaseModel):
    message: str


class AgentReplyResponse(BaseModel):
    status: str


class AgentResolveResponse(BaseModel):
    status: str
    conversation_mode: str


class StatusMessage(BaseModel):
    role: str
    text: str
    timestamp: str


class StatusResponse(BaseModel):
    conversation_mode: str
    new_messages: List[StatusMessage]


# ---------------------------------------------------------------------------
# POST /chat
# ---------------------------------------------------------------------------
# Light scripted logic so the frontend has varied, realistic scenarios to
# render against instead of one static response every time:
#   - message mentions "human" / "agent" / "speak to someone" -> handover
#   - message mentions "not sure" / "confused" -> clarify
#   - message mentions "wire" / "transfer" -> the exact example from the doc
#   - everything else -> generic auto_respond

@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    text = req.message.lower()

    # If this session has already been handed to a human, stay in human mode
    # and don't produce a new AI reply (the frontend should be polling
    # /chat/{session_id}/status for agent replies instead).
    if SESSION_MODE.get(req.session_id) == "human":
        return ChatResponse(
            session_id=req.session_id,
            reply="",
            conversation_mode="human",
            confidence=Confidence(retrieval=0, grounding=0, intent=0, composite=0, decision="handover"),
            citations=[],
            quick_actions=[],
            handover_triggered=False,
        )

    if any(kw in text for kw in ["human", "agent", "speak to someone", "real person"]):
        SESSION_MODE[req.session_id] = "human"
        ticket_id = f"tkt_{uuid.uuid4().hex[:6]}"
        MOCK_TICKETS.append({
            "ticket_id": ticket_id,
            "session_id": req.session_id,
            "user_id": req.user_id,
            "user_name": "Demo User",
            "trigger_reason": "explicit_request",
            "created_at": _now_iso(),
            "summary": {
                "issue": "Customer explicitly requested a human agent",
                "context": f"Last message: \"{req.message}\"",
                "attempted_resolution": "None — routed immediately on request",
                "suggested_next_step": "Greet customer, ask how you can help",
            },
        })
        TICKET_STATUS[ticket_id] = "open"
        return ChatResponse(
            session_id=req.session_id,
            reply="I'll connect you with a human agent now. Someone will be with you shortly.",
            conversation_mode="human",
            confidence=Confidence(retrieval=0.0, grounding=0.0, intent=0.95, composite=0.38, decision="handover"),
            citations=[],
            quick_actions=[],
            handover_triggered=True,
        )

    if any(kw in text for kw in ["not sure", "confused", "don't understand"]):
        return ChatResponse(
            session_id=req.session_id,
            reply="I want to make sure I get this right — could you tell me a bit more about what you're trying to do?",
            conversation_mode="ai",
            confidence=Confidence(retrieval=0.55, grounding=0.48, intent=0.60, composite=0.54, decision="clarify"),
            citations=[],
            quick_actions=["Check balance", "Transfer funds", "Dispute a charge"],
            handover_triggered=False,
        )

    if "wire" in text or "transfer" in text:
        return ChatResponse(
            session_id=req.session_id,
            reply=(
                "For international wires over $10,000, there's a $45 flat fee "
                "plus a 0.2% exchange margin. Want me to proceed with the transfer?"
            ),
            conversation_mode="ai",
            confidence=Confidence(retrieval=0.81, grounding=0.77, intent=0.90, composite=0.82, decision="auto_respond"),
            citations=[
                Citation(source="wire_transfers.md", section="Section 4.2 — International / Outbound Wire Transfers"),
            ],
            quick_actions=["Confirm transfer", "Cancel"],
            handover_triggered=False,
        )

    # Generic fallback
    return ChatResponse(
        session_id=req.session_id,
        reply=f"Got it — you said: \"{req.message}\". Here's a mock response for that (swap in the real backend for grounded answers).",
        conversation_mode="ai",
        confidence=Confidence(retrieval=0.70, grounding=0.68, intent=0.75, composite=0.70, decision="auto_respond"),
        citations=[Citation(source="general_faq.md", section="Section 1.1 — Overview")],
        quick_actions=[],
        handover_triggered=False,
    )


# ---------------------------------------------------------------------------
# GET /agent/queue
# ---------------------------------------------------------------------------

@app.get("/agent/queue")
def get_queue():
    open_tickets = [t for t in MOCK_TICKETS if TICKET_STATUS.get(t["ticket_id"]) == "open"]
    return {"tickets": open_tickets}


# ---------------------------------------------------------------------------
# POST /agent/{ticket_id}/reply
# ---------------------------------------------------------------------------

@app.post("/agent/{ticket_id}/reply", response_model=AgentReplyResponse)
def agent_reply(ticket_id: str, req: AgentReplyRequest):
    ticket = next((t for t in MOCK_TICKETS if t["ticket_id"] == ticket_id), None)
    if ticket is None:
        raise HTTPException(status_code=404, detail="ticket not found")

    session_id = ticket["session_id"]
    PENDING_AGENT_MESSAGES.setdefault(session_id, []).append({
        "role": "agent",
        "text": req.message,
        "timestamp": _now_iso(),
    })
    return AgentReplyResponse(status="sent")


# ---------------------------------------------------------------------------
# POST /agent/{ticket_id}/resolve
# ---------------------------------------------------------------------------

@app.post("/agent/{ticket_id}/resolve", response_model=AgentResolveResponse)
def agent_resolve(ticket_id: str):
    ticket = next((t for t in MOCK_TICKETS if t["ticket_id"] == ticket_id), None)
    if ticket is None:
        raise HTTPException(status_code=404, detail="ticket not found")

    TICKET_STATUS[ticket_id] = "resolved"
    SESSION_MODE[ticket["session_id"]] = "ai"
    return AgentResolveResponse(status="resolved", conversation_mode="ai")


# ---------------------------------------------------------------------------
# GET /chat/{session_id}/status  (polled every 3s while conversation_mode == "human")
# ---------------------------------------------------------------------------

@app.get("/chat/{session_id}/status", response_model=StatusResponse)
def chat_status(session_id: str):
    mode = SESSION_MODE.get(session_id, "ai")
    new_messages = PENDING_AGENT_MESSAGES.pop(session_id, [])
    return StatusResponse(
        conversation_mode=mode,
        new_messages=[StatusMessage(**m) for m in new_messages],
    )


# ---------------------------------------------------------------------------
# Convenience: reset all in-memory state (not part of the real contract —
# purely a mock-server dev helper)
# ---------------------------------------------------------------------------

@app.post("/_mock/reset")
def reset_state():
    SESSION_MODE.clear()
    PENDING_AGENT_MESSAGES.clear()
    for t in TICKET_STATUS:
        TICKET_STATUS[t] = "open"
    return {"status": "reset"}
