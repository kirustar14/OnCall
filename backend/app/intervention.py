"""Conflict surfacing: after every extraction update, check active medications
against documented allergies.

This agent does NOT recommend treatment. It states two facts — that an allergy
is documented, and that the ordered drug belongs to the class it is documented
against — plus where the allergy came from and when. The clinician draws the
conclusion.

That boundary is deliberate. Software that recommends a therapy is a different
regulatory and liability object than software that surfaces information a
clinician can independently review, and the second one is the honest description
of what a language model is good at. Every alert therefore carries its own
provenance, so the basis is inspectable rather than trusted.
"""

import asyncio
import logging
import time
import uuid

import anthropic

from app.case_store import Alert, CaseState
from app.config import ANTHROPIC_API_KEY, CLAUDE_MODEL

logger = logging.getLogger("servare.intervention")

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

# Coarse allergen -> medication-keyword pre-filter. Deliberately cheap and
# deliberately over-inclusive: it only decides what is worth asking about.
# The real judgement is drug-class reasoning, below.
CROSS_REACTIVITY = {
    "penicillin": [
        "penicillin", "amoxicillin", "ampicillin", "augmentin", "unasyn",
        "sulbactam", "piperacillin", "tazobactam", "oxacillin", "nafcillin",
    ],
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


CONFLICT_SCHEMA = {
    "type": "object",
    "properties": {
        "conflict": {"type": "boolean"},
        "drug_class": {
            "type": "string",
            "description": (
                "The class that links the ordered drug to the documented allergy, e.g. "
                '"penicillin (beta-lactam)". Empty string if there is no conflict.'
            ),
        },
        "basis": {
            "type": "string",
            "description": (
                "One clause a clinician would accept, stating the class relationship as fact. "
                "Do NOT suggest what to give instead. Empty string if there is no conflict."
            ),
        },
    },
    "required": ["conflict", "drug_class", "basis"],
    "additionalProperties": False,
}

CONFLICT_SYSTEM_PROMPT = """You determine whether an ordered medication belongs to a drug class \
a patient is documented allergic to.

Reason about DRUG CLASS, not string matching. Ampicillin-sulbactam, amoxicillin, \
piperacillin-tazobactam and nafcillin are all penicillins even though the word "penicillin" \
appears in none of their names. A keyword match would miss every one of them; that reasoning \
is the entire job.

You are NOT recommending treatment. Do not name an alternative drug, do not suggest what to \
give instead, and do not tell the clinician what to do. State the class relationship as a fact \
and stop. The clinician decides.

Set conflict=false where there is no genuine class relationship. Do not manufacture one."""


async def _assess_conflict(allergen: str, medication: str) -> dict:
    if _client is None:
        logger.warning("ANTHROPIC_API_KEY not set — skipping conflict check")
        return {"conflict": False, "drug_class": "", "basis": ""}

    def _call() -> dict:
        import json

        response = _client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2048,
            thinking={"type": "adaptive"},
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": CONFLICT_SCHEMA},
            },
            system=CONFLICT_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Documented allergy: {allergen}\n"
                        f"Medication just ordered: {medication}\n"
                        "Does the ordered medication belong to the class this patient is "
                        "documented allergic to?"
                    ),
                }
            ],
        )
        text = next(b.text for b in response.content if b.type == "text")
        return json.loads(text)

    try:
        return await asyncio.to_thread(_call)
    except Exception:
        logger.exception("conflict assessment failed")
        return {"conflict": False, "drug_class": "", "basis": ""}


def _time_ago(timestamp: float) -> str:
    seconds = max(0, time.time() - timestamp)
    if seconds < 60:
        return "moments ago"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = int(minutes // 60)
    return f"{hours} hour{'s' if hours != 1 else ''} ago"


def _spoken_alert(case: CaseState, allergy: dict, med: dict, assessment: dict) -> str:
    """Two facts and their provenance. No instruction, no recommendation.

    Reads as: what was ordered, what is on file, where it came from, and how the
    two are related.
    """
    parts = [f"Case {case.case_id[:8]}. {med['name']} was just ordered."]

    source = (allergy.get("source") or "").strip()
    attribution = f", from {source}" if source else ""
    parts.append(
        f"There is a documented {allergy['allergen']} allergy on file, "
        f"recorded {_time_ago(allergy['timestamp'])}{attribution}."
    )

    if assessment.get("basis"):
        parts.append(assessment["basis"].rstrip("."))
    elif assessment.get("drug_class"):
        parts.append(f"{med['name']} is in the {assessment['drug_class']} class")

    return " ".join(p if p.endswith(".") else p + "." for p in parts)


async def check_for_conflicts(case: CaseState) -> list[Alert]:
    """Run after every extraction update. Returns newly created alerts (empty if none)."""
    candidate_pairs = _find_conflicts(case.allergies, case.medications)
    new_alerts: list[Alert] = []

    for allergy, med in candidate_pairs:
        already_alerted = any(
            a.allergen.lower() == allergy["allergen"].lower()
            and med["name"].lower() in a.text.lower()
            for a in case.alerts
        )
        if already_alerted:
            continue

        assessment = await _assess_conflict(allergy["allergen"], med["name"])
        if not assessment.get("conflict"):
            continue

        alert = Alert(
            id=str(uuid.uuid4()),
            text=_spoken_alert(case, allergy, med, assessment),
            allergen=allergy["allergen"],
            drug_class=assessment.get("drug_class", ""),
            source=(allergy.get("source") or "").strip(),
            timestamp=time.time(),
        )
        case.alerts.append(alert)
        new_alerts.append(alert)

    return new_alerts
