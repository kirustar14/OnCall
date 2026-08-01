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
