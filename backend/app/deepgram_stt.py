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


class DeepgramSTTSession:
    """on_transcript(text, is_final, speaker_index) — speaker_index is None when
    diarization has not resolved a speaker for the segment."""

    def __init__(self, on_transcript: Callable[[str, bool, Optional[int]], Awaitable[None]]):
        self._on_transcript = on_transcript
        self._ws: Optional[websockets.ClientConnection] = None
        self._reader_task: Optional[asyncio.Task] = None

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
        self._reader_task = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        if self._ws is None:
            return
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
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
        except Exception:
            logger.exception("failed to send audio chunk to Deepgram")

    async def finish(self) -> None:
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
