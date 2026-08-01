import { useCallback, useEffect, useRef, useState } from 'react';
import { startAudioCapture, stopAudioCapture } from '../lib/audioCapture';
import { startFilePlayback } from '../lib/filePlayback';
import { createFrameGrabber } from '../lib/frameCapture';
import { enqueueAlert } from '../lib/audioQueue';
import { getSavedInput, saveInput, saveOutput } from '../lib/audioDevices';

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
  // The last POV frame the agent looked at, with what it saw. Kept so a human
  // can check the picture against what was said about it.
  const [lastLook, setLastLook] = useState(null);
  const [looking, setLooking] = useState(false);
  const [lookError, setLookError] = useState('');

  const wsRef = useRef(null);
  const audioCaptureRef = useRef(null);
  const mediaStreamRef = useRef(null);
  const grabberRef = useRef(null);
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
    const dismissTimers = new Set();

    // A banner clears itself once the room has actually heard it, rather than
    // sitting on screen until something else replaces it. Two steps, because
    // dropping it from state outright would make it vanish instead of fade.
    function dismissAlert(alertId) {
      if (cancelled) return;
      setAlerts((prev) => prev.map((a) => (a.id === alertId ? { ...a, leaving: true } : a)));
      const timer = setTimeout(() => {
        dismissTimers.delete(timer);
        if (cancelled) return;
        setAlerts((prev) => prev.filter((a) => a.id !== alertId));
      }, 320);
      dismissTimers.add(timer);
    }

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
          onSpoken: () => dismissAlert(msg.alert.id),
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
        // Honour a previously chosen microphone — the glasses, when paired.
        // `ideal` rather than `exact` so a device that has since disconnected
        // falls back to the default instead of failing the whole case open.
        const savedInput = getSavedInput();
        const media = await navigator.mediaDevices.getUserMedia({
          audio: savedInput ? { deviceId: { ideal: savedInput } } : true,
          video: true,
        });
        if (cancelled) {
          media.getTracks().forEach((t) => t.stop());
          return;
        }
        mediaStreamRef.current = media;
        setVideoStream(media);
        // Hold a grabber on the stream so a capture is instant rather than
        // spinning up a video element at the moment someone needs an answer.
        grabberRef.current = createFrameGrabber(media);

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
      dismissTimers.forEach(clearTimeout);
      dismissTimers.clear();
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

  /**
   * Look at what the wearer is looking at, right now.
   *
   * Deliberately on demand rather than a background loop. A frame is only
   * interesting at a moment somebody cares about — and continuously shipping
   * the room to a model is both wasteful and a much harder thing to defend.
   */
  const look = useCallback(async () => {
    if (looking) return;

    // Every failure below used to return silently, which on stage looks
    // identical to a button that does nothing. Say what went wrong instead.
    const grabber = grabberRef.current;
    if (!grabber) {
      setLookError('No camera. Grant camera access and reopen the case.');
      return;
    }

    setLooking(true);
    setLookError('');
    try {
      const frame = await grabber.capture();
      if (!frame) {
        setLookError('Camera not ready yet — try again in a moment.');
        return;
      }

      const res = await fetch(`${API_BASE}/api/observe`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          case_id: caseId,
          image_b64: frame.base64,
          media_type: 'image/jpeg',
        }),
      });
      if (!res.ok) {
        setLookError(`Backend returned ${res.status}.`);
        return;
      }

      const observation = await res.json();
      if (observation.error) {
        setLookError(observation.error);
        return;
      }

      // Keep the picture next to the claim so the reading can be checked
      // against what was actually on screen.
      setLastLook({ dataUrl: frame.dataUrl, observation, at: Date.now() });
    } catch (err) {
      console.error('look failed', err);
      setLookError('Could not reach the backend.');
    } finally {
      setLooking(false);
    }
  }, [caseId, looking]);

  /**
   * Switch which microphone feeds the room audio — the glasses, once paired.
   *
   * Swaps the audio track in place rather than reopening the case: the
   * WebSocket, the Deepgram session and the ledger all stay exactly where they
   * are, so changing device mid-case costs nothing.
   */
  const setAudioInput = useCallback(async (deviceId) => {
    saveInput(deviceId);

    const ws = wsRef.current;
    const media = mediaStreamRef.current;
    if (!ws || !media) return;

    try {
      const next = await navigator.mediaDevices.getUserMedia({
        audio: deviceId ? { deviceId: { ideal: deviceId } } : true,
      });

      stopAudioCapture(audioCaptureRef.current);
      audioCaptureRef.current = null;
      media.getAudioTracks().forEach((t) => {
        media.removeTrack(t);
        t.stop();
      });
      next.getAudioTracks().forEach((t) => media.addTrack(t));

      // Only reopen the mic if it was open — in clip mode it stays shut.
      if (inputModeRef.current === 'mic') {
        audioCaptureRef.current = await startAudioCapture(media, (buf) => {
          if (ws.readyState === WebSocket.OPEN) ws.send(buf);
        });
      }
    } catch (err) {
      console.error('failed to switch microphone', err);
    }
  }, []);

  const setAudioOutput = useCallback((deviceId) => {
    // Applied per utterance in the audio queue — nothing to restart.
    saveOutput(deviceId);
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
    lastLook,
    looking,
    lookError,
    look,
    setAudioInput,
    setAudioOutput,
    endCase,
    assignSpeakerRole,
    requestHandoff,
    setInputMode,
    playFile,
    stopPlayback,
    dismissHandoff: () => setHandoffBrief(null),
  };
}
