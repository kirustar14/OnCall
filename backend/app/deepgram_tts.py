"""Deepgram TTS via their REST /v1/speak endpoint (raw HTTP, no SDK dependency)."""

import logging

import httpx

from app.config import DEEPGRAM_API_KEY

logger = logging.getLogger("servare.deepgram_tts")

DEEPGRAM_SPEAK_URL = "https://api.deepgram.com/v1/speak?model=aura-asteria-en&encoding=mp3"


async def synthesize_speech(text: str) -> bytes:
    if not DEEPGRAM_API_KEY:
        logger.warning("DEEPGRAM_API_KEY not set — TTS disabled")
        return b""
    if not text.strip():
        return b""

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            DEEPGRAM_SPEAK_URL,
            headers={
                "Authorization": f"Token {DEEPGRAM_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"text": text},
        )
        resp.raise_for_status()
        return resp.content
