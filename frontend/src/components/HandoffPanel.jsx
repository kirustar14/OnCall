const PANELS = [
  ['What we know', 'what_we_know', false],
  ['What we think', 'what_we_think', false],
  ['What has been done', 'what_has_been_done', false],
  ['What is pending', 'what_is_pending', false],
  ['Unresolved', 'unresolved', true],
];

export default function HandoffPanel({ brief, onClose }) {
  if (!brief) return null;

  return (
    <div className="handoff-panel">
      <div className="handoff-head">
        <strong>Handoff briefing</strong>
        <span className="handoff-sub">for someone who just walked in</span>
        <button className="handoff-close" onClick={onClose}>
          close
        </button>
      </div>

      {brief.spoken_brief && <p className="handoff-spoken">“{brief.spoken_brief}”</p>}

      <div className="handoff-grid">
        {PANELS.map(([title, key, highlight]) => {
          const items = brief[key] || [];
          return (
            <div key={key} className={`handoff-cell ${highlight ? 'unresolved' : ''}`}>
              <h5>{title}</h5>
              {items.length === 0 ? (
                <p className="muted">—</p>
              ) : (
                <ul>
                  {items.map((line, i) => (
                    <li key={i}>{line}</li>
                  ))}
                </ul>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
