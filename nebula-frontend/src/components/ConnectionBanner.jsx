export default function ConnectionBanner({ mode }) {
  if (mode === "human") {
    return (
      <div className="banner banner--human">
        <span className="banner__pulse" />
        <span>You're connected to a live agent — this chat is being handled by a human.</span>
      </div>
    );
  }
  return (
    <div className="banner banner--ai">
      <span className="banner__dot" />
      <span>Chatting with the Nebula AI Assistant</span>
    </div>
  );
}
