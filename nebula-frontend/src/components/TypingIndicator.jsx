export default function TypingIndicator({ isAgent }) {
  return (
    <div className="msg-row msg-row--assistant">
      <div className={`msg-bubble msg-bubble--assistant typing-bubble ${isAgent ? "msg-bubble--agent" : ""}`}>
        {isAgent && <div className="msg-bubble__tag">Live agent</div>}
        <div className="typing-dots">
          <span />
          <span />
          <span />
        </div>
      </div>
    </div>
  );
}
