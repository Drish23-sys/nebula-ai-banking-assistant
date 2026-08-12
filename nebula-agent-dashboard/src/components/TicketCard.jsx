import { useCallback, useEffect, useRef, useState } from "react";
import TriggerBadge from "./TriggerBadge";
import { sendReply, resolveTicket, fetchSessionStatus } from "../api";

const POLL_MS = 3000;

const ROLE_LABEL = {
  user: "Customer",
  assistant: "Nebula AI",
  agent: "You",
};

export default function TicketCard({ ticket, onResolved }) {
  const [reply, setReply] = useState("");
  const [sendingReply, setSendingReply] = useState(false);
  const [resolving, setResolving] = useState(false);
  const [status, setStatus] = useState(null); // { type: 'ok'|'error', text }
  const [thread, setThread] = useState([]);

  const seenIds = useRef(new Set());
  const lastTs = useRef(null);
  const threadEndRef = useRef(null);
  const summary = ticket.summary || {};

  const poll = useCallback(async () => {
    try {
      const data = await fetchSessionStatus(ticket.session_id, lastTs.current);
      const fresh = [];
      for (const msg of data.new_messages || []) {
        if (seenIds.current.has(msg.message_id)) continue;
        seenIds.current.add(msg.message_id);
        lastTs.current = msg.timestamp;
        fresh.push(msg);
      }
      if (fresh.length > 0) setThread((prev) => [...prev, ...fresh]);
    } catch {
      // Silent — next tick retries, no need to interrupt the agent with
      // a transient network error on every poll.
    }
  }, [ticket.session_id]);

  useEffect(() => {
    poll(); // full history on mount (lastTs is null, so this fetches everything so far)
    const interval = setInterval(poll, POLL_MS);
    return () => clearInterval(interval);
  }, [poll]);

  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [thread]);

  const handleReply = async () => {
    if (!reply.trim()) {
      setStatus({ type: "error", text: "Reply can't be empty." });
      return;
    }
    setSendingReply(true);
    setStatus(null);
    try {
      await sendReply(ticket.ticket_id, reply);
      setReply("");
      poll(); // fetch immediately rather than waiting up to 3s for the next tick
    } catch (err) {
      setStatus({ type: "error", text: `Failed to send: ${err.message}` });
    } finally {
      setSendingReply(false);
    }
  };

  const handleResolve = async () => {
    setResolving(true);
    setStatus(null);
    try {
      await resolveTicket(ticket.ticket_id);
      onResolved(ticket.ticket_id);
    } catch (err) {
      setStatus({ type: "error", text: `Failed to resolve: ${err.message}` });
      setResolving(false);
    }
  };

  return (
    <div className="ticket-card">
      <div className="ticket-card__head">
        <div>
          <div className="ticket-card__user">{ticket.user_name || ticket.user_id}</div>
          <div className="ticket-card__meta tabular">
            {ticket.ticket_id} · session {ticket.session_id} · {ticket.created_at}
          </div>
        </div>
        <TriggerBadge reason={ticket.trigger_reason} />
      </div>

      <dl className="ticket-card__summary">
        <div>
          <dt>Issue</dt>
          <dd>{summary.issue || "—"}</dd>
        </div>
        <div>
          <dt>Context</dt>
          <dd>{summary.context || "—"}</dd>
        </div>
        <div>
          <dt>Suggested next step</dt>
          <dd>{summary.suggested_next_step || "—"}</dd>
        </div>
      </dl>

      <div className="ticket-card__thread">
        {thread.length === 0 && <div className="ticket-card__thread-empty">No messages yet.</div>}
        {thread.map((msg) => (
          <div key={msg.message_id} className={`thread-msg thread-msg--${msg.role}`}>
            <span className="thread-msg__role">{ROLE_LABEL[msg.role] || msg.role}</span>
            <p className="thread-msg__text">{msg.text}</p>
          </div>
        ))}
        <div ref={threadEndRef} />
      </div>

      <textarea
        className="ticket-card__reply"
        placeholder="Reply to customer…"
        value={reply}
        onChange={(e) => setReply(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            handleReply();
          }
        }}
        rows={2}
      />

      {status && (
        <div className={`ticket-card__status ticket-card__status--${status.type}`}>{status.text}</div>
      )}

      <div className="ticket-card__actions">
        <button className="btn btn--ghost" onClick={handleReply} disabled={sendingReply}>
          {sendingReply ? "Sending…" : "Send reply"}
        </button>
        <button className="btn btn--primary" onClick={handleResolve} disabled={resolving}>
          {resolving ? "Resolving…" : "Resolve — hand back to AI"}
        </button>
      </div>
    </div>
  );
}
