const TRIGGER_META = {
  fraud_flag: { label: "Fraud flag", color: "var(--coral)", soft: "var(--coral-soft)" },
  low_confidence_repeated: { label: "Low confidence", color: "var(--amber)", soft: "var(--amber-soft)" },
  explicit_request: { label: "Explicit request", color: "var(--signal-blue)", soft: "var(--signal-blue-soft)" },
  out_of_scope: { label: "Out of scope", color: "var(--text-muted)", soft: "var(--bg-elevated-2)" },
};

export default function TriggerBadge({ reason }) {
  const meta = TRIGGER_META[reason] || { label: reason, color: "var(--text-muted)", soft: "var(--bg-elevated-2)" };
  return (
    <span className="trigger-badge" style={{ "--badge-color": meta.color, "--badge-soft": meta.soft }}>
      <span className="trigger-badge__dot" />
      {meta.label}
    </span>
  );
}
