// Grab a still from the POV video stream.
//
// Ships on Ray-Ban Meta (camera + open-ear audio, no display) via the Meta
// Wearables Device Access Toolkit; in the browser the POV proxy is a webcam.
// The backend receives the same base64 JPEG either way — the model cannot tell
// which lens it came from.
//
// A hidden video element is kept attached to the stream so a capture is
// instant. Building one per capture costs a few hundred ms of load and play,
// which is exactly the wrong place to spend time when someone has just asked
// the room to confirm something.

const MAX_WIDTH = 1024;
const JPEG_QUALITY = 0.82;

// Ring buffer settings. Capture is continuous and entirely local — frames are
// held in memory and thrown away. Nothing is sent anywhere until something
// happens that a frame would explain.
const BUFFER_FPS = 1;
const BUFFER_SECONDS = 180;
// Buffered frames only ever get used to look *back* at a moment, so they can be
// much smaller than an on-demand capture.
const BUFFER_WIDTH = 640;
const BUFFER_QUALITY = 0.7;

export function createFrameGrabber(stream) {
  if (!stream || stream.getVideoTracks().length === 0) return null;

  const video = document.createElement('video');
  video.srcObject = stream;
  video.muted = true;
  video.playsInline = true;
  const ready = video.play().catch(() => {});

  const canvas = document.createElement('canvas');

  return {
    /** @returns {Promise<{ base64: string, width: number, height: number } | null>} */
    async capture() {
      await ready;
      if (!video.videoWidth) return null;

      // Downscale before encoding — a 4K frame is slow to send and the model
      // gains nothing from the extra pixels.
      const scale = Math.min(1, MAX_WIDTH / video.videoWidth);
      canvas.width = Math.round(video.videoWidth * scale);
      canvas.height = Math.round(video.videoHeight * scale);

      const ctx = canvas.getContext('2d');
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

      const dataUrl = canvas.toDataURL('image/jpeg', JPEG_QUALITY);
      return {
        base64: dataUrl.split(',')[1],
        width: canvas.width,
        height: canvas.height,
        dataUrl,
      };
    },

    /** Small frame for the ring buffer — same picture, cheaper. */
    async captureSmall() {
      await ready;
      if (!video.videoWidth) return null;
      const scale = Math.min(1, BUFFER_WIDTH / video.videoWidth);
      canvas.width = Math.round(video.videoWidth * scale);
      canvas.height = Math.round(video.videoHeight * scale);
      canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
      const dataUrl = canvas.toDataURL('image/jpeg', BUFFER_QUALITY);
      return { base64: dataUrl.split(',')[1], dataUrl };
    },

    stop() {
      try {
        video.pause();
        video.srcObject = null;
      } catch {
        /* already torn down */
      }
    },
  };
}

/**
 * Keep the last few minutes of point-of-view frames, in memory, locally.
 *
 * Nobody narrates a glance at a monitor, so by the time something is worth
 * explaining the moment has already passed. Capturing continuously means the
 * frame from *when it happened* still exists; matching on timestamp is what
 * turns "look now" into "look at then".
 *
 * Deliberately not a stream. Frames never leave the machine on their own — the
 * buffer is local, fixed-size and self-discarding. One frame goes out only when
 * something happens that a picture would explain.
 */
export function startFrameBuffer(grabber) {
  if (!grabber) return null;

  const maxFrames = BUFFER_FPS * BUFFER_SECONDS;
  let frames = [];
  let stopped = false;

  const timer = setInterval(async () => {
    if (stopped) return;
    const frame = await grabber.captureSmall();
    if (!frame || stopped) return;
    frames.push({ t: Date.now(), ...frame });
    if (frames.length > maxFrames) frames = frames.slice(-maxFrames);
  }, 1000 / BUFFER_FPS);

  return {
    /**
     * The frame closest to a moment. `toleranceMs` stops it handing back
     * something from a completely different part of the case when the buffer
     * has a gap.
     */
    nearest(timestampMs, toleranceMs = 10000) {
      let best = null;
      let bestDelta = Infinity;
      for (const frame of frames) {
        const delta = Math.abs(frame.t - timestampMs);
        if (delta < bestDelta) {
          bestDelta = delta;
          best = frame;
        }
      }
      return best && bestDelta <= toleranceMs ? { ...best, deltaMs: bestDelta } : null;
    },

    size() {
      return frames.length;
    },

    stop() {
      stopped = true;
      clearInterval(timer);
      frames = [];
    },
  };
}
