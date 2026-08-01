"""Intervention agent: after every extraction update, check active medications
against documented allergies. On a likely conflict, ask Claude (with web_search)
to confirm and find a clinically appropriate alternative, then produce a short
spoken alert string."""

import asyncio
import logging
import time
import uuid

import anthropic

from app.case_store import Alert, CaseState
from app.config import ANTHROPIC_API_KEY, CLAUDE_MODEL

logger = logging.getLogger("servare.intervention")

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

# Coarse allergen -> medication-keyword cross-reactivity map. This is a cheap pre-filter;
# Claude (with web search) makes the actual clinical call and picks an alternative.
CROSS_REACTIVITY = {
    "penicillin": ["penicillin", "amoxicillin", "ampicillin", "augmentin", "piperacillin", "oxacillin", "nafcillin"],
    "amoxicillin": ["amoxicillin", "penicillin", "ampicillin", "augmentin"],
    "sulfa": ["sulfamethoxazole", "bactrim", "sulfasalazine", "sulfadiazine"],
    "nsaid": ["ibuprofen", "naproxen", "aspirin", "ketorolac", "toradol"],
    "aspirin": ["aspirin", "ibuprofen", "naproxen", "ketorolac", "toradol"],
    "cephalosporin": ["cephalexin", "ceftriaxone", "cefazolin", "cefepime"],
    "morphine": ["morphine", "codeine", "hydromorphone"],
    "latex": [],
    "iodine": ["iodine", "contrast"],
}


def _find_conflicts(allergies: list[dict], medications: list[dict]) -> list[tuple[dict, dict]]:
    conflicts = []
    for allergy in allergies:
        allergen_key = allergy["allergen"].strip().lower()
        trigger_words = CROSS_REACTIVITY.get(allergen_key, [allergen_key])
        for med in medications:
            med_name = med["name"].strip().lower()
            if any(word in med_name for word in trigger_words) or allergen_key in med_name:
                conflicts.append((allergy, med))
    return conflicts


ALTERNATIVE_SYSTEM_PROMPT = """You are a clinical safety assistant in an ER. A patient has a \
documented allergy and a medication was just ordered that may conflict with it. Confirm whether \
this is a real clinical conflict (cross-reactivity or direct allergy), and if so use web search to \
find a clinically appropriate alternative medication for the same indication that avoids the \
allergen. Respond in this exact format, nothing else:

CONFLICT: yes|no
ALTERNATIVE: <alternative medication name, or "none" if CONFLICT is no>
REASON: <one short clause explaining the cross-reactivity or conflict>
"""


def _parse_alternative_response(text: str) -> dict:
    result = {"conflict": False, "alternative": "", "reason": ""}
    for line in text.splitlines():
        line = line.strip()
        if line.upper().startswith("CONFLICT:"):
            result["conflict"] = "yes" in line.lower()
        elif line.upper().startswith("ALTERNATIVE:"):
            result["alternative"] = line.split(":", 1)[1].strip()
        elif line.upper().startswith("REASON:"):
            result["reason"] = line.split(":", 1)[1].strip()
    return result


async def _confirm_and_find_alternative(allergen: str, medication: str) -> dict:
    if _client is None:
        logger.warning("ANTHROPIC_API_KEY not set — skipping intervention check")
        return {"conflict": False, "alternative": "", "reason": ""}

    def _call() -> dict:
        response = _client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 3}],
            system=ALTERNATIVE_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Documented allergy: {allergen}\nMedication just ordered: {medication}\n"
                        "Is this a conflict? If so, what alternative should be used instead?"
                    ),
                }
            ],
        )
        text = "\n".join(b.text for b in response.content if b.type == "text")
        return _parse_alternative_response(text)

    return await asyncio.to_thread(_call)


def _time_ago(timestamp: float) -> str:
    seconds = max(0, time.time() - timestamp)
    if seconds < 60:
        return "moments ago"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = int(minutes // 60)
    return f"{hours} hour{'s' if hours != 1 else ''} ago"


async def check_for_conflicts(case: CaseState) -> list[Alert]:
    """Run after every extraction update. Returns newly created alerts (empty if none)."""
    candidate_pairs = _find_conflicts(case.allergies, case.medications)
    new_alerts: list[Alert] = []

    for allergy, med in candidate_pairs:
        already_alerted = any(
            a.allergen.lower() == allergy["allergen"].lower() and med["name"].lower() in a.text.lower()
            for a in case.alerts
        )
        if already_alerted:
            continue

        try:
            result = await _confirm_and_find_alternative(allergy["allergen"], med["name"])
        except Exception:
            logger.exception("intervention: Claude conflict check failed for case %s", case.case_id)
            continue

        if not result["conflict"]:
            continue

        alternative = result["alternative"] or "an alternative agent"
        spoken = (
            f"Warning — Case {case.case_id[:8]} has a documented {allergy['allergen']} allergy "
            f"recorded {_time_ago(allergy['timestamp'])}. {med['name']} was just ordered. "
            f"Recommend {alternative} instead."
        )
        alert = Alert(
            id=str(uuid.uuid4()),
            text=spoken,
            allergen=allergy["allergen"],
            alternative=alternative,
            timestamp=time.time(),
        )
        case.alerts.append(alert)
        new_alerts.append(alert)

    return new_alerts
