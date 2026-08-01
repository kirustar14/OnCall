// Streams a pre-recorded clip into the pipeline exactly as the mic would.
//
// A live mic in a loud venue is the biggest single demo risk we have: room
// noise, someone coughing, a phrase not landing, and the run is gone. Playing a
// rehearsed clip through the same WebSocket makes the demo deterministic while
// exercising every real link — Deepgram STT, diarization, extraction, the
// ledger. Nothing is faked; the audio is simply not coming from a microphone.
//
// It plays out loud by default so the room hears the scenario, and streams the
// same audio as 16 kHz mono PCM16 — the format the backend already forwards to
// Deepgram.

import { onSpeakingChange } from './audioQueue';

const TARGET_SAMPLE_RATE = 16000;
const CHUNK_MS = 100;

// How far the scenario drops while the agent is talking. Not silence: the room
// carries on in a real resuscitation, and hearing the agent cut through the
// noise is the point. Ducking rather than pausing also keeps the PCM stream to
// Deepgram flowing at real-time pace, which pausing would break.
const DUCKED_GAIN = 0.12;
const DUCK_RAMP_SECONDS = 0.18;

/** Decode any browser-supported audio file to 16 kHz mono Float32. */
async function decodeTo16kMono(file) {
  const bytes = await file.arrayBuffer();

  // A plain AudioContext decodes at the device rate; render through an
  // OfflineAudioContext to resample properly rather than dropping samples.
  const decodeCtx = new AudioContext();
  let decoded;
  try {
    decoded = await decodeCtx.decodeAudioData(bytes);
  } finally {
    void decodeCtx.close();
  }

  const frames = Math.ceil(decoded.duration * TARGET_SAMPLE_RATE);
  const offline = new OfflineAudioContext(1, frames, TARGET_SAMPLE_RATE);
  const source = offline.createBufferSource();
  source.buffer = decoded;
  source.connect(offline.destination);
  source.start();

  const rendered = await offline.startRendering();
  return { samples: rendered.getChannelData(0), original: decoded };
}

function toPCM16(float32) {
  const out = new Int16Array(float32.length);
  for (let i = 0; i < float32.length; i++) {
    const s = Math.max(-1, Math.min(1, float32[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

/**
 * @param file      an audio File (the generated demo-script.wav, or any clip)
 * @param onChunk   receives ArrayBuffers of PCM16 at real-time pace
 * @param options   { audible = true, onProgress, onEnded }
 * @returns handle with stop() and durationSeconds
 */
export async function startFilePlayback(file, onChunk, options = {}) {
  const { audible = true, onProgress, onEnded } = options;

  const { samples, original } = await decodeTo16kMono(file);
  const pcm = toPCM16(samples);
  const samplesPerChunk = Math.floor((TARGET_SAMPLE_RATE * CHUNK_MS) / 1000);
  const durationSeconds = pcm.length / TARGET_SAMPLE_RATE;

  // Audible playback is a separate path from the stream — the room hears the
  // original file while Deepgram receives the resampled PCM.
  let playbackCtx = null;
  let playbackSource = null;
  let gainNode = null;
  let unsubscribeDuck = null;
  if (audible) {
    playbackCtx = new AudioContext();
    playbackSource = playbackCtx.createBufferSource();
    playbackSource.buffer = original;
    gainNode = playbackCtx.createGain();
    playbackSource.connect(gainNode);
    gainNode.connect(playbackCtx.destination);

    // Duck whenever the agent speaks, restore when it stops. Ramped, because an
    // instant gain change clicks.
    unsubscribeDuck = onSpeakingChange((speaking) => {
      if (!gainNode || !playbackCtx) return;
      const target = speaking ? DUCKED_GAIN : 1;
      const now = playbackCtx.currentTime;
      gainNode.gain.cancelScheduledValues(now);
      gainNode.gain.setValueAtTime(gainNode.gain.value, now);
      gainNode.gain.linearRampToValueAtTime(target, now + DUCK_RAMP_SECONDS);
    });
  }

  let offset = 0;
  let stopped = false;
  let timer = null;

  const cleanup = () => {
    if (timer) clearInterval(timer);
    timer = null;
    unsubscribeDuck?.();
    unsubscribeDuck = null;
    try {
      playbackSource?.stop();
    } catch {
      /* already ended */
    }
    void playbackCtx?.close();
  };

  const stop = () => {
    if (stopped) return;
    stopped = true;
    cleanup();
  };

  // Start the stream and the speakers together so what the room hears matches
  // what the transcript is doing.
  playbackSource?.start();

  timer = setInterval(() => {
    if (stopped) return;

    if (offset >= pcm.length) {
      stop();
      onEnded?.();
      return;
    }

    const slice = pcm.slice(offset, offset + samplesPerChunk);
    offset += samplesPerChunk;
    onChunk(slice.buffer);
    onProgress?.(Math.min(1, offset / pcm.length), durationSeconds);
  }, CHUNK_MS);

  return { stop, durationSeconds };
}
