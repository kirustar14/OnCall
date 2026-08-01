import { useState } from 'react';
import CaseTabBar from './components/CaseTabBar';
import CaseSession from './components/CaseSession';
import PatientDatabase from './components/PatientDatabase';
import './App.css';

function newCaseId() {
  return crypto.randomUUID();
}

export default function App() {
  const [cases, setCases] = useState([]); // [{id, closed}]
  const [activeId, setActiveId] = useState(null);
  const [databaseActive, setDatabaseActive] = useState(false);

  function handleNewCase() {
    const id = newCaseId();
    setCases((prev) => [...prev, { id, closed: false }]);
    setActiveId(id);
    setDatabaseActive(false);
  }

  function handleSelect(id) {
    setActiveId(id);
    setDatabaseActive(false);
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>Servare</h1>
        <p className="tagline">Always-on voice agent for the ER</p>
      </header>

      <CaseTabBar
        cases={cases}
        activeId={activeId}
        onSelect={handleSelect}
        onNewCase={handleNewCase}
        onSelectDatabase={() => setDatabaseActive(true)}
        databaseActive={databaseActive}
      />

      <main className="app-main">
        {cases.length === 0 && !databaseActive && (
          <div className="empty-state">
            <p>No cases open. Click "New Case" to start streaming.</p>
          </div>
        )}

        {cases.map((c) => (
          <CaseSession key={c.id} caseId={c.id} active={c.id === activeId && !databaseActive} />
        ))}

        {databaseActive && <PatientDatabase />}
      </main>
    </div>
  );
}
