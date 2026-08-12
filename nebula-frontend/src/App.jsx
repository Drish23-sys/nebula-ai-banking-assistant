import { useCallback, useEffect, useRef, useState } from "react";
import Sidebar from "./components/Sidebar";
import ConnectionBanner from "./components/ConnectionBanner";
import MessageBubble from "./components/MessageBubble";
import TypingIndicator from "./components/TypingIndicator";
import ChatInput from "./components/ChatInput";
import { sendMessage, pollStatus } from "./api";

const USER_ID = "USR-4401"; // demo user; swap for real auth later

function newSessionId() {
  return `sess_${crypto.randomUUID().slice(0, 8)}`;
}

export default function App() {
  const [sessionId, setSessionId] = useState(newSessionId);
  const [messages, setMessages] = useState([]);
  const [conversationMode, setConversationMode] = useState("ai");
  const [sending, setSending] = useState(false);

  // Mutable pollers — refs so the interval closure always sees the latest
  // value without re-subscribing on every message.
  const seenMessageIds = useRef(new Set());
  const lastPollTs = useRef(null);
  const listEndRef = useRef(null);

  const scrollToBottom = () => {
    listEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(scrollToBottom, [messages]);

  const handleSend = useCallback(
    async (text) => {
      setMessages((prev) => [...prev, { role: "user", text }]);
      setSending(true);
      try {
        const data = await sendMessage({ sessionId, userId: USER_ID, message: text });
        setConversationMode(data.conversation_mode || "ai");
        if (data.reply) {
          setMessages((prev) => [
            ...prev,
            {
              role: "assistant",
              text: data.reply,
              confidence: data.confidence,
              citations: data.citations,
              quickActions: data.quick_actions,
            },
          ]);
        }
      } catch (err) {
        setMessages((prev) => [
          ...prev,
          { role: "assistant", text: `Couldn't reach the backend: ${err.message}` },
        ]);
      } finally {
        setSending(false);
      }
    },
    [sessionId]
  );

  // Poll /chat/{session_id}/status every 3s while a live agent owns the
  // session. `since` + message_id dedup mirror the Streamlit version's
  // fix: without both, this either re-shows the whole history every poll
  // or silently drops every message after the first.
  useEffect(() => {
    if (conversationMode !== "human") return;

    const poll = async () => {
      try {
        const data = await pollStatus({ sessionId, since: lastPollTs.current });
        setConversationMode(data.conversation_mode || "human");

        const fresh = [];
        for (const msg of data.new_messages || []) {
          if (seenMessageIds.current.has(msg.message_id)) continue;
          seenMessageIds.current.add(msg.message_id);
          lastPollTs.current = msg.timestamp;
          if (msg.role === "agent") {
            fresh.push({ role: "assistant", text: msg.text, isAgent: true });
          }
        }
        if (fresh.length > 0) setMessages((prev) => [...prev, ...fresh]);
      } catch {
        // Silent — next 3s tick retries. A transient network blip
        // shouldn't spam the customer with error bubbles.
      }
    };

    const interval = setInterval(poll, 3000);
    poll(); // fire immediately on entering human mode, don't wait 3s
    return () => clearInterval(interval);
  }, [conversationMode, sessionId]);

  const handleReset = () => {
    setSessionId(newSessionId());
    setMessages([]);
    setConversationMode("ai");
    seenMessageIds.current = new Set();
    lastPollTs.current = null;
  };

  return (
    <div className="app-shell">
      <Sidebar sessionId={sessionId} userId={USER_ID} onReset={handleReset} />

      <main className="chat-panel">
        <ConnectionBanner mode={conversationMode} />

        <div className="chat-panel__list">
          {messages.length === 0 && (
            <div className="empty-state">
              <div className="empty-state__glow" />
              <p>Ask about your balance, a transfer limit, a card, or report an issue.</p>
            </div>
          )}
          {messages.map((m, i) => (
            <MessageBubble key={i} message={m} onQuickAction={handleSend} />
          ))}
          {/* Waiting for a reply whenever the most recent message is the
              customer's own — covers both the AI generating a response
              and an agent who hasn't typed back yet, with no extra state
              needed beyond the message list itself. */}
          {messages.length > 0 && messages[messages.length - 1].role === "user" && (
            <TypingIndicator isAgent={conversationMode === "human"} />
          )}
          <div ref={listEndRef} />
        </div>

        <ChatInput disabled={sending} onSend={handleSend} />
      </main>
    </div>
  );
}
