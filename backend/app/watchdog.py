"""The unowned-work watchdog.

A request nobody was named for is a request nobody will do, and — worse —
nobody will notice wasn't done. Closed-loop communication (order, readback,
confirmation) is trained protocol in trauma resuscitation precisely because it
degrades when the room gets loud. This loop watches for the degradation and
asks the room the question a team lead would ask.

It never assigns an owner and never advises. It asks who is taking it.

The underlying state is FHIR-native: an unowned item is exactly
`GET /Task?owner:missing=true&status=ready`. We scan in memory for latency,
but the concept is standard search, not a bespoke invention.
"""

import asyncio
import base64
import logging
import time

from app.case_store import CaseState, WorkItem, store
from app.config import UNOWNED_PROMPT_DELAY_SECONDS
from app.deepgram_tts import synthesize_speech
from app.ws_manager import ws_manager

logger = logging.getLogger("servare.watchdog")

POLL_INTERVAL_SECONDS = 2.0


def _find_orphan(case: CaseState, now: float) -> WorkItem | None:
    """Oldest unowned, unprompted, still-open item past the grace period."""
    candidates = [
        w
        for w in case.work
        if w.is_orphan and w.prompted_at is None and (now - w.opened_at) >= UNOWNED_PROMPT_DELAY_SECONDS
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda w: w.opened_at)


def _prompt_text(case: CaseState, item: WorkItem) -> str:
    if item.kind == "uncertainty":
        return (
            f"{case.spoken_label}. {item.action} is still unanswered "
            "and nobody has taken it. Who is taking it?"
        )
    return f"{case.spoken_label}. {item.action} has no owner. Who is taking it?"


async def _announce(case: CaseState, item: WorkItem) -> None:
    text = _prompt_text(case, item)

    audio_b64 = None
    try:
        audio_bytes = await synthesize_speech(text)
        if audio_bytes:
            audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
    except Exception:
        logger.exception("watchdog TTS failed for case %s", case.case_id)

    await ws_manager.send_json(
        case.case_id,
        {
            "type": "unowned_prompt",
            "work_id": item.id,
            "text": text,
            "action": item.action,
            "kind": item.kind,
            "audio_b64": audio_b64,
            "audio_mime": "audio/mpeg",
        },
    )
    logger.info("watchdog: asked the room about %r (case %s)", item.action, case.case_id)


async def watchdog_loop() -> None:
    logger.info(
        "watchdog running — unowned items announced after %ss", UNOWNED_PROMPT_DELAY_SECONDS
    )
    while True:
        try:
            now = time.time()
            for case in store.open_cases():
                orphan = _find_orphan(case, now)
                if orphan is None:
                    continue
                # Mark before announcing so a slow TTS call can't double-fire.
                orphan.prompted_at = now
                await _announce(case, orphan)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("watchdog iteration failed")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
