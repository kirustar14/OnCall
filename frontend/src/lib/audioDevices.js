// Choose which ears and which mouth.
//
// Ray-Ban Meta pairs as an ordinary Bluetooth audio device: its microphones
// become a system input and its open-ear speakers a system output. That is the
// whole integration for the audio half — no SDK, no native app, no Device
// Access Toolkit. Select the glasses here and the agent genuinely hears through
// them and speaks into the wearer's ear.
//
// A picker rather than the OS default, because on stage "whatever macOS decided
// was the default output" is not something you want to find out about live.

const OUTPUT_KEY = 'oncall.audio.output';
const INPUT_KEY = 'oncall.audio.input';

/** Device labels stay blank until mic permission has been granted once. */
export async function listAudioDevices() {
  if (!navigator.mediaDevices?.enumerateDevices) return { inputs: [], outputs: [] };

  const devices = await navigator.mediaDevices.enumerateDevices();
  const pick = (kind) =>
    devices
      .filter((d) => d.kind === kind && d.deviceId)
      .map((d, i) => ({
        deviceId: d.deviceId,
        label: d.label || `${kind === 'audioinput' ? 'Input' : 'Output'} ${i + 1}`,
      }));

  return { inputs: pick('audioinput'), outputs: pick('audiooutput') };
}

/** Heuristic used only to preselect — never to claim a device is the glasses. */
export function looksLikeGlasses(label) {
  return /ray-?ban|meta|glasses/i.test(label || '');
}

export function getSavedInput() {
  return localStorage.getItem(INPUT_KEY) || '';
}

export function getSavedOutput() {
  return localStorage.getItem(OUTPUT_KEY) || '';
}

export function saveInput(deviceId) {
  if (deviceId) localStorage.setItem(INPUT_KEY, deviceId);
  else localStorage.removeItem(INPUT_KEY);
}

export function saveOutput(deviceId) {
  if (deviceId) localStorage.setItem(OUTPUT_KEY, deviceId);
  else localStorage.removeItem(OUTPUT_KEY);
}

/**
 * Route an <audio> element to a specific output.
 *
 * setSinkId is Chromium-only and needs a secure context. It is also the only
 * way to send the agent's voice to the glasses while the scenario clip keeps
 * playing to the room — so when it is unavailable we say so rather than
 * silently playing everything through the laptop.
 */
export async function routeToOutput(audioEl, deviceId) {
  if (!deviceId || typeof audioEl.setSinkId !== 'function') return false;
  try {
    await audioEl.setSinkId(deviceId);
    return true;
  } catch (err) {
    console.warn('could not route audio to selected output', err);
    return false;
  }
}

export function outputRoutingSupported() {
  return typeof HTMLMediaElement !== 'undefined' &&
    typeof HTMLMediaElement.prototype.setSinkId === 'function';
}
