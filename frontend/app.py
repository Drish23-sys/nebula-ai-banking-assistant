"""
frontend/app.py

Minimal Tab 1 (AI chat) skeleton, wired to the real POST /chat endpoint.
This is a STARTING POINT for Member B (RAG/Frontend track), not the
finished UI — see docs/FRONTEND_HANDOFF.md for the full day-by-day plan
(confidence badges, citations display, Tab 2 agent queue, polling sync,
etc.), all still to be built.

Run with:
    streamlit run frontend/app.py
(with backend/main.py already running via `uvicorn backend.main:app`)
"""

import uuid

import requests
import streamlit as st

API_BASE = "http://localhost:8000"

st.set_page_config(page_title="Nebula AI Banking Assistant", page_icon="🏦")
st.title("🏦 Nebula AI Banking Assistant")

# TODO(Member B): st.tabs(["Chat", "Agent Console"]) — this file only
# builds Tab 1. Tab 2 (agent_queue / reply / resolve) isn't started yet.

if "session_id" not in st.session_state:
    st.session_state.session_id = f"sess_{uuid.uuid4().hex[:8]}"
if "user_id" not in st.session_state:
    st.session_state.user_id = "USR-4401"  # hardcoded demo user for now
if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.write(msg["text"])
        # TODO(Member B): render confidence badge (msg.get("confidence")),
        # citations list, and quick_action buttons here per
        # docs/FRONTEND_HANDOFF.md §1.

user_input = st.chat_input("Ask about your account, cards, or banking policies...")

if user_input:
    st.session_state.messages.append({"role": "user", "text": user_input})
    with st.chat_message("user"):
        st.write(user_input)

    try:
        resp = requests.post(
            f"{API_BASE}/chat",
            json={
                "session_id": st.session_state.session_id,
                "user_id": st.session_state.user_id,
                "message": user_input,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        data = {"reply": f"⚠️ Couldn't reach the backend — is `uvicorn backend.main:app` running? ({exc})"}

    reply_text = data.get("reply", "")
    st.session_state.messages.append({"role": "assistant", "text": reply_text})
    with st.chat_message("assistant"):
        st.write(reply_text)
        if data.get("handover_triggered"):
            st.info("You've been connected with a human support specialist.")
