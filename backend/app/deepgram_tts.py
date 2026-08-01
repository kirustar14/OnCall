"""Deepgram TTS via their REST /v1/speak endpoint (raw HTTP, no SDK dependency)."""

import logging

import httpx

from app.config import DEEPGRAM_API_KEY

logger = logging.getLogger("servare.deepgram_tts")

# `speed` is a real Deepgram /v1/speak param — a speaking-rate multiplier that
# preserves natural prosody (unlike a naive frontend playbackRate hack, which
# would also shift pitch). Only Aura-2 models support it (aura-asteria-en, the
# Aura-1 voice this used to use, 400s on `speed`) — verified live: at 1.25x a
# sample alert went from 7.0s to 5.8s, ~1.2x faster, no distortion.
TTS_SPEED = 1.25

DEEPGRAM_SPEAK_URL = f"https://api.deepgram.com/v1/speak?model=aura-2-asteria-en&encoding=mp3&speed={TTS_SPEED}"


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
