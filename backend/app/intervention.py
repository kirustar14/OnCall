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
import re
import time
import uuid

import anthropic

from app.case_store import Alert, CaseState, next_alert_seq
from app.config import ANTHROPIC_API_KEY, CLAUDE_MODEL
from app.rxnav import drug_classes, epc_classes

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


def _allergen_keys(allergen: str) -> list[str]:
    """Resolve a spoken allergy phrase to cross-reactivity keys.

    What the room says is never a bare substance name. A live run produced
    "Penicillin (severe, anaphylaxis as a child with hospitalization)", and an
    exact dict lookup on that whole string missed — so the ordered penicillin
    was never even checked. Match any known class named anywhere in the phrase,
    and fall back to the leading substance before any qualifier.
    """
    text = allergen.strip().lower()
    matched = [key for key in CROSS_REACTIVITY if key in text]
    if matched:
        return matched
    head = re.split(r"[(,;/]| - ", text)[0].strip()
    return [head] if head else []


def _find_conflicts(allergies: list[dict], medications: list[dict]) -> list[tuple[dict, dict]]:
    conflicts = []
    for allergy in allergies:
        keys = _allergen_keys(allergy["allergen"])
        trigger_words = {w for key in keys for w in CROSS_REACTIVITY.get(key, [key])}
        trigger_words.update(keys)
        for med in medications:
            med_name = med["name"].strip().lower()
            if any(word and word in med_name for word in trigger_words):
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
                "ONE short clause, EIGHT WORDS MAXIMUM, stating the class relationship as fact. "
                'Start with "That is" — the drug name is already said immediately before this, and '
                'repeating it wastes the only seconds you have. e.g. "That is a penicillin" or '
                '"That is a penicillin-class antibacterial". This is spoken over a working trauma '
                "team, so length is a safety property: a long alert is one nobody hears the end of. "
                "No pharmacology, no mechanism, and never what to give instead. "
                "Empty string if there is no conflict."
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

You may be given the FDA's own drug classes for the ordered medication, retrieved from NIH \
RxNav. When they are present, GROUND YOUR ANSWER IN THEM and name the Established \
Pharmacologic Class (EPC) in your basis clause — an assertion you can point at beats one the \
clinician has to take on trust. When no classes were returned, reason from pharmacology and \
say plainly in the basis that the classification is unverified.

You are NOT recommending treatment. Do not name an alternative drug, do not suggest what to \
give instead, and do not tell the clinician what to do. State the class relationship as a fact \
and stop. The clinician decides.

Set conflict=false where there is no genuine class relationship. Do not manufacture one.

Writing style: avoid em dashes. Use a comma, a full stop, or a colon instead. Keep punctuation plain so a text-to-speech voice reads it naturally and a clinician scanning the screen is not slowed down by ornament.
"""


async def _assess_conflict(allergen: str, medication: str) -> dict:
    """Resolve the drug against FDA classification first, then reason."""
    if _client is None:
        logger.warning("ANTHROPIC_API_KEY not set — skipping conflict check")
        return {"conflict": False, "drug_class": "", "basis": "", "fda_classes": []}

    # Authoritative classification BEFORE the model gets an opinion. A failed
    # lookup must not block the check — it just means the answer is unverified.
    fda_classes = await drug_classes(medication)
    fda_block = (
        "FDA drug classes for the ordered medication (NIH RxNav):\n  "
        + "\n  ".join(fda_classes)
        if fda_classes
        else "FDA drug classes: none returned — classification is UNVERIFIED."
    )

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
            # Stable prefix — cached across every conflict check.
            system=[
                {
                    "type": "text",
                    "text": CONFLICT_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Documented allergy: {allergen}\n"
                        f"Medication just ordered: {medication}\n"
                        f"{fda_block}\n\n"
                        "Does the ordered medication belong to the class this patient is "
                        "documented allergic to?"
                    ),
                }
            ],
        )
        text = next(b.text for b in response.content if b.type == "text")
        return json.loads(text)

    try:
        verdict = await asyncio.to_thread(_call)
        verdict["fda_classes"] = fda_classes
        return verdict
    except Exception:
        logger.exception("conflict assessment failed")
        return {"conflict": False, "drug_class": "", "basis": "", "fda_classes": fda_classes}


def _time_ago(timestamp: float) -> str:
    seconds = max(0, time.time() - timestamp)
    if seconds < 60:
        return "moments ago"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = int(minutes // 60)
    return f"{hours} hour{'s' if hours != 1 else ''} ago"


MAX_SPOKEN_WORDS = 30


def _spoken_alert(case: CaseState, allergy: dict, med: dict, assessment: dict) -> str:
    """The order, the class relationship, then who said it and when. Stop.

    Ordered that way deliberately: the class claim is the actionable half and
    goes first, provenance follows so the clinician can weigh it. Length is a
    safety property here, not a style preference — an alert that runs twenty
    seconds is one the room talks over. No instruction, no recommendation.
    """
    reaction = (allergy.get("reaction") or "").strip()
    # "penicillin allergy, anaphylaxis" is redundant when the reaction says it.
    allergy_phrase = (
        f"Documented {allergy['allergen']} {reaction}"
        if reaction
        else f"Documented {allergy['allergen']} allergy"
    )

    source = (allergy.get("source") or "").strip()
    attribution = f" from {source}" if source else ""

    basis = (assessment.get("basis") or "").rstrip(".")
    if not basis and assessment.get("drug_class"):
        basis = f"That is in the {assessment['drug_class']} class"

    parts = [f"{case.spoken_label}. {med['name']} just ordered."]
    if basis:
        parts.append(basis)
    parts.append(f"{allergy_phrase}, {_time_ago(allergy['timestamp'])}{attribution}")

    spoken = " ".join(p if p.endswith(".") else p + "." for p in parts)

    # Backstop: the model is told eight words for the basis, but a runaway one
    # must not turn the interrupt into a monologue.
    words = spoken.split()
    if len(words) > MAX_SPOKEN_WORDS:
        spoken = " ".join(words[:MAX_SPOKEN_WORDS]).rstrip(",;") + "."
    return spoken


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

        fda = assessment.get("fda_classes") or []
        alert = Alert(
            id=str(uuid.uuid4()),
            text=_spoken_alert(case, allergy, med, assessment),
            timestamp=time.time(),
            seq=next_alert_seq(),
            # A documented allergy against an ordered drug of that class is the
            # one thing here that always jumps the speaker queue.
            urgency="critical",
            kind="verified_conflict",
            reasoning=assessment.get("basis", ""),
            allergen=allergy["allergen"],
            drug_class=assessment.get("drug_class", ""),
            source=(allergy.get("source") or "").strip(),
            fda_classes=fda,
            # The difference between "the model says so" and "the FDA says so".
            fda_verified=bool(epc_classes(fda)),
        )
        case.alerts.append(alert)
        new_alerts.append(alert)

    return new_alerts
