export default function Sidebar({ sessionId, userId, onReset, onLogout }) {
  return (
    <aside className="sidebar">
      <div className="sidebar__brand">
        <span className="sidebar__mark">N</span>
        <span className="sidebar__wordmark">Nebula</span>
      </div>

      <div className="sidebar__section">
        <div className="sidebar__label">Session</div>
        <div className="sidebar__value tabular">{sessionId}</div>
      </div>
      <div className="sidebar__section">
        <div className="sidebar__label">Account</div>
        <div className="sidebar__value tabular">{userId}</div>
      </div>

      <button className="sidebar__reset" onClick={onReset}>
        Reset conversation
      </button>
      {onLogout && (
        <button className="sidebar__logout" onClick={onLogout}>
          Log out
        </button>
      )}

      <div className="sidebar__footer">Nebula AI Banking Assistant</div>
    </aside>
  );
}
