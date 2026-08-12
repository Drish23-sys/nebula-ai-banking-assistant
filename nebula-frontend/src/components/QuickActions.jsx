export default function QuickActions({ actions, onSelect }) {
  if (!actions || actions.length === 0) return null;

  return (
    <div className="quick-actions">
      {actions.map((action, i) => (
        <button key={i} className="quick-actions__btn" onClick={() => onSelect(action)}>
          {action}
        </button>
      ))}
    </div>
  );
}
