import { useState } from 'react';

/**
 * Map a diarized voice to a role.
 *
 * Deepgram tells us reliably *that* the speaker changed — it never tells us who
 * they are, and we deliberately don't try to work it out. A human labels each
 * voice once, and from then on every task that voice claims is attributed
 * correctly. It's a label on an index, not identification.
 */

const QUICK_ROLES = ['MEDIC', 'DR. REYES', 'NURSE OKAFOR'];

function SpeakerRow({ index, seen, role, onAssign }) {
  const [custom, setCustom] = useState('');

  return (
    <div className={`speaker-row ${role ? 'assigned' : ''}`}>
      <div className="speaker-row-head">
        <span className="speaker-index">Speaker {index}</span>
        {role ? (
          <span className="speaker-role-assigned">{role}</span>
        ) : (
          <span className="speaker-unassigned">unassigned</span>
        )}
        <span className="speaker-count">{seen.count} turn{seen.count === 1 ? '' : 's'}</span>
      </div>

      {seen.sample && <p className="speaker-sample">“{seen.sample.slice(0, 90)}…”</p>}

      <div className="speaker-actions">
        {QUICK_ROLES.map((r) => (
          <button
            key={r}
            className={role === r ? 'on' : ''}
            onClick={() => onAssign(index, r)}
          >
            {r.replace('DR. ', '').replace('NURSE ', '')}
          </button>
        ))}
        <input
          type="text"
          placeholder="other…"
          value={custom}
          onChange={(e) => setCustom(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && custom.trim()) {
              onAssign(index, custom.trim());
              setCustom('');
            }
          }}
        />
      </div>
    </div>
  );
}

export default function SpeakerRoles({ speakers, speakerRoles, onAssign }) {
  const indices = Object.keys(speakers)
    .map(Number)
    .sort((a, b) => a - b);

  if (indices.length === 0) return null;

  const unassigned = indices.filter((i) => !speakerRoles[String(i)]).length;

  return (
    <div className="speaker-roles">
      <div className="speaker-roles-head">
        <h4>Voices</h4>
        <span className="speaker-roles-sub">who is who — assign once per voice</span>
        {unassigned > 0 && (
          <span className="speaker-roles-warn">{unassigned} unassigned</span>
        )}
      </div>

      {indices.map((i) => (
        <SpeakerRow
          key={i}
          index={i}
          seen={speakers[i]}
          role={speakerRoles[String(i)] || ''}
          onAssign={onAssign}
        />
      ))}
    </div>
  );
}
