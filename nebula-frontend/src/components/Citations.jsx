import { useState } from "react";

export default function Citations({ citations }) {
  const [open, setOpen] = useState(false);
  if (!citations || citations.length === 0) return null;

  return (
    <div className="citations">
      <button className="citations__toggle" onClick={() => setOpen((o) => !o)}>
        <span className={`citations__chevron ${open ? "is-open" : ""}`}>›</span>
        {citations.length} source{citations.length > 1 ? "s" : ""}
      </button>
      {open && (
        <ul className="citations__list">
          {citations.map((c, i) => (
            <li key={i}>
              <span className="citations__source">{c.source}</span>
              <span className="citations__sep">·</span>
              <span className="citations__section">{c.section}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
