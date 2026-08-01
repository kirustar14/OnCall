"""Visual context from a point-of-view frame.

The hard rule here is what makes vision safe to include at all:

    VISION INFORMS. SPEECH RECORDS.

This model describes what is *visible* — a monitor is in frame and showing a
reading, gloved hands are at an open wound on the left leg, a vial is being
drawn up. It never reports a clinical value as a fact, and nothing it returns is
written into vitals, allergies or medications. A number misread off a screen at
arm's length, at an angle, in motion, would otherwise enter the chart as
documented and be indistinguishable from something a clinician actually said.

So the frame is context attached to a moment, not evidence. It can tell the room
"the repeat pressure looks like it has been taken — someone confirm the value",
which is useful precisely because nobody narrates a glance at a monitor. What it
cannot do is decide what the value was.

Ships on Ray-Ban Meta (camera + open-ear audio, no display) via the Meta
Wearables Device Access Toolkit; in the browser the POV proxy is a webcam. Same
frame, same call — the model cannot tell which lens it came from, and neither
claim is weakened by using the other.
"""

import asyncio
import json
import logging
from typing import Any, Optional

import anthropic

from app.config import ANTHROPIC_API_KEY, CLAUDE_MODEL

logger = logging.getLogger("oncall.vision")

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

OBSERVE_SCHEMA = {
    "type": "object",
    "properties": {
        "scene": {
            "type": "string",
            "enum": [
                "monitor",
                "medication",
                "wound",
                "procedure",
                "people",
                "document",
                "other",
                "unreadable",
            ],
        },
        "description": {
            "type": "string",
            "description": (
                "One or two plain sentences describing what is visible. Under 30 words."
            ),
        },
        "readings": {
            "type": "array",
            "description": (
                "Values legible IN THE IMAGE. Always UNCONFIRMED — they are offered for a human "
                "to confirm, never recorded. Omit anything you cannot actually read; a guessed "
                "digit is worse than a missing one."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": 'e.g. "Blood pressure", "Drug on vial label"'},
                    "value": {"type": "string", "description": "Exactly as displayed."},
                    "legibility": {"type": "string", "enum": ["clear", "partial", "guessing"]},
                },
                "required": ["label", "value", "legibility"],
                "additionalProperties": False,
            },
        },
        "people": {
            "type": "array",
            "description": (
                "People present, BY ROLE ONLY — scrubs, badge, gown, what they are doing. Never "
                "identify an individual, never describe a face, never name anyone. Role presence "
                "is what the ledger can act on; identity is biometric data we do not collect."
            ),
            "items": {"type": "string"},
        },
        "relates_to_work_ids": {
            "type": "array",
            "description": (
                "Ids from the open ledger this frame appears to bear on — e.g. a monitor in "
                "frame relates to an outstanding repeat-observation item. Empty if none."
            ),
            "items": {"type": "string"},
        },
        "prompt_the_room": {
            "type": "string",
            "description": (
                "If the frame suggests an open item may have been actioned without anyone "
                "saying so, one short line asking the room to CONFIRM — never asserting. "
                'e.g. "The repeat pressure looks taken. Can someone confirm the value?" '
                "Empty string if there is nothing worth interrupting for."
            ),
        },
        "confidence": {"type": "string", "enum": ["clear", "partial", "poor"]},
    },
    "required": [
        "scene",
        "description",
        "readings",
        "people",
        "relates_to_work_ids",
        "prompt_the_room",
        "confidence",
    ],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You are looking through a clinician's point-of-view camera during an active \
emergency-department resuscitation. You provide CONTEXT, not measurements.

ABSOLUTE RULE: nothing you return is a fact. Everything is an unconfirmed observation a human \
will check. Nothing you say is written into the patient record.

READINGS. You MAY report a value you can genuinely read — it is far more useful to say "the \
monitor appears to read 98 over 62, confirm?" than "the pressure looks taken", because a \
clinician corrects a wrong number in one word. Put it in `readings` with honest legibility, and \
phrase it as something to confirm, never as something that is so. If a digit is ambiguous, mark \
it "guessing" or leave the reading out entirely. A number you invented is indistinguishable from \
one a clinician spoke, and that is the one failure this system cannot absorb.

  good: readings [{Blood pressure, "98/62", clear}] + "The monitor appears to read 98 over 62."
  bad:  "Blood pressure is 98 over 62."            <- stated as fact
  bad:  a reading you inferred, smoothed, or half-saw

PEOPLE. Report ROLE and activity only — scrubs, badge, gown, gloves, what they are doing. \
"Someone in surgical scrubs with an orthopaedics badge is at the bedside" is exactly what the \
ledger can act on. NEVER identify an individual, describe a face, or name anyone: that is \
biometric data, we do not collect it, and the role is the useful part anyway.

  good: "A person in scrubs with an ortho badge has arrived at the bedside."
  bad:  "Dr. Chen is at the bedside."              <- identification
  bad:  any description of somebody's face

Most frames contain nothing worth saying. A dark, blurred, or clinically empty frame is scene \
"unreadable" or "other", with empty readings, people and prompt_the_room — say so plainly rather \
than inventing something to report.

prompt_the_room is for one specific case: the frame suggests an open ledger item may have been \
done without anyone announcing it. Nobody narrates a glance at a monitor or notices ortho walking \
in, so that work silently stays open. Ask the room to confirm — include the reading if you have \
one, since a number is faster to confirm than a description. Never state that it IS done. Leave \
it empty unless it genuinely warrants interrupting a working team.

  good: "The repeat pressure appears to read 98 over 62. Can someone confirm?"
  good: "Ortho looks to be at the bedside. Confirming?"
  bad:  "The repeat pressure is done."             <- asserts a resolution"""


async def describe_scene(
    image_b64: str,
    media_type: str = "image/jpeg",
    open_ledger: Optional[list[dict[str, str]]] = None,
) -> dict[str, Any]:
    """Describe a POV frame. Returns context, never clinical facts."""
    empty = {
        "scene": "unreadable",
        "description": "",
        "readings": [],
        "people": [],
        "relates_to_work_ids": [],
        "prompt_the_room": "",
        "confidence": "poor",
    }
    if _client is None:
        logger.warning("ANTHROPIC_API_KEY not set — skipping vision")
        return empty

    ledger_text = json.dumps(open_ledger, indent=2) if open_ledger else "(none open)"

    def _call() -> dict[str, Any]:
        response = _client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2048,
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": OBSERVE_SCHEMA},
            },
            system=[
                {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
            ],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                "Open ledger items (only these ids may appear in "
                                f"relates_to_work_ids):\n{ledger_text}\n\n"
                                "What is visible in this frame?"
                            ),
                        },
                    ],
                }
            ],
        )
        text = next(b.text for b in response.content if b.type == "text")
        return json.loads(text)

    try:
        result = await asyncio.to_thread(_call)
    except Exception:
        logger.exception("vision call failed")
        return empty

    # Only ids we actually handed over may come back.
    allowed = {item["id"] for item in (open_ledger or [])}
    result["relates_to_work_ids"] = [
        wid for wid in result.get("relates_to_work_ids", []) if wid in allowed
    ]
    return result
