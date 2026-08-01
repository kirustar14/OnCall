"""Lightweight intent classifier for each finalized transcript segment: is this
clinical narration (extract it into case data) or a direct question/request
addressed to the voice assistant itself (route to the query agent instead)?
"""

import asyncio
import json
import logging

import anthropic

from app.config import ANTHROPIC_API_KEY, CLAUDE_MODEL

logger = logging.getLogger("oncall.intent")

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

SYSTEM_PROMPT = """You classify one segment of live ER transcript audio. Decide whether it is:

- Clinical narration: vitals, allergies, medications, orders, patient details, or observations \
being stated or dictated as part of the case — including when it reports someone ELSE's question \
or statement (e.g. "patient's asking if he can have some water" is narration, not a direct \
request, because it's reporting the patient's question, not asking the assistant anything).

- A direct question or request addressed to the voice assistant itself — the speaker wants the \
assistant to explain, look up, calculate, or answer something (e.g. "what does tachycardic mean", \
"is this medication safe given his allergy", "what was his blood pressure on arrival", "how much \
ibuprofen can we give a kid this age").

When in doubt between the two, ask: is the clinician informing the case record, or asking the \
assistant a question they want an answer to right now?"""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "is_question": {
            "type": "boolean",
            "description": "true if this segment is a direct question/request addressed to the assistant, false if it's clinical narration",
        }
    },
    "required": ["is_question"],
    "additionalProperties": False,
}


async def classify_segment(text: str) -> bool:
    """Returns True if this segment is a direct question/request addressed to the
    assistant (route to the query agent), False if it's clinical narration (extract
    it). Fails safe to narration on any error — worst case a question gets treated
    as narration and quietly ignored by extraction, rather than a narration line
    accidentally derailing into an unwanted "answer"."""

    if _client is None:
        return False

    def _call() -> bool:
        response = _client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=256,
            thinking={"type": "disabled"},
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Transcript segment:\n\n{text}"}],
        )
        text_out = next(b.text for b in response.content if b.type == "text")
        return bool(json.loads(text_out)["is_question"])

    try:
        return await asyncio.to_thread(_call)
    except Exception:
        logger.exception("intent classification failed, defaulting to narration")
        return False
