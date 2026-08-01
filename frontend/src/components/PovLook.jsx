/**
 * Point-of-view frame: what the wearer is looking at, and what the agent made
 * of it.
 *
 * The frame is shown next to the reading on purpose. Everything else in this
 * system is recorded with a source you can check; a value read off a screen
 * can't be, so the check is the picture itself. Nothing here is written into
 * the record — readings are labelled unconfirmed and the agent asks the room
 * rather than documenting an answer.
 */

const LEGIBILITY_LABEL = {
  clear: 'legible',
  partial: 'partly legible',
  guessing: 'unclear',
};

export default function PovLook({ lastLook, looking, onLook, disabled, error }) {
  const obs = lastLook?.observation;
  const readings = obs?.readings || [];
  const people = obs?.people || [];

  return (
    <div className="pov-look">
      <div className="pov-look-head">
        <h4>Point of view</h4>
        <span className="pov-proxy">webcam proxy · ships on Ray-Ban Meta</span>
      </div>

      <button className="look-btn" onClick={onLook} disabled={disabled || looking}>
        {looking ? 'Looking…' : '◉ Look now'}
      </button>

      {error && <p className="pov-error">{error}</p>}

      {obs && (
        <div className="pov-result">
          {lastLook.dataUrl && (
            <img className="pov-frame" src={lastLook.dataUrl} alt="captured point-of-view frame" />
          )}

          <div className="pov-scene">
            <span className={`pov-confidence ${obs.confidence}`}>{obs.confidence}</span>
            {obs.scene}
          </div>

          {obs.description && <p className="pov-description">{obs.description}</p>}

          {readings.length > 0 && (
            <div className="pov-readings">
              <div className="pov-readings-head">
                Unconfirmed — not recorded
              </div>
              {readings.map((r, i) => (
                <div className="pov-reading" key={i}>
                  <span className="pov-reading-label">{r.label}</span>
                  <span className="pov-reading-value">{r.value}</span>
                  <span className={`pov-legibility ${r.legibility}`}>
                    {LEGIBILITY_LABEL[r.legibility] || r.legibility}
                  </span>
                </div>
              ))}
            </div>
          )}

          {people.length > 0 && (
            <ul className="pov-people">
              {people.map((p, i) => (
                <li key={i}>{p}</li>
              ))}
            </ul>
          )}

          {obs.prompt_the_room && (
            <p className="pov-prompt">“{obs.prompt_the_room}”</p>
          )}
        </div>
      )}
    </div>
  );
}
