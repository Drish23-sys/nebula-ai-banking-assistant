// Reads from Vite env var for deployed backend URL, falls back to local
// dev backend on 8000. Set VITE_BACKEND_URL in .env or your Vercel
// project settings when deploying.
export const BASE_URL = import.meta.env.VITE_BACKEND_URL || "http://localhost:8000";

async function parseErrorDetail(resp) {
  try {
    const data = await resp.json();
    return data.detail || `Backend error: ${resp.status}`;
  } catch {
    return `Backend error: ${resp.status}`;
  }
}

export async function signup({ email, password }) {
  const resp = await fetch(`${BASE_URL}/auth/signup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!resp.ok) throw new Error(await parseErrorDetail(resp));
  return resp.json(); // { user_id, email, token, session_id }
}

export async function login({ email, password }) {
  const resp = await fetch(`${BASE_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!resp.ok) throw new Error(await parseErrorDetail(resp));
  return resp.json();
}

export async function sendMessage({ token, sessionId, userId, message }) {
  const resp = await fetch(`${BASE_URL}/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    // session_id/user_id still sent for the request shape, but the
    // backend derives real identity from the token, not these — see
    // require_user() in main.py.
    body: JSON.stringify({ session_id: sessionId, user_id: userId, message }),
  });
  if (resp.status === 401) throw new Error("Session expired — please log in again.");
  if (!resp.ok) throw new Error(await parseErrorDetail(resp));
  return resp.json();
}

export async function pollStatus({ token, sessionId, since }) {
  const params = since ? `?since=${encodeURIComponent(since)}` : "";
  const resp = await fetch(`${BASE_URL}/chat/${sessionId}/status${params}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!resp.ok) throw new Error(await parseErrorDetail(resp));
  return resp.json();
}

export async function resetConversation({ token }) {
  const resp = await fetch(`${BASE_URL}/chat/reset`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!resp.ok) throw new Error(await parseErrorDetail(resp));
  return resp.json();
}