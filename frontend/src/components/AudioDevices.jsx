import { useCallback, useEffect, useState } from 'react';
import {
  getSavedInput,
  getSavedOutput,
  listAudioDevices,
  looksLikeGlasses,
  outputRoutingSupported,
} from '../lib/audioDevices';

/**
 * Pick the ears and the mouth.
 *
 * Ray-Ban Meta pairs as an ordinary Bluetooth audio device — its microphones
 * are a system input, its open-ear speakers a system output. Selecting them
 * here is the entire integration for the audio half: the agent hears the room
 * through the glasses and answers into the wearer's ear, with no SDK and no
 * native app in between.
 *
 * A picker rather than the OS default, because "whatever the laptop decided was
 * the default output" is not a thing to discover on stage.
 */
export default function AudioDevices({ onInputChange, onOutputChange }) {
  const [devices, setDevices] = useState({ inputs: [], outputs: [] });
  const [input, setInput] = useState(getSavedInput());
  const [output, setOutput] = useState(getSavedOutput());

  const refresh = useCallback(async () => {
    setDevices(await listAudioDevices());
  }, []);

  useEffect(() => {
    refresh();
    // Devices come and go — pairing the glasses mid-session should just show up.
    navigator.mediaDevices?.addEventListener?.('devicechange', refresh);
    return () => navigator.mediaDevices?.removeEventListener?.('devicechange', refresh);
  }, [refresh]);

  const glassesSeen =
    devices.inputs.some((d) => looksLikeGlasses(d.label)) ||
    devices.outputs.some((d) => looksLikeGlasses(d.label));

  const routingOk = outputRoutingSupported();

  return (
    <div className="audio-devices">
      <div className="audio-devices-head">
        <h4>Devices</h4>
        {glassesSeen && <span className="glasses-badge">GLASSES PAIRED</span>}
      </div>

      <label className="device-row">
        <span className="device-label">Hears through</span>
        <select
          className="device-select"
          value={input}
          onChange={(e) => {
            setInput(e.target.value);
            onInputChange(e.target.value);
          }}
        >
          <option value="">System default</option>
          {devices.inputs.map((d) => (
            <option key={d.deviceId} value={d.deviceId}>
              {d.label}
            </option>
          ))}
        </select>
      </label>

      <label className="device-row">
        <span className="device-label">Speaks into</span>
        <select
          className="device-select"
          value={output}
          disabled={!routingOk}
          onChange={(e) => {
            setOutput(e.target.value);
            onOutputChange(e.target.value);
          }}
        >
          <option value="">System default</option>
          {devices.outputs.map((d) => (
            <option key={d.deviceId} value={d.deviceId}>
              {d.label}
            </option>
          ))}
        </select>
      </label>

      {!routingOk && (
        <p className="device-note">
          This browser can’t route audio per-element — the agent will use the system
          output. Chrome supports it.
        </p>
      )}

      {devices.inputs.some((d) => !d.label) && (
        <p className="device-note">Device names appear once microphone access is granted.</p>
      )}
    </div>
  );
}
