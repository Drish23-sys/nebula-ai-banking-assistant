export default function Header({ count, onRefresh, refreshing }) {
  return (
    <header className="console-header">
      <div className="console-header__brand">
        <span className="console-header__mark">N</span>
        <div>
          <div className="console-header__title">Agent Console</div>
          <div className="console-header__subtitle">Nebula banking assistant</div>
        </div>
      </div>

      <div className="console-header__right">
        <div className="queue-count">
          <span className="queue-count__num tabular">{count}</span>
          <span className="queue-count__label">waiting</span>
        </div>
        <button className="btn btn--ghost" onClick={onRefresh} disabled={refreshing}>
          {refreshing ? "Refreshing…" : "Refresh"}
        </button>
      </div>
    </header>
  );
}
