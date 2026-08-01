"""Deepgram TTS via their REST /v1/speak endpoint (raw HTTP, no SDK dependency)."""

import logging

import httpx

from app.config import DEEPGRAM_API_KEY

logger = logging.getLogger("servare.deepgram_tts")

# `speed` is a real Deepgram /v1/speak param — a speaking-rate multiplier that
# preserves natural prosody (unlike a naive frontend playbackRate hack, which
# would also shift pitch). Only Aura-2 models support it (aura-asteria-en, the
# Aura-1 voice this used to use, 400s on `speed`).
#
# 1.5 is Deepgram's actual hard cap for this param — verified empirically:
# speed=1.5 succeeds, speed=1.55 and above return 400 Bad Request with no more
# specific error. That's also the top of the requested 1.4-1.5x range, so no
# need to additionally stack a frontend playbackRate hack on top (which would
# shift pitch, unlike this).
# Backed off from the 1.5 cap. 1.5 is intelligible in isolation, but consonants
# go first and "ampicillin-sulbactam" is the one word the whole interrupt turns
# on. 1.35 costs about half a second on a 22-word alert and buys those back.
TTS_SPEED = 1.35

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
