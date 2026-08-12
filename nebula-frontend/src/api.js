// Reads from Vite env var for deployed backend URL, falls back to local
// dev backend on 8000. Set VITE_BACKEND_URL in .env or your Vercel
// project settings when deploying.
export const BASE_URL = import.meta.env.VITE_BACKEND_URL || "http://localhost:8000";

export async function sendMessage({ sessionId, userId, message }) {
  const resp = await fetch(`${BASE_URL}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, user_id: userId, message }),
  });
  if (!resp.ok) throw new Error(`Backend error: ${resp.status}`);
  return resp.json();
}

export async function pollStatus({ sessionId, since }) {
  const params = since ? `?since=${encodeURIComponent(since)}` : "";
  const resp = await fetch(`${BASE_URL}/chat/${sessionId}/status${params}`);
  if (!resp.ok) throw new Error(`Backend error: ${resp.status}`);
  return resp.json();
}
