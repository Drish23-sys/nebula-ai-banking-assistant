# Frontend/RAG Handoff — Integration Contract

**Owner of this track:** Member B (RAG + Frontend)
**Goal:** you should be able to build 100% of your part *without the backend
running*, using the mock responses below, and it should plug in on Day 5
with zero surprises.

**Golden rule:** don't invent your own field names, endpoint paths, or
config values. Everything you need is frozen below. If something's
missing, flag it — don't guess, because a guessed field name is exactly
what causes integration breakage.

---

## 0. Repo ground rules

- Clone the shared repo, work on branch `feature/rag-frontend`.
- **Never edit `backend/config.py`, `backend/agent/*`, or `backend/sandbox/tools.py`** — those are Member A's. If you need a new config value, ask, don't add it yourself (duplicate/conflicting config is the #1 cause of "works on my machine").
- **Your files:** `frontend/*` (a minimal skeleton exists — see below), plus your own tests/scenario scripts.
- **Not your files anymore — already built and tested by the backend track, don't rebuild:**
  - `backend/config.py`
  - `backend/sandbox/database.py`
  - `backend/sandbox/tools.py` — all 5 tool functions
  - `backend/rag/ingest.py`
  - `data/knowledge_base/*.md`
  - `backend/agent/state.py` / `nodes.py` / `graph.py` — the full LangGraph agent
  - `backend/session_store.py` — session/message/ticket persistence
  - **`backend/main.py`** — the real FastAPI app, implementing every
    endpoint in this doc exactly. **You no longer need a mock server —
    the real backend already exists and is running-tested.** Skip
    straight to running it for real (see §3 below, updated).
  - `frontend/app.py` — a minimal *working* Tab-1 chat skeleton already
    wired to the real `/chat` endpoint. Not the finished UI — build on
    top of it, don't start from scratch.

If this differs from what you were told earlier in the project, this
version is current — the backend track ended up building further ahead
than originally planned. Your actual remaining scope is narrower than
the original day-by-day below suggested: it's now UI polish + Tab 2 +
testing, not infrastructure.

---

## 1. The API contract (frozen — build against this)

Base URL: `http://localhost:8000` (from `API_HOST`/`API_PORT` in config.py)

### `POST /chat`

**Request:**
```json
{
  "session_id": "sess_abc123",
  "user_id": "USR-4401",
  "message": "I want to send $12,000 to my brother in the UK"
}
```

**Response:**
```json
{
  "session_id": "sess_abc123",
  "reply": "For international wires over $10,000, there's a $45 flat fee plus a 0.2% exchange margin. Want me to proceed with the transfer?",
  "conversation_mode": "ai",
  "confidence": {
    "retrieval": 0.81,
    "grounding": 0.77,
    "intent": 0.90,
    "composite": 0.82,
    "decision": "auto_respond"
  },
  "citations": [
    {"source": "wire_transfers.md", "section": "Section 4.2 — International / Outbound Wire Transfers"}
  ],
  "quick_actions": ["Confirm transfer", "Cancel"],
  "handover_triggered": false
}
```

Notes for your UI:
- `conversation_mode` is `"ai"` or `"human"` — this is what decides whether Tab 1 shows the AI chat or a "connected to live agent" state.
- `confidence.decision` is one of `"auto_respond"`, `"clarify"`, `"handover"` — use this to pick the badge color (green / yellow / red).
- `citations` can be an empty list — always guard for that.
- `quick_actions` can be an empty list — render nothing if empty, don't crash.

### `GET /agent/queue`

Returns tickets waiting for a human (for Tab 2).

**Response:**
```json
{
  "tickets": [
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
        "suggested_next_step": "Verify identity, confirm dispute details, file fraud claim"
      }
    }
  ]
}
```

`trigger_reason` is one of: `"fraud_flag"`, `"low_confidence_repeated"`, `"explicit_request"`, `"out_of_scope"`.

### `POST /agent/{ticket_id}/reply`

**Request:**
```json
{ "message": "Hi John, I've filed the dispute for TXN-1003, you'll see provisional credit within 10 business days." }
```
**Response:** `{ "status": "sent" }`

### `POST /agent/{ticket_id}/resolve`

Hands the session back to AI. **Request:** `{}` **Response:** `{ "status": "resolved", "conversation_mode": "ai" }`

### Polling

No websockets — Tab 1 polls `GET /chat/{session_id}/status` every 3s while `conversation_mode == "human"` to check for new agent replies and mode changes. Use `streamlit-autorefresh` (already in `requirements.txt`) for this, don't hand-roll a loop.

**`GET /chat/{session_id}/status` response:**
```json
{
  "conversation_mode": "human",
  "new_messages": [
    {"role": "agent", "text": "Hi John, I've filed the dispute...", "timestamp": "2026-08-03T10:22:00Z"}
  ]
}
```

---

## 2. Schemas you own

### Confidence score breakdown (you compute this in `rag_node`)

| Field | Weight | What it measures |
|---|---|---|
| `retrieval` | 0.4 | top-k cosine similarity from ChromaDB |
| `grounding` | 0.4 | does the generated answer's claims actually appear in retrieved chunks |
| `intent` | 0.2 | how confidently the intent classifier matched a known category |

`composite = 0.4*retrieval + 0.4*grounding + 0.2*intent`
Thresholds (from `config.py` — don't hardcode, import them):
```python
from backend.config import CONFIDENCE_AUTO_EXECUTE_THRESHOLD, CONFIDENCE_CLARIFY_THRESHOLD
# composite >= 0.65            -> "auto_respond"
# 0.50 <= composite < 0.65     -> "clarify"
# composite < 0.50             -> "handover"
```

### `session_store.py` — tables you're building

```
session_state(session_id, user_id, conversation_mode, created_at, updated_at)
messages(message_id, session_id, role, text, timestamp)
agent_queue(ticket_id, session_id, trigger_reason, summary_json, status, created_at)
```
Use the same `get_connection()` context-manager pattern as `backend/sandbox/database.py` — same file, same style, same DB engine (SQLite). Don't introduce a second database technology.

---

## 3. Running against the real backend (no mock server needed)

The backend is already built and tested. Run it for real:

```bash
python backend/sandbox/database.py   # seed demo data (John Doe, USR-4401)
python backend/rag/ingest.py          # needs real internet — downloads bge-small-en-v1.5 once
uvicorn backend.main:app --reload --port 8000
```

Then point `frontend/app.py` (or your own UI) at `http://localhost:8000`
— it's already wired that way in the skeleton. No mock JSON needed;
build directly against live responses.

One honest gap to know about: `/chat` replies are currently
**template-based, not real LLM output** — Ollama isn't wired into the
reply-generation step yet (see `_draft_reply()` in `main.py`). The
`confidence`, `citations`, `decision`, and `handover` fields are all
real and correct; only the natural-language `reply` text itself is a
placeholder for now. Don't be surprised if replies look a bit robotic —
that's expected, not a bug on your end.

---

## 4. Your actual remaining work

Everything infrastructure-related is done (see §0). What's actually
left is UI:

| Day | Tasks |
|---|---|
| **2** | Run `backend/rag/ingest.py` for real on your machine (needs internet — downloads bge-small-en-v1.5 once, ~130MB). Get the full stack running locally (`uvicorn` + `streamlit run frontend/app.py`) and confirm you can chat end-to-end. |
| **3–4** | Build out Tab 1 properly on top of the existing skeleton: confidence badge (color from `confidence.decision`), citations list, quick action buttons, nicer message styling. |
| **5** | **Integration checkpoint with Member A** — walk through the 3 demo scenarios together against the real backend, confirm everything matches. Start Tab 2. |
| **6** | Build Tab 2: ticket queue (`GET /agent/queue`), summary card, reply box (`POST /agent/{id}/reply`), resolve button (`POST /agent/{id}/resolve`). Add polling (`streamlit-autorefresh`) so Tab 1 picks up agent replies via `GET /chat/{id}/status`. |
| **7 AM** | Full 3-scenario test end-to-end, bug bash with Member A. |
| **7 PM** | Demo video, README, `SAMPLE_TRANSCRIPTS.md`. |

---

## 5. If you generate code with Claude on your end

Paste this whole document in as context, plus the actual `config.py` /
`database.py` / `ingest.py` files from the repo (don't let a fresh Claude
session regenerate those from scratch — it'll invent slightly different
field names or paths than what's already built, which is exactly the
integration risk we're avoiding). Tell it explicitly: *"Match this exact
API contract, don't invent field names."*
