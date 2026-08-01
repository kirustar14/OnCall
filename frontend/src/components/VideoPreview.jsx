import { useEffect, useRef } from 'react';

export default function VideoPreview({ stream }) {
  const videoRef = useRef(null);

  useEffect(() => {
    if (videoRef.current) {
      videoRef.current.srcObject = stream || null;
    }
  }, [stream]);

  return (
    <video
      ref={videoRef}
      className="video-preview"
      autoPlay
      playsInline
      muted
    />
  );
}
