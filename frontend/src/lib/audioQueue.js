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

// Serializing alerts against each other is only half the problem: a clip
// playing the scenario out loud is a second audio source this queue never saw,
// so the agent talked over it. Anything else making noise subscribes here and
// gets out of the way while the agent speaks.
const speakingListeners = new Set();

function setSpeaking(value) {
  if (isPlaying === value) return;
  isPlaying = value;
  for (const listener of speakingListeners) {
    try {
      listener(value);
    } catch (err) {
      console.warn('speaking listener failed', err);
    }
  }
}

/**
 * Subscribe to "the agent is speaking right now". Returns an unsubscribe fn.
 * Used by clip playback to duck itself rather than pause, since the PCM stream
 * to Deepgram has to keep flowing at real-time pace regardless.
 */
export function onSpeakingChange(listener) {
  speakingListeners.add(listener);
  listener(isPlaying);
  return () => speakingListeners.delete(listener);
}

export function isSpeaking() {
  return isPlaying;
}

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

  // onSpoken must fire exactly once per item. onended and onerror can both land
  // on the same element, and a caller using this to dismiss a banner would
  // otherwise schedule the same removal twice.
  let spokenFired = false;
  const fireSpoken = () => {
    if (spokenFired) return;
    spokenFired = true;
    try {
      next.onSpoken?.();
    } catch (err) {
      console.warn('onSpoken handler failed', err);
    }
  };

  if (!next.audioB64) {
    // Nothing to actually speak (TTS unavailable/failed). This item's turn in the
    // queue is already over, so say so now rather than leaving a caller waiting
    // on an event that will never come.
    fireSpoken();
    playNext();
    return;
  }

  setSpeaking(true);
  let url;
  try {
    url = decodeAudio(next.audioB64, next.audioMime);
  } catch (err) {
    console.warn('failed to decode queued alert audio', err);
    setSpeaking(false);
    fireSpoken();
    playNext();
    return;
  }

  const audio = new Audio(url);
  const finish = () => {
    URL.revokeObjectURL(url);
    setSpeaking(false);
    fireSpoken();
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
 * errors, is blocked, fails to decode, or (when there is no audio at all) right
 * away — so a caller can tie something like dismissing a banner to this one
 * alert's own playback, rather than to the state of the queue in general.
 */
export function enqueueAlert({
  id,
  caseId,
  caseStatus,
  urgency,
  seq,
  timestamp,
  audioB64,
  audioMime,
  onSpoken,
}) {
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
