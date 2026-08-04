# Nebula AI Banking Assistant

Agentic, trustworthy conversational AI for retail banking — RAG policy
grounding, a mock banking execution sandbox, and a stateful LangGraph
workflow with human handover. See `nebula_ai_banking_assistant_prd.docx`
for the full spec, and `docs/FRONTEND_HANDOFF.md` for the frozen API
contract the two build tracks (Agent/Backend vs. RAG/Frontend) integrate
against.

## Status — everything below is built and tested

- [x] Project scaffold
- [x] `backend/config.py` — env config, model names, confidence thresholds
- [x] `backend/sandbox/database.py` + `backend/sandbox/tools.py` — mock
      banking DB (users/accounts/cards/transactions) and the 5 tool
      functions (check balance, lock card, transaction history, transfer
      limit, loan EMI calculator)
- [x] `data/knowledge_base/*.md` + `backend/rag/ingest.py` — 3 sample
      policy FAQs, chunked and ready for ChromaDB ingestion
- [x] `backend/agent/state.py` / `nodes.py` / `graph.py` — `AgentState`,
      all 5 LangGraph nodes, compiled graph with `MemorySaver`, real
      confidence-based routing (auto_respond / clarify / handover)
- [x] `backend/session_store.py` — durable `session_state` /
      `messages` / `agent_queue` tables (the human-handover source of truth)
- [x] `backend/main.py` — FastAPI app implementing the full
      `docs/FRONTEND_HANDOFF.md` contract (`/chat`, `/chat/{id}/status`,
      `/agent/queue`, `/agent/{id}/reply`, `/agent/{id}/resolve`)
- [x] `frontend/app.py` — minimal Tab-1 chat skeleton wired to the real
      backend (starting point only — Member B builds the real UI from here)

**Known gap, by design, not a bug:** `_draft_reply()` in `main.py` and
`handover_node`'s summary in `nodes.py` are deterministic templates, not
real LLM output — Ollama isn't wired up yet. Every endpoint, the full
confidence/routing logic, and the handover flow are fully tested and
working; only the *natural-language generation* step is still a
placeholder. That's next.

**Known environment gap:** this sandbox has no network route to
`huggingface.co`, so `backend/rag/ingest.py` has only been verified in
two halves here (chunking logic + ChromaDB read/write path separately) —
not run fully end-to-end. Run it for real on your own machine first
(`python backend/rag/ingest.py`) before relying on RAG answers; without
that, `rag_node` fails soft and `/chat` falls back to a generic reply.

## Setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in GROQ_API_KEY when handover_node goes live
```

### 1. Seed the mock banking sandbox

```bash
python backend/sandbox/database.py
```

### 2. Ingest the policy knowledge base into ChromaDB

```bash
python backend/rag/ingest.py
```

### 3. Run the backend

```bash
uvicorn backend.main:app --reload --port 8000
```

### 4. Run the frontend (separate terminal)

```bash
streamlit run frontend/app.py
```

## Next up

- Real Ollama-generated replies (swap `_draft_reply()`'s template)
- Real Groq call for the 4-part handover summary (swap `handover_node`'s
  template — field names are already frozen, so this is a body-only change)
- Topic Stack Algorithm restoration (resuming a paused topic after a
  handled interruption) — currently pushes/pops correctly but nothing
  reads the stack back yet
- Tab 2 (agent console) in `frontend/app.py`

