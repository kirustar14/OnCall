import { useCallback, useEffect, useRef, useState } from 'react';
import { startAudioCapture, stopAudioCapture } from '../lib/audioCapture';
import { startFilePlayback } from '../lib/filePlayback';
import { enqueueAlert } from '../lib/audioQueue';

const WS_BASE = import.meta.env.VITE_WS_BASE || `ws://${window.location.hostname}:8000`;
const API_BASE = import.meta.env.VITE_API_BASE || `http://${window.location.hostname}:8000`;

const emptyStructured = {
  vitals: [],
  allergies: [],
  medications: [],
  notes: [],
  case_details: {},
  work: [],
};

// Everything the system says out loud goes through one queue. There is a single
// speaker, and a watchdog prompt talking over a critical contraindication is how
// a room learns to ignore both.
let localSeq = 1e9; // ordering for client-side utterances that carry no server seq

export function useCaseSocket(caseId, onAgentStep) {
  const [status, setStatus] = useState('connecting'); // connecting | recording | closed | error
  const [transcript, setTranscript] = useState('');
  const [interim, setInterim] = useState('');
  const [structured, setStructured] = useState(emptyStructured);
  const [alerts, setAlerts] = useState([]);
  const [videoStream, setVideoStream] = useState(null);
  // Diarization indices seen so far, with a sample of what each said — you map
  // a voice to a role by recognising the line, not by guessing at a number.
  const [speakers, setSpeakers] = useState({});
  // The agent asking the room who owns something. Transient — clears on answer.
  const [unownedPrompt, setUnownedPrompt] = useState(null);
  const [handoffBrief, setHandoffBrief] = useState(null);
  const [handoffLoading, setHandoffLoading] = useState(false);
  // 'mic' streams the room. 'file' streams a rehearsed clip through the very
  // same socket — deterministic in a loud venue, and nothing about the pipeline
  // downstream can tell the difference.
  const [inputMode, setInputModeState] = useState('mic');
  const [playback, setPlayback] = useState(null); // { name, progress, duration }

  const wsRef = useRef(null);
  const audioCaptureRef = useRef(null);
  const mediaStreamRef = useRef(null);
  const inputModeRef = useRef('mic');
  const playbackRef = useRef(null);

  // Refs, not dependencies: a new callback identity or a status change must not
  // tear down the socket and reopen the mic mid-case. statusRef also gives the
  // audio queue a live read for its open-case-wins tiebreak.
  const onAgentStepRef = useRef(onAgentStep);
  useEffect(() => {
    onAgentStepRef.current = onAgentStep;
  }, [onAgentStep]);

  const statusRef = useRef(status);
  useEffect(() => {
    statusRef.current = status;
  }, [status]);

  useEffect(() => {
    let cancelled = false;

    function handleMessage(msg) {
      if (msg.type === 'transcript') {
        if (msg.is_final) {
          setTranscript((prev) => (prev ? `${prev} ${msg.text}` : msg.text));
          setInterim('');
          if (typeof msg.speaker_index === 'number') {
            setSpeakers((prev) => {
              const seen = prev[msg.speaker_index] || { count: 0, sample: '' };
              return {
                ...prev,
                [msg.speaker_index]: {
                  count: seen.count + 1,
                  // Keep the first thing they said — it's usually the most
                  // recognisable ("Medic 6 with a nineteen year old female...").
                  sample: seen.sample || msg.text,
                },
              };
            });
          }
        } else {
          setInterim(msg.text);
        }
      } else if (msg.type === 'case_data') {
        setStructured({ ...emptyStructured, ...msg.data });
        // If the item we asked about now has an owner, stop showing the ask.
        setUnownedPrompt((prev) => {
          if (!prev) return prev;
          const item = (msg.data.work || []).find((w) => w.id === prev.work_id);
          return item && (item.owner || item.status !== 'open') ? null : prev;
        });
      } else if (msg.type === 'alert') {
        // The banner appears immediately; only the speech is queued.
        setAlerts((prev) => [...prev, msg.alert]);
        enqueueAlert({
          id: msg.alert.id,
          caseId,
          caseStatus: statusRef.current,
          urgency: msg.alert.urgency,
          seq: msg.alert.seq,
          timestamp: msg.alert.timestamp,
          audioB64: msg.audio_b64,
          audioMime: msg.audio_mime,
        });
      } else if (msg.type === 'unowned_prompt') {
        setUnownedPrompt(msg);
        // Advisory: asking who owns a task must never cut across a
        // contraindication warning.
        enqueueAlert({
          id: msg.work_id,
          caseId,
          caseStatus: statusRef.current,
          urgency: 'advisory',
          seq: localSeq++,
          timestamp: Date.now() / 1000,
          audioB64: msg.audio_b64,
          audioMime: msg.audio_mime,
        });
      } else if (msg.type === 'agent_step') {
        onAgentStepRef.current?.(msg);
      } else if (msg.type === 'handoff') {
        setHandoffBrief(msg.brief);
        setHandoffLoading(false);
        enqueueAlert({
          id: `handoff-${caseId}-${Date.now()}`,
          caseId,
          caseStatus: statusRef.current,
          urgency: 'informational',
          seq: localSeq++,
          timestamp: Date.now() / 1000,
          audioB64: msg.audio_b64,
          audioMime: msg.audio_mime,
        });
      } else if (msg.type === 'status' && msg.status === 'closed') {
        setStatus('closed');
      }
    }

    async function connect() {
      try {
        const media = await navigator.mediaDevices.getUserMedia({ audio: true, video: true });
        if (cancelled) {
          media.getTracks().forEach((t) => t.stop());
          return;
        }
        mediaStreamRef.current = media;
        setVideoStream(media);

        const ws = new WebSocket(`${WS_BASE}/ws/case/${caseId}`);
        ws.binaryType = 'arraybuffer';
        wsRef.current = ws;

        ws.onopen = async () => {
          if (cancelled) return;
          setStatus('recording');
          // In file mode the mic stays closed, so a clip playing out loud can't
          // be picked up twice.
          if (inputModeRef.current !== 'mic') return;
          try {
            audioCaptureRef.current = await startAudioCapture(media, (buf) => {
              if (ws.readyState === WebSocket.OPEN) ws.send(buf);
            });
          } catch (err) {
            console.error('audio capture failed to start', err);
          }
        };

        ws.onmessage = (event) => {
          try {
            handleMessage(JSON.parse(event.data));
          } catch (err) {
            console.warn('bad ws message', err);
          }
        };

        ws.onerror = () => setStatus('error');
        ws.onclose = () => setStatus((s) => (s === 'closed' ? s : 'closed'));
      } catch (err) {
        console.error('failed to acquire camera/mic', err);
        setStatus('error');
      }
    }

    connect();

    return () => {
      cancelled = true;
      stopAudioCapture(audioCaptureRef.current);
      audioCaptureRef.current = null;
      playbackRef.current?.stop();
      playbackRef.current = null;
      if (mediaStreamRef.current) mediaStreamRef.current.getTracks().forEach((t) => t.stop());
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) wsRef.current.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseId]);

  const setInputMode = useCallback(async (mode) => {
    inputModeRef.current = mode;
    setInputModeState(mode);

    if (mode === 'file') {
      stopAudioCapture(audioCaptureRef.current);
      audioCaptureRef.current = null;
      return;
    }

    // Back to the room: stop any clip and reopen the mic.
    playbackRef.current?.stop();
    playbackRef.current = null;
    setPlayback(null);

    const ws = wsRef.current;
    const media = mediaStreamRef.current;
    if (!ws || !media || audioCaptureRef.current) return;
    try {
      audioCaptureRef.current = await startAudioCapture(media, (buf) => {
        if (ws.readyState === WebSocket.OPEN) ws.send(buf);
      });
    } catch (err) {
      console.error('failed to reopen mic', err);
    }
  }, []);

  const playFile = useCallback(async (file) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      console.warn('socket not open; cannot play file');
      return;
    }

    // Never let a clip and the mic feed the socket at once.
    inputModeRef.current = 'file';
    setInputModeState('file');
    stopAudioCapture(audioCaptureRef.current);
    audioCaptureRef.current = null;
    playbackRef.current?.stop();

    setPlayback({ name: file.name, progress: 0, duration: 0 });
    try {
      playbackRef.current = await startFilePlayback(
        file,
        (buf) => {
          if (ws.readyState === WebSocket.OPEN) ws.send(buf);
        },
        {
          audible: true,
          onProgress: (progress, duration) =>
            setPlayback((p) => (p ? { ...p, progress, duration } : p)),
          onEnded: () => setPlayback((p) => (p ? { ...p, progress: 1 } : p)),
        },
      );
    } catch (err) {
      console.error('file playback failed', err);
      setPlayback(null);
    }
  }, []);

  const stopPlayback = useCallback(() => {
    playbackRef.current?.stop();
    playbackRef.current = null;
    setPlayback(null);
  }, []);

  const endCase = useCallback(() => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'end_case' }));
      ws.close();
    }
    stopAudioCapture(audioCaptureRef.current);
    audioCaptureRef.current = null;
    playbackRef.current?.stop();
    playbackRef.current = null;
    if (mediaStreamRef.current) mediaStreamRef.current.getTracks().forEach((t) => t.stop());
    setStatus('closed');
  }, []);

  const assignSpeakerRole = useCallback(
    async (speakerIndex, role) => {
      try {
        await fetch(`${API_BASE}/api/speaker-role`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ case_id: caseId, speaker_index: speakerIndex, role }),
        });
        // The server pushes updated case_data over the socket, which is what
        // actually updates speaker_roles here.
      } catch (err) {
        console.error('speaker role assignment failed', err);
      }
    },
    [caseId],
  );

  const requestHandoff = useCallback(async () => {
    setHandoffLoading(true);
    try {
      // The brief also arrives over the socket with audio; this call is what
      // triggers it, and the response is the fallback if the socket is gone.
      const res = await fetch(`${API_BASE}/api/handoff`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ case_id: caseId }),
      });
      const brief = await res.json();
      if (!brief.error) setHandoffBrief(brief);
    } catch (err) {
      console.error('handoff request failed', err);
    } finally {
      setHandoffLoading(false);
    }
  }, [caseId]);

  return {
    status,
    transcript,
    interim,
    structured,
    alerts,
    videoStream,
    speakers,
    speakerRoles: structured.speaker_roles || {},
    unownedPrompt,
    handoffBrief,
    handoffLoading,
    inputMode,
    playback,
    endCase,
    assignSpeakerRole,
    requestHandoff,
    setInputMode,
    playFile,
    stopPlayback,
    dismissHandoff: () => setHandoffBrief(null),
  };
}
