# Nebula — Customer Chat (React)

React rebuild of the Streamlit customer-facing chat, talking to the same
FastAPI backend (`backend/main.py`) with no backend changes required.

## Run locally

```bash
npm install
npm run dev
```

Opens on `http://localhost:5173`. Make sure the backend is running first:

```bash
uvicorn backend.main:app --reload --port 8000
```

By default this app calls `http://localhost:8000`. To point it elsewhere,
copy `.env.example` to `.env` and set `VITE_BACKEND_URL`.

## What's implemented

- Send/receive chat, matching `POST /chat`'s response shape exactly
  (`reply`, `conversation_mode`, `confidence`, `citations`, `quick_actions`,
  `handover_triggered`).
- Live-agent polling via `GET /chat/{session_id}/status`, using the same
  `since` cursor + `message_id` dedup fix applied to the Streamlit version —
  no duplicate or dropped messages during handover.
- Confidence meter (signature UI element) rendering the composite score
  plus the retrieval/grounding/intent breakdown.
- Citations (collapsible), quick-action reply buttons, reset conversation.

## Not yet built

- Tab 2 / agent dashboard — planned as its own separate app (different
  deploy, same backend), not part of this project.
- Auth — `USR-4401` is hardcoded in `src/App.jsx`, same as the Streamlit
  version's demo user.

## Deploying to Vercel

1. Push this folder to a GitHub repo (or a subfolder of your monorepo —
   set Vercel's "Root Directory" to this folder if so).
2. Import the repo in Vercel. Framework preset: **Vite**.
3. Add an environment variable: `VITE_BACKEND_URL` = your Render backend's
   public URL (e.g. `https://nebula-backend.onrender.com`).
4. Deploy. Vercel runs `npm run build` and serves `dist/` automatically.

Your FastAPI backend on Render will also need CORS opened up for the
Vercel domain — add `fastapi.middleware.cors.CORSMiddleware` to
`backend/main.py` if it isn't there already, allowing your Vercel origin.
