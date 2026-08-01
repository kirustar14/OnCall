import { useCallback, useEffect, useRef, useState } from 'react';
import { startAudioCapture, stopAudioCapture } from '../lib/audioCapture';
import { enqueueAlert } from '../lib/audioQueue';

const WS_BASE = import.meta.env.VITE_WS_BASE || `ws://${window.location.hostname}:8000`;

const emptyStructured = {
  vitals: [],
  allergies: [],
  medications: [],
  notes: [],
  case_details: {},
};

export function useCaseSocket(caseId, onAgentStep) {
  const [status, setStatus] = useState('connecting'); // connecting | recording | closed | error
  const [transcript, setTranscript] = useState('');
  const [interim, setInterim] = useState('');
  const [structured, setStructured] = useState(emptyStructured);
  const [alerts, setAlerts] = useState([]);
  const [videoStream, setVideoStream] = useState(null);

  const wsRef = useRef(null);
  const audioCaptureRef = useRef(null);
  const mediaStreamRef = useRef(null);
  // Kept in refs (not dependencies) so a new onAgentStep identity, or a status
  // change, doesn't tear down and reconnect the WebSocket/audio capture. statusRef
  // gives handleMessage a live read of the current status for the audio queue's
  // open-case-wins tiebreak, without making the connect effect depend on status.
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
        } else {
          setInterim(msg.text);
        }
      } else if (msg.type === 'case_data') {
        setStructured(msg.data);
      } else if (msg.type === 'alert') {
        // Visual always shows immediately — only the spoken audio is serialized
        // through the priority queue.
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
      } else if (msg.type === 'agent_step') {
        onAgentStepRef.current?.(msg);
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

  return { status, transcript, interim, structured, alerts, videoStream, endCase };
}
