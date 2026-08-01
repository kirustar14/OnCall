export default function CaseTabBar({ cases, activeId, onSelect, onNewCase, onSelectDatabase, databaseActive }) {
  return (
    <div className="tab-bar">
      <button className="new-case-btn" onClick={onNewCase}>
        + New Case
      </button>

      {cases.map((c) => (
        <button
          key={c.id}
          className={`tab ${c.id === activeId && !databaseActive ? 'tab-active' : ''} ${c.closed ? 'tab-closed' : ''}`}
          onClick={() => onSelect(c.id)}
        >
          Case {c.id.slice(0, 8)}
          {c.closed && <span className="tab-closed-label"> (closed)</span>}
        </button>
      ))}

      <div className="tab-bar-spacer" />

      <button
        className={`tab database-tab ${databaseActive ? 'tab-active' : ''}`}
        onClick={onSelectDatabase}
      >
        Patient Database
      </button>
    </div>
  );
}
