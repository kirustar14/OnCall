import { useCallback, useEffect, useRef, useState } from 'react';
import CaseTabBar from './components/CaseTabBar';
import CaseSession from './components/CaseSession';
import PatientDatabase from './components/PatientDatabase';
import AgentLog from './components/AgentLog';
import './App.css';

const API_BASE = import.meta.env.VITE_API_BASE || `http://${window.location.hostname}:8000`;

function newCaseId() {
  return crypto.randomUUID();
}

export default function App() {
  const [cases, setCases] = useState([]); // [{id, closed}]
  const [activeId, setActiveId] = useState(null);
  const [view, setView] = useState('case'); // 'case' | 'database' | 'agentlog'
  const [agentSteps, setAgentSteps] = useState([]);
  const seenStepIds = useRef(new Set());

  // Hydrate any agent activity that happened before this page load (e.g. a
  // refresh mid-case) — live steps after this arrive over each case's own
  // WebSocket via onAgentStep below.
  useEffect(() => {
    let cancelled = false;
    fetch(`${API_BASE}/api/agent_log`)
      .then((r) => r.json())
      .then((steps) => {
        if (cancelled) return;
        for (const s of steps) if (s.id) seenStepIds.current.add(s.id);
        setAgentSteps(steps);
      })
      .catch((err) => console.warn('failed to hydrate agent log', err));
    return () => {
      cancelled = true;
    };
  }, []);

  const handleAgentStep = useCallback((step) => {
    if (step.id) {
      if (seenStepIds.current.has(step.id)) return;
      seenStepIds.current.add(step.id);
    }
    setAgentSteps((prev) => [...prev, step]);
  }, []);

  function handleNewCase() {
    const id = newCaseId();
    setCases((prev) => [...prev, { id, closed: false }]);
    setActiveId(id);
    setView('case');
  }

  function handleSelectCase(id) {
    setActiveId(id);
    setView('case');
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>OnCall</h1>
        <p className="tagline">Always-on voice agent for the ER</p>
      </header>

      <CaseTabBar
        cases={cases}
        activeId={activeId}
        view={view}
        onSelectCase={handleSelectCase}
        onNewCase={handleNewCase}
        onSelectView={setView}
      />

      <main className="app-main">
        {cases.length === 0 && view === 'case' && (
          <div className="empty-state">
            <p>No cases open. Click "New Case" to start streaming.</p>
          </div>
        )}

        {cases.map((c) => (
          <CaseSession
            key={c.id}
            caseId={c.id}
            active={c.id === activeId && view === 'case'}
            onAgentStep={handleAgentStep}
          />
        ))}

        {view === 'database' && <PatientDatabase />}
        {view === 'agentlog' && <AgentLog steps={agentSteps} />}
      </main>
    </div>
  );
}
