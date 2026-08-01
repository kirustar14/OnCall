"""Deepgram TTS via their REST /v1/speak endpoint (raw HTTP, no SDK dependency)."""

import logging

import httpx

from app.config import DEEPGRAM_API_KEY

logger = logging.getLogger("oncall.deepgram_tts")

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


# Observed once in a clean end-to-end run: Deepgram closed a /v1/speak response
# mid-body (httpx RemoteProtocolError, "incomplete chunked read"). Callers all
# catch TTS failures and carry on, which is right — a case must not stop because
# a voice did not render — but the visible effect is an alert that appears on
# screen and is never spoken, and being spoken is the entire point of this one.
# It is a transient socket failure, so one immediate retry clears it.
_TTS_ATTEMPTS = 2


async def synthesize_speech(text: str) -> bytes:
    if not DEEPGRAM_API_KEY:
        logger.warning("DEEPGRAM_API_KEY not set — TTS disabled")
        return b""
    if not text.strip():
        return b""

    last_error: Exception | None = None
    for attempt in range(_TTS_ATTEMPTS):
        try:
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
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            # Don't burn a retry on a request that will fail identically: a bad
            # key or a rejected parameter is not going to resolve in 200ms.
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None and 400 <= status < 500 and status != 429:
                raise
            last_error = exc
            if attempt + 1 < _TTS_ATTEMPTS:
                logger.warning("TTS attempt %d failed (%s) — retrying", attempt + 1, type(exc).__name__)

    assert last_error is not None
    raise last_error
