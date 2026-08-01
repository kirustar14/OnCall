import { useCaseSocket } from '../hooks/useCaseSocket';
import VideoPreview from './VideoPreview';

function StatusBadge({ status }) {
  const label = {
    connecting: 'Connecting…',
    recording: 'Recording',
    closed: 'Closed',
    error: 'Error',
  }[status] || status;

  return <span className={`status-badge status-${status}`}>{label}</span>;
}

function StructuredList({ title, items, render }) {
  return (
    <div className="structured-block">
      <h4>{title}</h4>
      {items.length === 0 ? (
        <p className="muted">None recorded yet</p>
      ) : (
        <ul>
          {items.map((item, i) => (
            <li key={i}>{render(item)}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function CaseSession({ caseId, active, onAgentStep }) {
  const { status, transcript, interim, structured, alerts, videoStream, endCase } = useCaseSocket(
    caseId,
    onAgentStep
  );

  return (
    <div className={`case-session ${active ? '' : 'hidden'} ${status === 'closed' ? 'case-closed' : ''}`}>
      {alerts.length > 0 && (
        <div className="alert-stack">
          {alerts.map((a) => (
            <div className="alert-banner" key={a.id}>
              ⚠️ {a.text}
            </div>
          ))}
        </div>
      )}

      <div className="case-session-grid">
        <div className="video-column">
          <VideoPreview stream={videoStream} />
          <div className="case-controls">
            <StatusBadge status={status} />
            {status !== 'closed' && (
              <button className="end-case-btn" onClick={endCase}>
                End Case
              </button>
            )}
          </div>
        </div>

        <div className="transcript-column">
          <h4>Live Transcript</h4>
          <div className="transcript-box">
            <span>{transcript}</span>
            {interim && <span className="interim"> {interim}</span>}
            {!transcript && !interim && <span className="muted">Listening…</span>}
          </div>
        </div>

        <div className="structured-column">
          <h4>Case {caseId.slice(0, 8)}</h4>
          {(structured.case_details.age || structured.case_details.sex || structured.case_details.mechanism) && (
            <p className="case-summary-line">
              {[structured.case_details.age, structured.case_details.sex, structured.case_details.mechanism]
                .filter(Boolean)
                .join(' · ')}
            </p>
          )}
          <StructuredList
            title="Allergies"
            items={structured.allergies}
            render={(a) => `${a.allergen} (${a.source})`}
          />
          <StructuredList
            title="Vitals"
            items={structured.vitals}
            render={(v) => `${v.name}: ${v.value}`}
          />
          <StructuredList
            title="Medications"
            items={structured.medications}
            render={(m) => `${m.name} — ${m.status}`}
          />
          <StructuredList title="Notes" items={structured.notes} render={(n) => n} />
        </div>
      </div>
    </div>
  );
}
