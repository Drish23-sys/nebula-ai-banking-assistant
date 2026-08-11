"""
Streamlit frontend — banking assistant.

Tab 1: AI chat interface (this file)
Tab 2: Agent ticket queue (added separately, see agent_queue_tab.py content
       merged in below once built)

Point BASE_URL at the mock server while developing, swap to the real
backend on Day 5 integration checkpoint. Nothing else in this file should
need to change if the mock server matches the contract exactly.
"""

import streamlit as st
import requests
import uuid

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_URL = "http://localhost:8001"  # mock server. Swap to http://localhost:8000 on Day 5.

DECISION_COLORS = {
    "auto_respond": "🟢",
    "clarify": "🟡",
    "handover": "🔴",
}

st.set_page_config(page_title="Banking Assistant", layout="wide")

# ---------------------------------------------------------------------------
# Session state init
# ---------------------------------------------------------------------------

if "session_id" not in st.session_state:
    st.session_state.session_id = f"sess_{uuid.uuid4().hex[:8]}"
if "user_id" not in st.session_state:
    st.session_state.user_id = "USR-4401"  # demo user; swap for real auth later
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []  # list of dicts: {role, text, confidence?, citations?, quick_actions?}
if "conversation_mode" not in st.session_state:
    st.session_state.conversation_mode = "ai"

tab1, tab2 = st.tabs(["💬 Chat", "🎧 Agent Queue"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def send_message(message: str):
    """POST /chat and append the result to chat_history."""
    st.session_state.chat_history.append({"role": "user", "text": message})
    try:
        resp = requests.post(
            f"{BASE_URL}/chat",
            json={
                "session_id": st.session_state.session_id,
                "user_id": st.session_state.user_id,
                "message": message,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        st.session_state.chat_history.append({
            "role": "assistant",
            "text": f"⚠️ Couldn't reach the backend: {e}",
        })
        return

    st.session_state.conversation_mode = data.get("conversation_mode", "ai")

    # Guard: reply can be empty (e.g. mock server's "already handed to human" case)
    if data.get("reply"):
        st.session_state.chat_history.append({
            "role": "assistant",
            "text": data["reply"],
            "confidence": data.get("confidence"),
            "citations": data.get("citations", []),
            "quick_actions": data.get("quick_actions", []),
        })


def poll_status():
    """GET /chat/{session_id}/status — called while conversation_mode == 'human'."""
    try:
        resp = requests.get(f"{BASE_URL}/chat/{st.session_state.session_id}/status", timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException:
        return

    st.session_state.conversation_mode = data.get("conversation_mode", "ai")
    for msg in data.get("new_messages", []):
        st.session_state.chat_history.append({
            "role": "assistant",  # agent replies render the same as assistant bubbles
            "text": msg["text"],
            "is_agent": True,
        })


# ---------------------------------------------------------------------------
# Tab 1: Chat
# ---------------------------------------------------------------------------

with tab1:
    mode = st.session_state.conversation_mode

    if mode == "human":
        st.info("🔴 You're connected to a live agent. This chat is being handled by a human.")
        # Poll every 3s while in human mode, per the contract's polling section.
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=3000, key="status_poll")
        poll_status()
    else:
        st.success("🟢 Chatting with AI Assistant")

    # Render history
    for msg in st.session_state.chat_history:
        with st.chat_message("user" if msg["role"] == "user" else "assistant"):
            if msg.get("is_agent"):
                st.caption("Live agent")
            st.write(msg["text"])

            confidence = msg.get("confidence")
            if confidence:
                decision = confidence.get("decision", "auto_respond")
                badge = DECISION_COLORS.get(decision, "⚪")
                st.caption(
                    f"{badge} **{decision}** — "
                    f"composite: {confidence.get('composite', 0):.2f} "
                    f"(retrieval {confidence.get('retrieval', 0):.2f}, "
                    f"grounding {confidence.get('grounding', 0):.2f}, "
                    f"intent {confidence.get('intent', 0):.2f})"
                )

            # Guard: citations can be an empty list — render nothing if so.
            citations = msg.get("citations") or []
            if citations:
                with st.expander(f"📎 {len(citations)} source(s)"):
                    for c in citations:
                        st.write(f"- **{c['source']}** — {c['section']}")

            # Guard: quick_actions can be an empty list — render nothing if so.
            quick_actions = msg.get("quick_actions") or []
            if quick_actions:
                cols = st.columns(len(quick_actions))
                for i, action in enumerate(quick_actions):
                    if cols[i].button(action, key=f"qa_{len(st.session_state.chat_history)}_{i}"):
                        send_message(action)
                        st.rerun()

    # Chat input (disabled while connected to a human — the human is typing, not the AI)
    if mode != "human":
        user_input = st.chat_input("Type a message...")
        if user_input:
            send_message(user_input)
            st.rerun()
    else:
        st.chat_input("An agent is handling this conversation...", disabled=True)

    with st.sidebar:
        st.caption(f"Session: `{st.session_state.session_id}`")
        st.caption(f"User: `{st.session_state.user_id}`")
        if st.button("Reset conversation"):
            st.session_state.session_id = f"sess_{uuid.uuid4().hex[:8]}"
            st.session_state.chat_history = []
            st.session_state.conversation_mode = "ai"
            st.rerun()

# ---------------------------------------------------------------------------
# Tab 2: Agent Queue
# ---------------------------------------------------------------------------

TRIGGER_LABELS = {
    "fraud_flag": "🚨 Fraud flag",
    "low_confidence_repeated": "🟡 Low confidence (repeated)",
    "explicit_request": "🙋 Explicit request",
    "out_of_scope": "❓ Out of scope",
}

with tab2:
    st.subheader("Tickets waiting for a human")

    if st.button("🔄 Refresh queue"):
        st.rerun()

    try:
        resp = requests.get(f"{BASE_URL}/agent/queue", timeout=10)
        resp.raise_for_status()
        tickets = resp.json().get("tickets", [])
    except requests.RequestException as e:
        st.error(f"Couldn't reach the backend: {e}")
        tickets = []

    if not tickets:
        st.info("No tickets waiting. 🎉")

    for ticket in tickets:
        trigger = ticket.get("trigger_reason", "")
        label = TRIGGER_LABELS.get(trigger, trigger)

        with st.container(border=True):
            col1, col2 = st.columns([3, 1])
            with col1:
                st.markdown(f"**{ticket.get('user_name', 'Unknown')}** — `{ticket.get('user_id', '')}`")
                st.caption(f"Ticket `{ticket['ticket_id']}` · Session `{ticket['session_id']}` · {ticket.get('created_at', '')}")
            with col2:
                st.markdown(label)

            summary = ticket.get("summary", {})
            st.markdown(f"**Issue:** {summary.get('issue', '—')}")
            st.markdown(f"**Context:** {summary.get('context', '—')}")
            st.markdown(f"**Attempted resolution:** {summary.get('attempted_resolution', '—')}")
            st.markdown(f"**Suggested next step:** {summary.get('suggested_next_step', '—')}")

            reply_key = f"reply_{ticket['ticket_id']}"
            reply_text = st.text_area("Reply to customer", key=reply_key, height=80)

            btn_col1, btn_col2 = st.columns(2)
            with btn_col1:
                if st.button("Send reply", key=f"send_{ticket['ticket_id']}"):
                    if reply_text.strip():
                        try:
                            r = requests.post(
                                f"{BASE_URL}/agent/{ticket['ticket_id']}/reply",
                                json={"message": reply_text},
                                timeout=10,
                            )
                            r.raise_for_status()
                            st.success("Reply sent.")
                        except requests.RequestException as e:
                            st.error(f"Failed to send reply: {e}")
                    else:
                        st.warning("Reply can't be empty.")
            with btn_col2:
                if st.button("✅ Resolve (hand back to AI)", key=f"resolve_{ticket['ticket_id']}"):
                    try:
                        r = requests.post(f"{BASE_URL}/agent/{ticket['ticket_id']}/resolve", timeout=10)
                        r.raise_for_status()
                        st.success("Resolved — session handed back to AI.")
                        st.rerun()
                    except requests.RequestException as e:
                        st.error(f"Failed to resolve: {e}")
