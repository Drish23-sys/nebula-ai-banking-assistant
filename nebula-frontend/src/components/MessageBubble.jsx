import Citations from "./Citations";
import QuickActions from "./QuickActions";

export default function MessageBubble({ message, onQuickAction }) {
  const isUser = message.role === "user";

  return (
    <div className={`msg-row ${isUser ? "msg-row--user" : "msg-row--assistant"}`}>
      <div className={`msg-bubble ${isUser ? "msg-bubble--user" : "msg-bubble--assistant"} ${message.isAgent ? "msg-bubble--agent" : ""}`}>
        {message.isAgent && <div className="msg-bubble__tag">Live agent</div>}
        <p className="msg-bubble__text">{message.text}</p>

        {/* Confidence meter intentionally not rendered here — internal
            signal, not something the customer should see. Still available
            on `message.confidence` if an internal/admin view wants it. */}
        {message.citations && <Citations citations={message.citations} />}
        {message.quickActions && (
          <QuickActions actions={message.quickActions} onSelect={onQuickAction} />
        )}
      </div>
    </div>
  );
}
