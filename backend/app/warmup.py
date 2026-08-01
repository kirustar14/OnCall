"""Pre-compile the structured-output schemas at startup.

The first request that uses a given JSON schema pays a one-time compilation
cost; every request after that hits a 24-hour cache. Measured on the real
pipeline, that made the first transcript segment take ~15s while every later
one took ~3s — which in a demo is fifteen seconds of blank screen right at the
moment the EMS handoff lands.

Firing each schema once against a trivial input at boot moves that cost to
startup, where nobody is watching.
"""

import asyncio
import logging
import time

from app.case_store import CaseState
from app.extraction import extract_from_segment
from app.handoff import build_handoff
from app.intervention import _assess_conflict

logger = logging.getLogger("servare.warmup")


async def warm_schemas() -> None:
    t0 = time.time()
    try:
        await asyncio.gather(
            # Trivial inputs — we only care that each schema gets compiled.
            extract_from_segment("Patient is awake.", []),
            _assess_conflict("penicillin", "acetaminophen"),
            build_handoff(CaseState(case_id="warmup", number=0)),
            return_exceptions=True,
        )
        logger.info("schema warmup complete in %.1fs", time.time() - t0)
    except asyncio.CancelledError:
        raise
    except Exception:
        # Warmup is an optimisation. Never let it stop the server coming up.
        logger.exception("schema warmup failed (non-fatal)")
