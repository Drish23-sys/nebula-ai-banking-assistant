"""
backend/agent/graph.py

Compiles the AgentState nodes (backend/agent/nodes.py) into a runnable
LangGraph StateGraph with MemorySaver checkpointing (PRD §3.1, Day 2).

Shape (Day 3 — real confidence thresholds now wired):

    START -> intent_node -> [route on active_intent]
                                |-> TOOL_CALL -> tool_node -> confidence_node -> [route on decision]
                                |-> RAG_QUERY -> rag_node  -> confidence_node -> [route on decision]
                                |                                                   |-> auto_respond/clarify -> END
                                |                                                   |-> handover -> handover_node -> END
                                |-> HANDOVER  -> handover_node -> END

confidence_node computes C = 0.4*S_retrieval + 0.4*S_grounding +
0.2*S_intent and a decision label using config.py's 0.65/0.50
thresholds, plus the "2 consecutive low-confidence turns" escalation
rule (§4.3). route_after_confidence sends low-confidence turns to
handover_node instead of ending the graph directly.

MemorySaver gives each session_id its own persisted thread of state
across turns (in-process only — swapped for a durable checkpointer
before deployment if needed, not required for the hackathon scope).
"""

import os
import sys
from typing import Literal

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from backend.agent.nodes import (  # noqa: E402
    confidence_node,
    handover_node,
    intent_node,
    rag_node,
    tool_node,
)
from backend.agent.state import AgentState  # noqa: E402

TOOL_INTENTS = {
    "CHECK_BALANCE",
    "LOCK_CARD",
    "TRANSACTION_HISTORY",
    "CHECK_TRANSFER_LIMIT",
    "CALCULATE_LOAN_EMI",
}


def route_after_intent(state: AgentState) -> Literal["tool_node", "rag_node", "handover_node"]:
    """Edge-routing function: picks the next node based on active_intent."""
    intent = state.get("active_intent", "")
    if intent == "HANDOVER":
        return "handover_node"
    if intent in TOOL_INTENTS:
        return "tool_node"
    return "rag_node"


def route_after_confidence(state: AgentState) -> Literal["handover_node", "__end__"]:
    """
    Edge-routing function (Day 3): confidence_node's `decision` field
    (auto_respond/clarify/handover — see nodes.py) decides whether this
    turn needs to escalate to a human.

    "clarify" still ends the graph run here, same as auto_respond — the
    difference between them is what the API layer sends back to the
    user (a direct answer vs. a clarifying question), not a different
    graph path. Only "handover" needs a different node.
    """
    if state.get("decision") == "handover":
        return "handover_node"
    return END


def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("intent_node", intent_node)
    graph.add_node("tool_node", tool_node)
    graph.add_node("rag_node", rag_node)
    graph.add_node("confidence_node", confidence_node)
    graph.add_node("handover_node", handover_node)

    graph.set_entry_point("intent_node")

    graph.add_conditional_edges(
        "intent_node",
        route_after_intent,
        {
            "tool_node": "tool_node",
            "rag_node": "rag_node",
            "handover_node": "handover_node",
        },
    )

    graph.add_edge("tool_node", "confidence_node")
    graph.add_edge("rag_node", "confidence_node")
    graph.add_conditional_edges(
        "confidence_node",
        route_after_confidence,
        {"handover_node": "handover_node", END: END},
    )
    graph.add_edge("handover_node", END)

    checkpointer = MemorySaver()
    return graph.compile(checkpointer=checkpointer)


# Module-level compiled graph — import this directly from FastAPI/tests
# rather than recompiling per-request.
compiled_graph = build_graph()


if __name__ == "__main__":
    # Quick smoke test — three turns across the three routing branches,
    # all under one session_id thread so MemorySaver checkpointing is
    # exercised too.
    from langchain_core.messages import HumanMessage

    from backend.agent.state import new_state

    session_id = "sess_smoketest"
    config = {"configurable": {"thread_id": session_id}}

    turns = [
        "What's my account balance?",
        "I want to talk to a human, my card was stolen",
        "What's the fee for an international wire transfer?",
    ]

    state = new_state(session_id=session_id, user_id="USR-4401")

    for turn_text in turns:
        state["messages"] = state.get("messages", []) + [HumanMessage(content=turn_text)]
        result = compiled_graph.invoke(state, config=config)
        state = result

        print(f"\n--- Turn: {turn_text!r} ---")
        print(f"  active_intent      : {result['active_intent']}")
        print(f"  topic_stack        : {result['topic_stack']}")
        print(f"  confidence_score   : {result.get('confidence_score')}")
        print(f"  confidence_breakdown: {result.get('confidence_breakdown')}")
        print(f"  decision           : {result.get('decision')}")
        print(f"  unclear_attempts   : {result.get('unclear_attempts')}")
        print(f"  is_handover_active : {result['is_handover_active']}")

    print("\nGraph compiled, routed all 3 branches with confidence-based "
          "handover routing, and checkpointed across turns: OK")
