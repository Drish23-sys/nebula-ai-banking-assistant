"""
backend/agent/state.py

AgentState — the object that flows through every LangGraph node,
maintaining conversation context and memory (PRD §3.2).

Note: `is_handover_active` here is per-invocation graph state, not the
durable cross-tab routing flag. The durable source of truth for "who's
driving this session" (AI vs. a live human agent) lives in the
`session_state` table's `conversation_mode` column, checked by FastAPI
*before* the graph is invoked at all (§4.5). Don't conflate the two.
"""

from typing import Annotated, Any, Dict, List, Optional

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class AgentState(TypedDict):
    session_id: str
    user_id: str
    user_profile: Dict[str, Any]              # Account balance, card status, verified status
    # `add_messages` (LangGraph's canonical reducer) appends/merges by
    # message id instead of overwriting — required now that main.py only
    # passes the new message per turn and relies on the checkpoint for
    # everything else (see main.py's /chat handler for why).
    messages: Annotated[List[BaseMessage], add_messages]
    active_intent: str                        # e.g. "CHECK_BALANCE", "LOCK_CARD", "RAG_POLICY"
    topic_stack: List[str]                    # Stack of previous intents for topic restoration
    retrieved_docs: List[Dict[str, Any]]      # Vector RAG search results with confidence scores
    tool_calls: List[Dict[str, Any]]          # Pending tool executions
    confidence_score: float                   # Computed confidence score (0.0 to 1.0)
    confidence_breakdown: Dict[str, float]    # {"retrieval": .., "grounding": .., "intent": ..}
    decision: str                             # "auto_respond" | "clarify" | "handover"
    unclear_attempts: int                     # Count of low-confidence turns (triggers handover at 2)
    is_handover_active: bool                  # Flag indicating transfer to human agent
    handover_summary: Optional[Dict[str, Any]]  # Structured 4-part summary for support agent
    handover_reason: Optional[str]            # "fraud_flag"|"low_confidence_repeated"|"explicit_request"|"out_of_scope" — set by whichever node decides to escalate, single source of truth (not re-derived downstream)
    guardrail_blocked: bool                   # This turn was blocked by guardrail_node (out-of-scope or injection attempt)
    guardrail_reason: Optional[str]           # "out_of_scope" | "prompt_injection" | None
    out_of_scope_attempts: int                # Consecutive blocked turns (escalates to handover at the same limit as low-confidence)


def new_state(session_id: str, user_id: str, user_profile: Optional[Dict[str, Any]] = None) -> AgentState:
    """Factory for a fresh AgentState at the start of a session."""
    return AgentState(
        session_id=session_id,
        user_id=user_id,
        user_profile=user_profile or {},
        messages=[],
        active_intent="",
        topic_stack=[],
        retrieved_docs=[],
        tool_calls=[],
        confidence_score=0.0,
        confidence_breakdown={},
        decision="",
        unclear_attempts=0,
        is_handover_active=False,
        handover_summary=None,
        handover_reason=None,
        guardrail_blocked=False,
        guardrail_reason=None,
        out_of_scope_attempts=0,
    )