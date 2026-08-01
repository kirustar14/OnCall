"""Buffer Deepgram segments into whole utterances before extraction.

Deepgram finalizes on acoustic boundaries, which are shorter than clinical
utterances. Measured on the demo audio, running extraction per raw segment
caused three separate failures:

  "…respirations 22, sat 97…"     GCS split across a boundary -> vital lost
  "I've got ortho."               claim split from its second half, and
  "I'm calling them now."         diarized to two different speakers, so the
                                  ortho task was never resolved
  "…in the waiting room."         a word duplicated across the seam
  "Room. She says Ava has…"

Extraction is stateful and reasons about what was *said*, so it wants a
sentence, not an acoustic fragment. This buffers finals per case and flushes
when the room goes quiet for a beat — the same cue a human uses to decide
someone has finished talking.
"""

import asyncio
import logging
import time
from typing import Awaitable, Callable, Optional

logger = logging.getLogger("servare.segmenter")

# How long the room must be quiet before we treat an utterance as finished.
QUIET_SECONDS = 1.3
# Flush early if someone monologues, so a long EMS handoff doesn't stall.
MAX_CHARS = 600
# A fragment this short sitting on a speaker change is much more likely a
# diarization boundary error than a real turn. Measured case: "I've got ortho."
# (16 chars) was attributed to the physician who had just *asked* for it, while
# "I'm calling them now." went to the nurse — so the claim never resolved the
# task and the item stayed unowned. Below this length we merge across the
# boundary and attribute the whole utterance to whoever said more of it.
MIN_TURN_CHARS = 30


class UtteranceBuffer:
    """Accumulates finalized segments and flushes a whole utterance.

    Flushes on: a quiet gap, a speaker change, a length cap, or close().
    """

    def __init__(self, on_utterance: Callable[[str, Optional[int]], Awaitable[None]]):
        self._on_utterance = on_utterance
        # (text, speaker) so we can weigh attribution if a turn gets merged.
        self._parts: list[tuple[str, Optional[int]]] = []
        self._timer: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    @property
    def pending(self) -> str:
        return " ".join(t for t, _ in self._parts)

    def _dominant_speaker(self) -> Optional[int]:
        """Attribute a merged utterance to whoever contributed most of it."""
        weight: dict[int, int] = {}
        for text, speaker in self._parts:
            if speaker is not None:
                weight[speaker] = weight.get(speaker, 0) + len(text)
        if not weight:
            return None
        return max(weight.items(), key=lambda kv: kv[1])[0]

    async def add(self, text: str, speaker: Optional[int]) -> None:
        text = text.strip()
        if not text:
            return

        async with self._lock:
            current = self._dominant_speaker()
            speaker_changed = (
                self._parts and speaker is not None and current is not None and speaker != current
            )
            # Only honour a speaker change once the current turn is substantial
            # enough to plausibly BE a turn — otherwise merge across it.
            if speaker_changed and len(self.pending) >= MIN_TURN_CHARS:
                await self._flush_locked()

            self._parts.append((text, speaker))

            if len(self.pending) >= MAX_CHARS:
                await self._flush_locked()
                return

        self._restart_timer()

    def _restart_timer(self) -> None:
        if self._timer and not self._timer.done():
            self._timer.cancel()
        self._timer = asyncio.create_task(self._flush_after_quiet())

    async def _flush_after_quiet(self) -> None:
        try:
            await asyncio.sleep(QUIET_SECONDS)
        except asyncio.CancelledError:
            return
        async with self._lock:
            await self._flush_locked()

    async def _flush_locked(self) -> None:
        if not self._parts:
            return
        text, speaker = self.pending, self._dominant_speaker()
        self._parts = []
        try:
            await self._on_utterance(text, speaker)
        except Exception:
            logger.exception("utterance handler failed")

    async def close(self) -> None:
        """Flush whatever is left — a case shouldn't end mid-sentence."""
        if self._timer and not self._timer.done():
            self._timer.cancel()
        async with self._lock:
            await self._flush_locked()
