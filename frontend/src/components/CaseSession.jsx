import { useEffect, useState } from 'react';
import { useCaseSocket } from '../hooks/useCaseSocket';
import VideoPreview from './VideoPreview';
import Ledger from './Ledger';
import HandoffPanel from './HandoffPanel';
import SpeakerRoles from './SpeakerRoles';
import AudioSource from './AudioSource';
import PovLook from './PovLook';

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
  const {
    status,
    transcript,
    interim,
    structured,
    alerts,
    videoStream,
    speakers,
    speakerRoles,
    unownedPrompt,
    handoffBrief,
    handoffLoading,
    inputMode,
    playback,
    lastLook,
    looking,
    look,
    endCase,
    assignSpeakerRole,
    requestHandoff,
    setInputMode,
    playFile,
    stopPlayback,
    dismissHandoff,
  } = useCaseSocket(caseId, onAgentStep);

  // Only the current alert is shown. Reasoning stays collapsed by default and
  // re-collapses when a new alert arrives, so the banner never grows into a
  // wall of text mid-case.
  const [showReasoning, setShowReasoning] = useState(false);
  const latestAlert = alerts.length ? alerts[alerts.length - 1] : null;
  const earlierAlerts = Math.max(0, alerts.length - 1);

  useEffect(() => {
    setShowReasoning(false);
  }, [latestAlert?.id]);

  return (
    <div className={`case-session ${active ? '' : 'hidden'} ${status === 'closed' ? 'case-closed' : ''}`}>
      {latestAlert && (
        <div className="alert-stack">
          {/* Only the current alert, and only the line that was spoken. The
              reasoning is long by design and already streams to the Agent Log;
              putting it here stacked three deep buried the thing the clinician
              actually needs to read. It then clears itself once the room has
              actually heard it, so a stale warning never sits over a live case. */}
          <div
            className={`alert-banner urgency-${latestAlert.urgency || 'critical'}${
              latestAlert.leaving ? ' leaving' : ''
            }`}
          >
            <div className="alert-head">
              <span className="alert-text">⚠️ {latestAlert.text}</span>
              {latestAlert.reasoning && (
                <button
                  className="alert-why"
                  onClick={() => setShowReasoning((v) => !v)}
                  aria-expanded={showReasoning}
                >
                  {showReasoning ? 'hide why' : 'why'}
                </button>
              )}
            </div>

            <div className="alert-provenance">
              {/* The two tiers are labelled differently on purpose: one cites
                  the FDA, the other cites itself. */}
              {latestAlert.kind === 'verified_conflict' && latestAlert.fda_verified ? (
                <>
                  <span className="fda-stamp">FDA VERIFIED</span>
                  <span>
                    {(latestAlert.fda_classes || [])
                      .filter((c) => c.startsWith('EPC:'))
                      .map((c) => c.replace('EPC: ', ''))
                      .join(' · ')}
                  </span>
                  <span className="fda-source">NIH RxNav</span>
                </>
              ) : latestAlert.kind === 'verified_conflict' ? (
                <>
                  <span className="fda-stamp unverified">UNVERIFIED CLASS</span>
                  <span>no FDA classification returned</span>
                </>
              ) : (
                <>
                  <span className="fda-stamp">{(latestAlert.urgency || 'advisory').toUpperCase()}</span>
                  <span className="fda-source">agent reasoning</span>
                </>
              )}

              {earlierAlerts > 0 && (
                <span className="alert-earlier">
                  {earlierAlerts} earlier in Agent Log
                </span>
              )}
            </div>

            {showReasoning && latestAlert.reasoning && (
              <p className="alert-reasoning">{latestAlert.reasoning}</p>
            )}
          </div>
        </div>
      )}

      {unownedPrompt && (
        <div className="unowned-banner">
          <span className="unowned-mark">◎</span>
          <span className="unowned-text">{unownedPrompt.text}</span>
          <span className="unowned-note">spoken to the room</span>
        </div>
      )}

      {handoffBrief && <HandoffPanel brief={handoffBrief} onClose={dismissHandoff} />}

      <div className="case-session-grid">
        <div className="video-column">
          <VideoPreview stream={videoStream} />
          <div className="case-controls">
            <StatusBadge status={status} />
            <button className="handoff-btn" onClick={requestHandoff} disabled={handoffLoading}>
              {handoffLoading ? 'Briefing…' : 'Handoff'}
            </button>
            {status !== 'closed' && (
              <button className="end-case-btn" onClick={endCase}>
                End Case
              </button>
            )}
          </div>

          <PovLook
            lastLook={lastLook}
            looking={looking}
            onLook={look}
            disabled={status === 'closed'}
          />

          <AudioSource
            inputMode={inputMode}
            playback={playback}
            onModeChange={setInputMode}
            onPlayFile={playFile}
            onStop={stopPlayback}
          />

          <SpeakerRoles
            speakers={speakers}
            speakerRoles={speakerRoles}
            onAssign={assignSpeakerRole}
          />
        </div>

        <div className="transcript-column">
          <h4>Live Transcript</h4>
          <div className="transcript-box">
            <span>{transcript}</span>
            {interim && <span className="interim"> {interim}</span>}
            {!transcript && !interim && <span className="muted">Listening…</span>}
          </div>

          <Ledger work={structured.work} />
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
