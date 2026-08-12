import { useCallback, useEffect, useState } from "react";
import Header from "./components/Header";
import TicketCard from "./components/TicketCard";
import { fetchQueue } from "./api";

const AUTO_REFRESH_MS = 5000;

export default function App() {
  const [tickets, setTickets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(async (silent = false) => {
    if (!silent) setRefreshing(true);
    try {
      const data = await fetchQueue();
      setTickets(data);
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(() => load(true), AUTO_REFRESH_MS);
    return () => clearInterval(interval);
  }, [load]);

  const handleResolved = (ticketId) => {
    setTickets((prev) => prev.filter((t) => t.ticket_id !== ticketId));
  };

  return (
    <div className="console-shell">
      <Header count={tickets.length} onRefresh={() => load(false)} refreshing={refreshing} />

      <div className="console-body">
        {error && <div className="console-error">Couldn't reach the backend: {error}</div>}

        {!loading && !error && tickets.length === 0 && (
          <div className="empty-queue">
            <div className="empty-queue__glow" />
            <p>No tickets waiting. Queue's clear.</p>
          </div>
        )}

        <div className="ticket-grid">
          {tickets.map((ticket) => (
            <TicketCard key={ticket.ticket_id} ticket={ticket} onResolved={handleResolved} />
          ))}
        </div>
      </div>
    </div>
  );
}
