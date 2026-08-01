import { useCallback, useEffect, useRef, useState } from 'react';
import { startAudioCapture, stopAudioCapture } from '../lib/audioCapture';

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

function playBase64Audio(b64, mime) {
  try {
    const byteChars = atob(b64);
    const byteNumbers = new Array(byteChars.length);
    for (let i = 0; i < byteChars.length; i++) byteNumbers[i] = byteChars.charCodeAt(i);
    const blob = new Blob([new Uint8Array(byteNumbers)], { type: mime });
    const audio = new Audio(URL.createObjectURL(blob));
    audio.play().catch((e) => console.warn('agent audio playback blocked', e));
  } catch (err) {
    console.warn('failed to decode/play agent audio', err);
  }
}

export function useCaseSocket(caseId) {
  const [status, setStatus] = useState('connecting'); // connecting | recording | closed | error
  const [transcript, setTranscript] = useState('');
  const [interim, setInterim] = useState('');
  const [structured, setStructured] = useState(emptyStructured);
  const [alerts, setAlerts] = useState([]);
  const [videoStream, setVideoStream] = useState(null);
  // The agent asking the room who owns something. Transient — clears on answer.
  const [unownedPrompt, setUnownedPrompt] = useState(null);
  const [handoffBrief, setHandoffBrief] = useState(null);
  const [handoffLoading, setHandoffLoading] = useState(false);

  const wsRef = useRef(null);
  const audioCaptureRef = useRef(null);
  const mediaStreamRef = useRef(null);

  useEffect(() => {
    let cancelled = false;

    function handleMessage(msg) {
      if (msg.type === 'transcript') {
        if (msg.is_final) {
          setTranscript((prev) => (prev ? `${prev} ${msg.text}` : msg.text));
          setInterim('');
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
        setAlerts((prev) => [...prev, msg.alert]);
        if (msg.audio_b64) playBase64Audio(msg.audio_b64, msg.audio_mime || 'audio/mpeg');
      } else if (msg.type === 'unowned_prompt') {
        setUnownedPrompt(msg);
        if (msg.audio_b64) playBase64Audio(msg.audio_b64, msg.audio_mime || 'audio/mpeg');
      } else if (msg.type === 'handoff') {
        setHandoffBrief(msg.brief);
        setHandoffLoading(false);
        if (msg.audio_b64) playBase64Audio(msg.audio_b64, msg.audio_mime || 'audio/mpeg');
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
      if (mediaStreamRef.current) mediaStreamRef.current.getTracks().forEach((t) => t.stop());
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) wsRef.current.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseId]);

  const endCase = useCallback(() => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'end_case' }));
      ws.close();
    }
    stopAudioCapture(audioCaptureRef.current);
    audioCaptureRef.current = null;
    if (mediaStreamRef.current) mediaStreamRef.current.getTracks().forEach((t) => t.stop());
    setStatus('closed');
  }, []);

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
    unownedPrompt,
    handoffBrief,
    handoffLoading,
    endCase,
    requestHandoff,
    dismissHandoff: () => setHandoffBrief(null),
  };
}
