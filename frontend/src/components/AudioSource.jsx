import { useRef } from 'react';

/**
 * Choose what feeds the pipeline: the room, or a rehearsed clip.
 *
 * Both go down the same WebSocket to the same Deepgram session — the clip is
 * not a simulation, it's just audio that isn't coming from a microphone. The
 * badge says so plainly, because a demo that hides which one it's using is the
 * kind of thing a judge finds out about at the worst moment.
 */
export default function AudioSource({ inputMode, playback, onModeChange, onPlayFile, onStop }) {
  const fileRef = useRef(null);

  const pct = playback ? Math.round(playback.progress * 100) : 0;
  const playing = Boolean(playback) && pct < 100;

  return (
    <div className="audio-source">
      <div className="audio-source-head">
        <h4>Audio in</h4>
        <span className={`source-badge ${inputMode}`}>
          {inputMode === 'mic' ? 'LIVE MIC' : 'CLIP'}
        </span>
      </div>

      <div className="audio-source-modes">
        <button
          className={inputMode === 'mic' ? 'on' : ''}
          onClick={() => onModeChange('mic')}
        >
          Live mic
        </button>
        <button
          className={inputMode === 'file' ? 'on' : ''}
          onClick={() => onModeChange('file')}
        >
          Clip
        </button>
      </div>

      {inputMode === 'file' && (
        <div className="audio-source-file">
          <input
            ref={fileRef}
            type="file"
            accept="audio/*"
            style={{ display: 'none' }}
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) onPlayFile(file);
              e.target.value = '';
            }}
          />

          {!playing ? (
            <button className="play-btn" onClick={() => fileRef.current?.click()}>
              ▶ Choose clip and play
            </button>
          ) : (
            <button className="stop-btn" onClick={onStop}>
              ■ Stop
            </button>
          )}

          {playback && (
            <div className="playback-status">
              <div className="playback-name">{playback.name}</div>
              <div className="playback-bar">
                <div className="playback-fill" style={{ width: `${pct}%` }} />
              </div>
              <div className="playback-meta">
                {pct}%
                {playback.duration ? ` · ${playback.duration.toFixed(0)}s` : ''}
                {pct >= 100 ? ' · finished' : ''}
              </div>
            </div>
          )}

          <p className="audio-source-note">
            Plays out loud and streams the same audio to Deepgram. The mic stays
            closed so nothing is captured twice.
          </p>
        </div>
      )}
    </div>
  );
}
