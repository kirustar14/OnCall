// Global (app-wide, not per-case) priority queue for spoken alert audio. There is
// only one physical speaker output, so even though alerts are generated per case,
// playback has to be serialized across the whole app — this is the single point
// that does that.
//
// Priority order when picking what plays next:
//   1. urgency: critical > advisory > informational
//   2. seq (a global monotonic counter from the backend) — earlier-triggered first
//   3. case status — an open case's alert beats a closed case's, as a final tiebreak
//
// The queue is re-sorted every time a slot opens up (not just on insert), so a
// critical item that arrives mid-speech naturally lands at the front for the *next*
// play — without ever cutting off audio that's already playing.

const URGENCY_RANK = { critical: 0, advisory: 1, informational: 2 };

let queue = [];
let isPlaying = false;

function comparePriority(a, b) {
  const rankA = URGENCY_RANK[a.urgency] ?? URGENCY_RANK.advisory;
  const rankB = URGENCY_RANK[b.urgency] ?? URGENCY_RANK.advisory;
  if (rankA !== rankB) return rankA - rankB;

  const seqA = a.seq ?? a.timestamp ?? 0;
  const seqB = b.seq ?? b.timestamp ?? 0;
  if (seqA !== seqB) return seqA - seqB;

  const openA = a.caseStatus !== 'closed';
  const openB = b.caseStatus !== 'closed';
  if (openA !== openB) return openA ? -1 : 1;

  return 0;
}

function decodeAudio(base64, mime) {
  const byteChars = atob(base64);
  const byteNumbers = new Array(byteChars.length);
  for (let i = 0; i < byteChars.length; i++) byteNumbers[i] = byteChars.charCodeAt(i);
  const blob = new Blob([new Uint8Array(byteNumbers)], { type: mime || 'audio/mpeg' });
  return URL.createObjectURL(blob);
}

function playNext() {
  if (isPlaying || queue.length === 0) return;

  queue.sort(comparePriority);
  const next = queue.shift();

  if (!next.audioB64) {
    // Nothing to actually speak (TTS unavailable/failed) — this alert's turn in
    // the audio queue is effectively already "over," so tell the caller right
    // away rather than leaving its banner waiting on an event that will never fire.
    next.onSpoken?.();
    playNext();
    return;
  }

  isPlaying = true;
  let url;
  try {
    url = decodeAudio(next.audioB64, next.audioMime);
  } catch (err) {
    console.warn('failed to decode queued alert audio', err);
    isPlaying = false;
    next.onSpoken?.();
    playNext();
    return;
  }

  const audio = new Audio(url);
  const finish = () => {
    URL.revokeObjectURL(url);
    isPlaying = false;
    next.onSpoken?.();
    playNext();
  };
  audio.onended = finish;
  audio.onerror = finish;
  audio.play().catch((err) => {
    console.warn('alert audio playback blocked', err);
    finish();
  });
}

/**
 * Add a spoken alert to the queue. Safe to call as often as alerts arrive — only
 * one will ever play at a time, in priority order. Purely additive: does not
 * affect anything visual (banners/Agent Log render immediately regardless).
 *
 * `onSpoken`, if given, fires exactly once for THIS item — when its audio ends,
 * errors, is blocked, fails to decode, or (if there's no audio at all) right
 * away — so a caller can tie something (like dismissing a banner) to this one
 * alert's own playback lifecycle rather than to the queue in general.
 */
export function enqueueAlert({ id, caseId, caseStatus, urgency, seq, timestamp, audioB64, audioMime, onSpoken }) {
  if (!audioB64) {
    onSpoken?.();
    return;
  }
  queue.push({
    id,
    caseId,
    caseStatus,
    urgency: urgency || 'advisory',
    seq,
    timestamp,
    audioB64,
    audioMime,
    onSpoken,
  });
  playNext();
}
