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

DEEPGRAM_LISTEN_URL = (
    "wss://api.deepgram.com/v1/listen"
    "?model=nova-2&language=en-US&encoding=linear16&sample_rate=16000"
    "&channels=1&interim_results=true&smart_format=true&punctuate=true&endpointing=300"
)


class DeepgramSTTSession:
    def __init__(self, on_transcript: Callable[[str, bool], Awaitable[None]]):
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

                is_final = bool(msg.get("is_final") or msg.get("speech_final"))
                try:
                    await self._on_transcript(text, is_final)
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
