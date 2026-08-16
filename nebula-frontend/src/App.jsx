import { useCallback, useEffect, useRef, useState } from "react";
import Sidebar from "./components/Sidebar";
import ConnectionBanner from "./components/ConnectionBanner";
import MessageBubble from "./components/MessageBubble";
import TypingIndicator from "./components/TypingIndicator";
import ChatInput from "./components/ChatInput";
import LoginForm from "./components/LoginForm";
import SplashScreen from "./components/SplashScreen";
import { sendMessage, pollStatus, resetConversation } from "./api";

const AUTH_STORAGE_KEY = "nebula_auth";

function loadStoredAuth() {
  try {
    const raw = localStorage.getItem(AUTH_STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export default function App() {
  const [auth, setAuth] = useState(loadStoredAuth); // { user_id, email, token, session_id } | null
  const [showSplash, setShowSplash] = useState(true);
  const [messages, setMessages] = useState([]);
  const [conversationMode, setConversationMode] = useState("ai");
  const [sending, setSending] = useState(false);

  // Mutable pollers — refs so the interval closure always sees the latest
  // value without re-subscribing on every message.
  const seenMessageIds = useRef(new Set());
  const lastPollTs = useRef(null);
  const listEndRef = useRef(null);

  const handleAuthenticated = (result) => {
    localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(result));
    setAuth(result);
  };

  const handleLogout = () => {
    localStorage.removeItem(AUTH_STORAGE_KEY);
    setAuth(null);
    setMessages([]);
    setConversationMode("ai");
    seenMessageIds.current = new Set();
    lastPollTs.current = null;
  };

  // On login (including a returning session restored from localStorage),
  // load whatever history the account already has — without this, a
  // customer's chat would appear empty on every page load even though
  // it's genuinely persisted server-side for 72h. Seeds seenMessageIds/
  // lastPollTs so the live poll below doesn't re-fetch or duplicate any
  // of what's loaded here.
  useEffect(() => {
    if (!auth) return;
    let cancelled = false;

    const loadHistory = async () => {
      try {
        const data = await pollStatus({ token: auth.token, sessionId: auth.session_id, since: null });
        if (cancelled) return;
        setConversationMode(data.conversation_mode || "ai");

        const hydrated = [];
        for (const msg of data.new_messages || []) {
          seenMessageIds.current.add(msg.message_id);
          lastPollTs.current = msg.timestamp;
          if (msg.role === "user") hydrated.push({ role: "user", text: msg.text });
          else if (msg.role === "assistant") hydrated.push({ role: "assistant", text: msg.text });
          else if (msg.role === "agent") hydrated.push({ role: "assistant", text: msg.text, isAgent: true });
        }
        setMessages(hydrated);
      } catch {
        // Expired/invalid token surfaces naturally on the next send
        // attempt instead — no need to force a logout just from a
        // failed background history load.
      }
    };
    loadHistory();
    return () => {
      cancelled = true;
    };
  }, [auth?.token]);

  const scrollToBottom = () => {
    listEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(scrollToBottom, [messages]);

  const handleSend = useCallback(
    async (text) => {
      if (!auth) return;
      setMessages((prev) => [...prev, { role: "user", text }]);
      setSending(true);
      try {
        const data = await sendMessage({
          token: auth.token,
          sessionId: auth.session_id,
          userId: auth.user_id,
          message: text,
        });
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
        if (err.message.includes("expired")) {
          handleLogout();
          return;
        }
        setMessages((prev) => [
          ...prev,
          { role: "assistant", text: `Couldn't reach the backend: ${err.message}` },
        ]);
      } finally {
        setSending(false);
      }
    },
    [auth]
  );

  // Poll /chat/{session_id}/status every 3s while a live agent owns the
  // session. `since` + message_id dedup mirror the earlier fix: without
  // both, this either re-shows the whole history every poll or silently
  // drops every message after the first.
  useEffect(() => {
    if (!auth || conversationMode !== "human") return;

    const poll = async () => {
      try {
        const data = await pollStatus({ token: auth.token, sessionId: auth.session_id, since: lastPollTs.current });
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
  }, [auth, conversationMode]);

  const handleReset = async () => {
    setMessages([]);
    setConversationMode("ai");
    seenMessageIds.current = new Set();
    lastPollTs.current = null;
    try {
      await resetConversation({ token: auth.token });
    } catch (err) {
      console.error("Failed to reset conversation server-side:", err);
    }
  };

  if (showSplash) {
      return <SplashScreen onFinish={() => setShowSplash(false)} />;
}

  if (!auth) {
    return <LoginForm onAuthenticated={handleAuthenticated} />;
  }

  return (
    <div className="app-shell">
      <Sidebar sessionId={auth.session_id} userId={auth.email} onReset={handleReset} onLogout={handleLogout} />

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
