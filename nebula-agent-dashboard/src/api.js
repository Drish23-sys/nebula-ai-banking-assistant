export const BASE_URL = import.meta.env.VITE_BACKEND_URL || "http://localhost:8000";

export async function fetchQueue() {
  const resp = await fetch(`${BASE_URL}/agent/queue`);
  if (!resp.ok) throw new Error(`Backend error: ${resp.status}`);
  const data = await resp.json();
  return data.tickets || [];
}

export async function sendReply(ticketId, message) {
  const resp = await fetch(`${BASE_URL}/agent/${ticketId}/reply`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  if (!resp.ok) throw new Error(`Backend error: ${resp.status}`);
  return resp.json();
}

export async function resolveTicket(ticketId) {
  const resp = await fetch(`${BASE_URL}/agent/${ticketId}/resolve`, { method: "POST" });
  if (!resp.ok) throw new Error(`Backend error: ${resp.status}`);
  return resp.json();
}

// Agent-facing endpoint, separate from the customer app's polling route —
// that one now requires a customer login token and derives the session
// from their identity, which the agent dashboard has no way to provide
// (it isn't a logged-in customer). See agent_session_messages() in main.py.
export async function fetchSessionStatus(sessionId, since) {
  const params = since ? `?since=${encodeURIComponent(since)}` : "";
  const resp = await fetch(`${BASE_URL}/agent/session/${sessionId}/messages${params}`);
  if (!resp.ok) throw new Error(`Backend error: ${resp.status}`);
  return resp.json();
}
