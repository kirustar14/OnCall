const STEP_LABEL = {
  trigger: 'Trigger',
  tool_call: 'Tool call',
  tool_result: 'Tool result',
  decision: 'Decision',
};

function formatTime(ts) {
  if (!ts) return '';
  return new Date(ts * 1000).toLocaleTimeString([], { hour12: false });
}

function StepBody({ step }) {
  switch (step.step) {
    case 'trigger':
      return <span>New fact: {step.text}</span>;
    case 'tool_call':
      return (
        <span>
          called <code>{step.tool}</code>
          {step.query ? <> with query "{step.query}"</> : null}
        </span>
      );
    case 'tool_result':
      return (
        <span>
          <code>{step.tool}</code> returned: {step.result_summary}
        </span>
      );
    case 'decision':
      return (
        <span>
          <strong>{step.action_needed ? 'Action needed' : 'No action needed'}</strong>
          {' — '}
          {step.reasoning}
          {step.action_needed && step.alert_text ? (
            <div className="agent-log-spoken">🔊 "{step.alert_text}"</div>
          ) : null}
        </span>
      );
    default:
      return <span>{JSON.stringify(step)}</span>;
  }
}

export default function AgentLog({ steps }) {
  const sorted = [...steps].sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0));

  return (
    <div className="agent-log">
      <h2>Agent Log</h2>
      <p className="muted">
        Every reasoning step the agent takes, live — no hardcoded conflict rules, just the model
        deciding what matters.
      </p>

      {sorted.length === 0 && <p className="muted">No agent activity yet.</p>}

      <ul className="agent-log-list">
        {sorted.map((step) => (
          <li key={step.id || `${step.case_id}-${step.timestamp}-${step.step}`} className={`agent-log-item step-${step.step}`}>
            <div className="agent-log-meta">
              <span className="agent-log-time">{formatTime(step.timestamp)}</span>
              <span className="agent-log-case">Case {step.case_id?.slice(0, 8)}</span>
              <span className={`agent-log-badge badge-${step.step}`}>{STEP_LABEL[step.step] || step.step}</span>
            </div>
            <div className="agent-log-body">
              <StepBody step={step} />
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
