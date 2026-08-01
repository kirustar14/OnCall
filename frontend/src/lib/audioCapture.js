// Captures mic audio from a MediaStream and streams downsampled PCM16 chunks
// to a callback. Swappable input source: pass any MediaStream (laptop mic
// today, glasses mic later) — nothing here is tied to getUserMedia.
export async function startAudioCapture(mediaStream, onChunk) {
  const audioContext = new AudioContext();
  await audioContext.audioWorklet.addModule('/pcm-worklet-processor.js');

  const source = audioContext.createMediaStreamSource(mediaStream);
  const workletNode = new AudioWorkletNode(audioContext, 'pcm-worklet-processor');

  workletNode.port.onmessage = (event) => {
    onChunk(event.data);
  };

  source.connect(workletNode);
  // Intentionally not connected to destination — we don't want to play back
  // the mic locally.

  return { audioContext, source, workletNode };
}

export function stopAudioCapture(capture) {
  if (!capture) return;
  const { audioContext, source, workletNode } = capture;
  try {
    workletNode.port.onmessage = null;
    source.disconnect();
    workletNode.disconnect();
    audioContext.close();
  } catch (err) {
    console.warn('error stopping audio capture', err);
  }
}
