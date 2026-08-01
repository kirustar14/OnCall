"""Point-of-view frames, held briefly, in memory, per case.

By the time an alert fires the room has moved on. The frame worth looking at is
the one from when the thing happened, a few seconds earlier — nobody narrates a
glance at a monitor, so there is no audio marking the moment either.

So frames arrive continuously and cheaply, and exactly one of them ever reaches
a model: the one nearest a timestamp somebody already cares about. That keeps
the cost at one vision call per alert rather than one per frame, and keeps the
justification simple — a trauma bay is not streamed to a model, it is looked at
once, at a moment that already mattered.

The source is deliberately anonymous. A phone relaying the glasses camera over
the Device Access Toolkit and a laptop webcam post identical JSON here; nothing
downstream can tell which, and nothing needs to.
"""

import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("oncall.frame_buffer")

# One frame a second is plenty to land within a second of any moment, and small
# enough that a few minutes costs a handful of megabytes.
MAX_AGE_SECONDS = 180.0
# Hard cap per case regardless of rate, so a misbehaving client cannot grow this
# without bound.
MAX_FRAMES = 400
# Beyond this, the nearest frame is from a different part of the case and worse
# than no frame at all.
DEFAULT_TOLERANCE_SECONDS = 10.0


@dataclass
class Frame:
    captured_at: float  # epoch seconds, from whoever captured it
    image_b64: str
    media_type: str
    source: str  # "glasses", "webcam", ... free text; for display only


class FrameBuffer:
    def __init__(self) -> None:
        self._frames: dict[str, list[Frame]] = {}
        self._lock = threading.Lock()

    def add(self, case_id: str, frame: Frame) -> int:
        with self._lock:
            frames = self._frames.setdefault(case_id, [])
            frames.append(frame)
            # Frames arrive in order in practice, but a retry can arrive late.
            frames.sort(key=lambda f: f.captured_at)
            cutoff = time.time() - MAX_AGE_SECONDS
            kept = [f for f in frames if f.captured_at >= cutoff][-MAX_FRAMES:]
            self._frames[case_id] = kept
            return len(kept)

    def nearest(
        self,
        case_id: str,
        moment: float,
        tolerance: float = DEFAULT_TOLERANCE_SECONDS,
    ) -> Optional[tuple[Frame, float]]:
        """The frame closest to `moment`, or None if nothing is close enough."""
        with self._lock:
            frames = list(self._frames.get(case_id, []))
        if not frames:
            return None

        best = min(frames, key=lambda f: abs(f.captured_at - moment))
        delta = abs(best.captured_at - moment)
        if delta > tolerance:
            logger.debug("nearest frame for %s is %.1fs off — ignoring", case_id, delta)
            return None
        return best, delta

    def count(self, case_id: str) -> int:
        with self._lock:
            return len(self._frames.get(case_id, []))

    def clear(self, case_id: str) -> None:
        with self._lock:
            self._frames.pop(case_id, None)


frame_buffer = FrameBuffer()
