const KIND_MARK = {
  task: '▸',
  uncertainty: '?',
  conditional: '⏲',
};

function relTime(epochSeconds) {
  if (!epochSeconds) return '';
  const secs = Math.max(0, Math.round(Date.now() / 1000 - epochSeconds));
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  return `${mins}m ago`;
}

function WorkRow({ item }) {
  const orphan = item.status === 'open' && !item.owner;
  const done = item.status === 'completed' || item.status === 'answered';

  return (
    <div className={`work-row ${item.kind} ${orphan ? 'orphan' : ''} ${done ? 'done' : ''}`}>
      <div className="work-row-head">
        <span className="work-kind">{KIND_MARK[item.kind] || '▸'}</span>
        <span className="work-action">{item.action}</span>
        <span className={`work-status status-${item.status}`}>{item.status}</span>
      </div>

      <div className="work-row-meta">
        <span className={orphan ? 'work-noowner' : 'work-owner'}>
          {item.owner ? item.owner : 'NO OWNER'}
        </span>
        {item.requested_by && <span>asked by {item.requested_by}</span>}
        {item.trigger && <span>{item.trigger}</span>}
        <span>{relTime(item.opened_at)}</span>
        {item.prompted_at && !item.owner && (
          <span className="work-asked">agent asked the room</span>
        )}
      </div>

      {item.why_it_matters && <p className="work-why">{item.why_it_matters}</p>}

      {item.evidence && (
        <p className="work-evidence">
          <span className={`evidence-src src-${item.evidence_source || 'speech'}`}>
            {item.evidence_source === 'vision' ? 'SEEN' : 'HEARD'}
          </span>
          {item.evidence}
        </p>
      )}
    </div>
  );
}

export default function Ledger({ work }) {
  const items = work || [];
  const open = items.filter((w) => w.status === 'open' || w.status === 'acknowledged');
  const closed = items.filter((w) => w.status === 'completed' || w.status === 'answered');
  const orphanCount = open.filter((w) => !w.owner).length;

  return (
    <div className="ledger">
      <div className="ledger-head">
        <h4>Ledger</h4>
        <span className="ledger-sub">what was asked, who owns it</span>
        {orphanCount > 0 && (
          <span className="ledger-orphan-count">
            {orphanCount} unowned
          </span>
        )}
      </div>

      {items.length === 0 && <p className="muted">Nothing requested yet.</p>}

      {open.map((w) => (
        <WorkRow key={w.id} item={w} />
      ))}

      {closed.length > 0 && (
        <>
          <div className="ledger-divider">resolved</div>
          {closed.map((w) => (
            <WorkRow key={w.id} item={w} />
          ))}
        </>
      )}
    </div>
  );
}
