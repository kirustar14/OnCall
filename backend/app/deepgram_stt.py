"""Deepgram live streaming STT, driven directly against Deepgram's documented
WebSocket wire protocol (wss://api.deepgram.com/v1/listen) rather than through
the deepgram-sdk package, so this doesn't depend on SDK version churn.

One DeepgramSTTSession wraps one live connection for one case: audio chunks
sent in via send_audio(), transcript results delivered out via the
on_transcript(text, is_final) callback as they arrive.
"""

import asyncio
import json
import logging
import time
from typing import Awaitable, Callable, Optional

import websockets

from app.config import DEEPGRAM_API_KEY

logger = logging.getLogger("servare.deepgram_stt")

# Terms the ledger depends on getting exactly right. Without keyterm prompting
# these come back mangled ("ampicillin-sulbactam" is the whole intervention) —
# and keyterm requires nova-3, which is also the clinically-tuned model.
KEYTERMS = [
    "ampicillin-sulbactam",
    "penicillin",
    "anaphylaxis",
    "clindamycin",
    "gentamicin",
    "vancomycin",
    "anticoagulated",
    "tranexamic acid",
    "Medic",
    "GCS thirteen",
    "tibia",
    "fentanyl",
    "GCS",
    "ortho",
    "respiratory",
]

DEEPGRAM_LISTEN_URL = (
    "wss://api.deepgram.com/v1/listen"
    "?model=nova-3-medical&language=en-US&encoding=linear16&sample_rate=16000"
    "&channels=1&interim_results=true&smart_format=true&punctuate=true&endpointing=300"
    # Who said it. Without this, "who owns this task" has no input at all.
    "&diarize=true"
    # Deepgram emits an explicit UtteranceEnd after this much *speech* silence.
    # That is the signal the utterance buffer actually wants: a wall-clock timer
    # started when a final arrives measures Deepgram's delivery cadence, not a
    # pause in the room, and it split "…passenger GCS" from "13, she's confused"
    # mid-sentence — losing the GCS entirely.
    # 2000, not 1000. At 1000 this fires on ordinary sentence pauses inside a
    # single turn — a live run split one EMS handoff into five separate
    # utterances, which both fragmented the extraction and multiplied the work
    # per clip. Turn boundaries are detected by the speaker change instead;
    # this only needs to catch a genuine pause by the same speaker.
    "&utterance_end_ms=2000"
    # UtteranceEnd is only emitted when interim results are on.
    + "".join(f"&keyterm={term.replace(' ', '%20')}" for term in KEYTERMS)
)


def _dominant_speaker(words: list[dict]) -> Optional[int]:
    """Deepgram labels each word with a speaker index. A segment can straddle a
    turn boundary, so attribute it to whoever said most of it."""
    counts: dict[int, int] = {}
    for word in words:
        speaker = word.get("speaker")
        if isinstance(speaker, int):
            counts[speaker] = counts.get(speaker, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


# Deepgram closes a connection that receives nothing within its timeout window
# ("Deepgram did not receive audio data or a text message within the timeout
# window", close code 1011). A trauma bay is not continuously loud, and a case
# is often opened before anyone speaks — without a keepalive the socket dies
# during the first quiet stretch and never comes back.
KEEPALIVE_INTERVAL_SECONDS = 5.0

# KeepAlive holds the connection open but does NOT flush buffered audio, so the
# last thing said before a pause sits unfinalized until more speech arrives.
# Measured: "Let's get ampicillin-sulbactam, three grams IV" — the order the
# whole safety check exists for — never reached the ledger, because it was the
# final utterance before silence. Finalize forces Deepgram to process what it
# is holding and emit it as a final transcript.
FINALIZE_AFTER_SECONDS = 1.2

# Finalize makes Deepgram emit the tail, but UtteranceEnd never follows it: that
# event is derived from silence *inside the audio stream*, and by definition the
# stream has stopped. So the last utterance of a case would sit in the buffer
# until the quiet-timer fallback expired. That is exactly the wrong utterance to
# delay, because in this scenario it is the drug order. Once Finalize's own
# transcript has had a beat to land, treat the pause as an utterance boundary.
#
# This only fires when the audio *stream* stops, not on a conversational pause:
# _last_audio is stamped by every frame, and both the mic and clip playback keep
# sending frames through silence.
FINALIZE_FLUSH_DELAY_SECONDS = 0.8


class DeepgramSTTSession:
    """on_transcript(text, is_final, speaker_index) — speaker_index is None when
    diarization has not resolved a speaker for the segment."""

    def __init__(
        self,
        on_transcript: Callable[[str, bool, Optional[int]], Awaitable[None]],
        on_utterance_end: Optional[Callable[[], Awaitable[None]]] = None,
    ):
        self._on_transcript = on_transcript
        # Fired when Deepgram observes real silence — the cue that a speaker has
        # actually finished, as opposed to a gap in delivery.
        self._on_utterance_end = on_utterance_end
        self._ws: Optional[websockets.ClientConnection] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._keepalive_task: Optional[asyncio.Task] = None
        self._last_send: float = 0.0
        # Audio specifically — KeepAlive counts as traffic but not as speech.
        self._last_audio: float = 0.0
        self._finalize_sent: bool = True

    async def start(self) -> None:
        if not DEEPGRAM_API_KEY:
            logger.warning("DEEPGRAM_API_KEY not set — live transcription disabled")
            return
        try:
            self._ws = await websockets.connect(
                DEEPGRAM_LISTEN_URL,
                additional_headers={"Authorization": f"Token {DEEPGRAM_API_KEY}"},
            )
        except Exception:
            logger.exception("failed to connect to Deepgram")
            self._ws = None
            return
        self._last_send = time.monotonic()
        self._reader_task = asyncio.create_task(self._read_loop())
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    async def _keepalive_loop(self) -> None:
        """Flush the tail of speech, then hold the connection open through silence."""
        try:
            while self._ws is not None:
                await asyncio.sleep(0.4)
                if self._ws is None:
                    return
                now = time.monotonic()

                # Audio just stopped — force out whatever is still buffered
                # before settling into keepalive.
                if not self._finalize_sent and now - self._last_audio >= FINALIZE_AFTER_SECONDS:
                    try:
                        await self._ws.send(json.dumps({"type": "Finalize"}))
                        self._finalize_sent = True
                        self._last_send = now
                    except Exception:
                        return
                    asyncio.create_task(self._flush_after_finalize())
                    continue

                if now - self._last_send < KEEPALIVE_INTERVAL_SECONDS:
                    continue
                try:
                    await self._ws.send(json.dumps({"type": "KeepAlive"}))
                    self._last_send = now
                except Exception:
                    return
        except asyncio.CancelledError:
            raise

    async def _flush_after_finalize(self) -> None:
        """Close the utterance once Finalize's transcript has landed."""
        try:
            await asyncio.sleep(FINALIZE_FLUSH_DELAY_SECONDS)
        except asyncio.CancelledError:
            return
        if self._on_utterance_end is not None:
            try:
                await self._on_utterance_end()
            except Exception:
                logger.exception("post-finalize flush failed")

    async def _read_loop(self) -> None:
        if self._ws is None:
            return
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue

                if msg.get("type") == "UtteranceEnd":
                    if self._on_utterance_end is not None:
                        try:
                            await self._on_utterance_end()
                        except Exception:
                            logger.exception("on_utterance_end callback failed")
                    continue

                if msg.get("type") != "Results":
                    continue

                alternatives = msg.get("channel", {}).get("alternatives", [])
                if not alternatives:
                    continue
                text = (alternatives[0].get("transcript") or "").strip()
                if not text:
                    continue

                speaker = _dominant_speaker(alternatives[0].get("words") or [])
                is_final = bool(msg.get("is_final") or msg.get("speech_final"))
                try:
                    await self._on_transcript(text, is_final, speaker)
                except Exception:
                    logger.exception("on_transcript callback failed")
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception:
            logger.exception("Deepgram read loop failed")

    async def send_audio(self, chunk: bytes) -> None:
        if self._ws is None:
            return
        try:
            await self._ws.send(chunk)
            now = time.monotonic()
            self._last_send = now
            self._last_audio = now
            # New speech — arm Finalize again for the next pause.
            self._finalize_sent = False
        except Exception:
            logger.exception("failed to send audio chunk to Deepgram")

    async def finish(self) -> None:
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            self._keepalive_task = None
        if self._ws is None:
            return
        try:
            await self._ws.send(json.dumps({"type": "CloseStream"}))
        except Exception:
            pass
        if self._reader_task is not None:
            try:
                await asyncio.wait_for(self._reader_task, timeout=3)
            except Exception:
                self._reader_task.cancel()
        try:
            await self._ws.close()
        except Exception:
            pass
        self._ws = None
