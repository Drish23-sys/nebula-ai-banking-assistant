# Nebula — Agent Console (React)

Standalone dashboard for live agents, meant to run on a different machine
than the customer chat app. Talks to the same FastAPI backend, no backend
changes required — this is a straight port of the Streamlit app's Tab 2.

## Run locally

```bash
npm install
npm run dev
```

Opens on `http://localhost:5174` (different port than the customer app,
so you can run both side-by-side while testing on one machine). Backend
must be running:

```bash
uvicorn backend.main:app --reload --port 8000
```

To point at a different backend, copy `.env.example` to `.env` and set
`VITE_BACKEND_URL`.

## What's implemented

- Polls `GET /agent/queue` every 5s, plus a manual refresh button.
- One card per open ticket: user, trigger reason (fraud / low confidence /
  explicit request / out of scope), the four summary fields, a reply box
  (`POST /agent/{ticket_id}/reply`), and resolve (`POST
  /agent/{ticket_id}/resolve`) which hands the session back to the AI and
  removes the card from view.

## Deploying to Vercel

Same process as the customer app — separate Vercel project (or same repo,
different Root Directory), Framework preset **Vite**, set
`VITE_BACKEND_URL` to your Render backend's public URL.

Since this will run on a different machine/network than the customer app,
make sure your backend's CORS config allows both deployed origins.
