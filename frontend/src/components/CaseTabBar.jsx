export default function CaseTabBar({ cases, activeId, view, onSelectCase, onNewCase, onSelectView }) {
  return (
    <div className="tab-bar">
      <button className="new-case-btn" onClick={onNewCase}>
        + New Case
      </button>

      {cases.map((c) => (
        <button
          key={c.id}
          className={`tab ${c.id === activeId && view === 'case' ? 'tab-active' : ''} ${c.closed ? 'tab-closed' : ''}`}
          onClick={() => onSelectCase(c.id)}
        >
          Case {c.id.slice(0, 8)}
          {c.closed && <span className="tab-closed-label"> (closed)</span>}
        </button>
      ))}

      <div className="tab-bar-spacer" />

      <button
        className={`tab database-tab ${view === 'agentlog' ? 'tab-active' : ''}`}
        onClick={() => onSelectView('agentlog')}
      >
        Agent Log
      </button>
      <button
        className={`tab database-tab ${view === 'database' ? 'tab-active' : ''}`}
        onClick={() => onSelectView('database')}
      >
        Patient Database
      </button>
    </div>
  );
}
