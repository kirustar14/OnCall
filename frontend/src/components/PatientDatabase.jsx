import { useEffect, useState } from 'react';
import QueryBox from './QueryBox';

const API_BASE = import.meta.env.VITE_API_BASE || `http://${window.location.hostname}:8000`;

export default function PatientDatabase() {
  const [cases, setCases] = useState([]);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const resp = await fetch(`${API_BASE}/api/cases`);
        const data = await resp.json();
        if (!cancelled) setCases(data);
      } catch (err) {
        console.warn('failed to poll case list', err);
      }
    }

    poll();
    const interval = setInterval(poll, 2500);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  return (
    <div className="patient-database">
      <h2>Patient Database</h2>
      <QueryBox cases={cases} />

      <table className="case-table">
        <thead>
          <tr>
            <th>Case</th>
            <th>Status</th>
            <th>Details</th>
            <th>Vitals</th>
            <th>Allergies</th>
            <th>Medications</th>
            <th>Notes</th>
          </tr>
        </thead>
        <tbody>
          {cases.map((c) => (
            <tr key={c.case_id} className={c.status === 'closed' ? 'row-closed' : ''}>
              <td>{c.case_id.slice(0, 8)}</td>
              <td>{c.status}</td>
              <td>
                {[c.case_details?.age, c.case_details?.sex, c.case_details?.mechanism]
                  .filter(Boolean)
                  .join(' · ') || '—'}
              </td>
              <td>{c.vitals?.map((v) => `${v.name}: ${v.value}`).join(', ') || '—'}</td>
              <td>{c.allergies?.map((a) => a.allergen).join(', ') || '—'}</td>
              <td>{c.medications?.map((m) => `${m.name} (${m.status})`).join(', ') || '—'}</td>
              <td>{c.notes?.join(' ') || '—'}</td>
            </tr>
          ))}
          {cases.length === 0 && (
            <tr>
              <td colSpan={7} className="muted">
                No cases yet — start one with "New Case".
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
