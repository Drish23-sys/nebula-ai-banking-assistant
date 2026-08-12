const DECISION_META = {
  auto_respond: { label: "Confident", color: "var(--mint)", soft: "var(--mint-soft)" },
  clarify: { label: "Needs clarity", color: "var(--amber)", soft: "var(--amber-soft)" },
  handover: { label: "Handed to agent", color: "var(--coral)", soft: "var(--coral-soft)" },
};

const SUB_SCORES = [
  { key: "retrieval", label: "Retrieval" },
  { key: "grounding", label: "Grounding" },
  { key: "intent", label: "Intent" },
];

// The signature element: renders the backend's confidence_breakdown as a
// glowing signal meter rather than plain text — this app's whole premise
// is transparency about how sure the assistant is, so that deserves to be
// the one visually "loud" thing on the page.
export default function ConfidenceMeter({ confidence }) {
  if (!confidence) return null;
  const decision = confidence.decision || "auto_respond";
  const meta = DECISION_META[decision] || DECISION_META.auto_respond;
  const composite = confidence.composite ?? 0;
  const pct = Math.round(composite * 100);

  return (
    <div className="conf-meter" style={{ "--meter-color": meta.color, "--meter-soft": meta.soft }}>
      <div className="conf-meter__head">
        <span className="conf-meter__dot" />
        <span className="conf-meter__label">{meta.label}</span>
        <span className="conf-meter__pct tabular">{pct}%</span>
      </div>
      <div className="conf-meter__track">
        <div className="conf-meter__fill" style={{ width: `${pct}%` }} />
      </div>
      <div className="conf-meter__subrow">
        {SUB_SCORES.map(({ key, label }) => (
          <div className="conf-meter__sub" key={key} title={`${label}: ${(confidence[key] ?? 0).toFixed(2)}`}>
            <span className="conf-meter__sub-label">{label}</span>
            <div className="conf-meter__sub-track">
              <div
                className="conf-meter__sub-fill"
                style={{ width: `${Math.round((confidence[key] ?? 0) * 100)}%` }}
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
