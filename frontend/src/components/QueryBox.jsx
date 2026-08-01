import { useState } from 'react';

const API_BASE = import.meta.env.VITE_API_BASE || `http://${window.location.hostname}:8000`;

export default function QueryBox({ cases }) {
  const [caseId, setCaseId] = useState('');
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState('');
  const [loading, setLoading] = useState(false);

  async function submit(e) {
    e.preventDefault();
    if (!caseId || !question.trim()) return;
    setLoading(true);
    setAnswer('');
    try {
      const resp = await fetch(`${API_BASE}/api/query`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ case_id: caseId, question }),
      });
      const data = await resp.json();
      setAnswer(data.answer || 'No answer returned.');
    } catch (err) {
      setAnswer(`Error: ${err.message}`);
    } finally {
      setLoading(false);
    }
  }

  return (
    <form className="query-box" onSubmit={submit}>
      <h3>Ask a question</h3>
      <div className="query-row">
        <select value={caseId} onChange={(e) => setCaseId(e.target.value)}>
          <option value="">Select case…</option>
          {cases.map((c) => (
            <option key={c.case_id} value={c.case_id}>
              Case {c.case_id.slice(0, 8)} ({c.status})
            </option>
          ))}
        </select>
        <input
          type="text"
          placeholder='e.g. "what was the BP on arrival?"'
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
        />
        <button type="submit" disabled={loading}>
          {loading ? 'Asking…' : 'Ask'}
        </button>
      </div>
      {answer && <div className="query-answer">{answer}</div>}
    </form>
  );
}
