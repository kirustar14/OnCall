// Downsamples the mic's native sample rate to 16kHz mono PCM16 and posts
// ~250ms chunks back to the main thread as ArrayBuffers, ready to send to
// Deepgram's streaming STT (encoding=linear16, sample_rate=16000).
class PCMWorkletProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.targetSampleRate = 16000;
    this.ratio = sampleRate / this.targetSampleRate;
    this.pending = [];
    this.chunkSamples = Math.floor(this.targetSampleRate * 0.25);
    this._carry = 0;
  }

  process(inputs) {
    const input = inputs[0];
    const channel = input && input[0];
    if (!channel) return true;

    for (let i = this._carry; i < channel.length; i += this.ratio) {
      const idx = Math.floor(i);
      if (idx < channel.length) {
        this.pending.push(channel[idx]);
      }
    }
    this._carry = (this._carry + channel.length) % this.ratio;

    while (this.pending.length >= this.chunkSamples) {
      const chunk = this.pending.splice(0, this.chunkSamples);
      const int16 = new Int16Array(chunk.length);
      for (let i = 0; i < chunk.length; i++) {
        const s = Math.max(-1, Math.min(1, chunk[i]));
        int16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
      }
      this.port.postMessage(int16.buffer, [int16.buffer]);
    }

    return true;
  }
}

registerProcessor('pcm-worklet-processor', PCMWorkletProcessor);
