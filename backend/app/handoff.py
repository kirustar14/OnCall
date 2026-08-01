"""The push half of Context: a briefing for a clinician who just walked in.

`query.py` covers the pull half — you ask, it answers. This is the other half:
somebody arrives cold, mid-resuscitation, and needs state rather than a
transcript. What we know, what we think, what has been done, what is pending
and who owns it, and what is still unresolved.

Unowned open questions lead the unresolved list, because those are the ones
nobody is going to answer.
"""

import asyncio
import json
import logging

import anthropic

from app.case_store import CaseState
from app.config import ANTHROPIC_API_KEY, CLAUDE_MODEL

logger = logging.getLogger("servare.handoff")

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

SYSTEM_PROMPT = """You brief a clinician who just walked into an active resuscitation. They did \
not see any of it.

Do not hand them a transcript. Hand them state: what we know, what we think may be happening, \
what has been done, what is pending and who owns it, and what is still unresolved.

Rules:
- Never state a finding the record does not contain. If something is unknown, that IS the \
briefing — say it is unknown, and say if nobody owns finding out.
- Open items with no owner come FIRST in "unresolved", and say explicitly that nobody has taken \
them.
- Attribute anything that came from outside the team (a family member, EMS) to its source.
- Do NOT recommend treatment. You report state; the clinician decides.
- "spoken_brief" is what a senior clinician would actually say out loud in about thirty seconds. \
Plain sentences, no headings, no lists."""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "what_we_know": {"type": "array", "items": {"type": "string"}},
        "what_we_think": {"type": "array", "items": {"type": "string"}},
        "what_has_been_done": {"type": "array", "items": {"type": "string"}},
        "what_is_pending": {"type": "array", "items": {"type": "string"}},
        "unresolved": {"type": "array", "items": {"type": "string"}},
        "spoken_brief": {"type": "string"},
    },
    "required": [
        "what_we_know",
        "what_we_think",
        "what_has_been_done",
        "what_is_pending",
        "unresolved",
        "spoken_brief",
    ],
    "additionalProperties": False,
}

EMPTY_BRIEF = {
    "what_we_know": [],
    "what_we_think": [],
    "what_has_been_done": [],
    "what_is_pending": [],
    "unresolved": [],
    "spoken_brief": "",
}


def _case_to_context(case: CaseState) -> dict:
    return {
        "case_id": case.case_id,
        "case_details": case.case_details,
        "allergies": case.allergies,
        "vitals": case.vitals,
        "medications": case.medications,
        "notes": case.notes,
        "work": [w.to_dict() for w in case.work],
        "alerts": [{"text": a.text, "allergen": a.allergen} for a in case.alerts],
        "transcript": case.running_transcript,
    }


async def build_handoff(case: CaseState) -> dict:
    if _client is None:
        return dict(EMPTY_BRIEF, spoken_brief="Anthropic API key not configured.")

    context = _case_to_context(case)

    def _call() -> dict:
        response = _client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4096,
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
            },
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {"role": "user", "content": f"Case record:\n{json.dumps(context, indent=2)}"}
            ],
        )
        text = next(b.text for b in response.content if b.type == "text")
        return json.loads(text)

    try:
        return await asyncio.to_thread(_call)
    except Exception:
        logger.exception("handoff generation failed for case %s", case.case_id)
        return dict(EMPTY_BRIEF, spoken_brief="Handoff generation failed.")
