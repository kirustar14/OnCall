"""Claude extraction agent: turns finalized transcript text into structured
clinical facts AND work items, then writes each into Medplum under the case's
Encounter.

Extraction is stateful. The open ledger is passed *in* with every call, because
"I've got it" only means something against a list of things that are open.
"""

import asyncio
import json
import logging
import time

import anthropic

from app.case_store import CaseState, WorkItem
from app.config import ANTHROPIC_API_KEY, CLAUDE_MODEL
from app.medplum_client import medplum_client
from app.moss_client import moss_client

logger = logging.getLogger("oncall.extraction")

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

EMPTY_EXTRACTION = {
    "allergies": [],
    "vitals": [],
    "medications": [],
    "notes": "",
    "case_details": {},
    "tasks": [],
    "resolutions": [],
}

SYSTEM_PROMPT = """You maintain the shared state of an active ER resuscitation from what the team \
says out loud (EMS handoff, nurse relay, physician orders, bystander statements). You track two \
things: clinical FACTS, and WORK.

Extract ONLY what is explicitly stated in THIS segment. Do not infer, do not carry forward facts \
from outside the given text, and do not invent anything not said.

## FACTS
- "allergies": known drug/food/environmental allergies. Each: {"allergen": <SUBSTANCE ONLY>, \
"reaction": <e.g. "anaphylaxis", or "" if not stated>, "severity": <"severe"|"moderate"|"mild"|"">, \
"source": <who said it, e.g. "EMS handoff", "nurse relay", "parent via nurse", "physician">}
  CRITICAL: "allergen" is the bare substance name and NOTHING else — "Penicillin", never \
"Penicillin (severe, anaphylaxis as a child)". Severity and reaction have their own fields. \
Downstream safety checks match the ordered drug against this field by drug class; qualifiers \
packed into it cause the check to silently miss a real contraindication.
- "vitals": vital signs (BP, HR, RR, SpO2, temp, GCS, pain score). Each: {"name": <e.g. "BP">, \
"value": <e.g. "120/80">, "source": <as above>}
- "medications": medications ordered or given. Each: {"name": <drug, include dose/route if \
stated>, "status": "ordered" or "given", "source": <as above>}
- "notes": any other explicitly stated case detail (age, sex, mechanism, chief complaint, PMH) \
as a short sentence. Empty string if nothing notable.
- "case_details": {"name": <string or "">, "age": <string or "">, "sex": <string or "">, \
"mechanism": <string or "">}
  "name" is the patient's own name, and only when someone actually states it as such ("this is \
Ava Lennox", "the mother says Ava has..."). It is NEVER a clinician's name, a crew member's, or \
a unit callsign: an EMS handoff opens by identifying the ambulance, not the patient, so "Medic 6" \
and anything a garbled transcript makes of it is the crew, not a name. Leave it empty rather than \
guessing; the wrong name is worse than none, because it silently attaches this patient's facts to \
somebody else's record.
  A number is only an age if it is stated as one ("nineteen year old female"). A GCS, a heart \
rate, a blood pressure, a respiratory rate or a saturation is NEVER an age, even when a segment \
boundary leaves it stranded on its own — "GCS 13" followed by "she's confused" describes a \
nineteen-year-old with a GCS of 13, not a thirteen-year-old. Leave "age" empty rather than \
guessing; a wrong age is worse than a missing one.

## WORK (new items requested in this segment)
- "task": an action someone must do. "Call respiratory", "Get two units up here".
- "uncertainty": an open question with no answer yet. "Find out whether she's anticoagulated", \
"Does anyone know her history?" These are the dangerous ones — a question nobody owns is a \
question nobody answers.
- "conditional": an action tied to a future trigger. "Repeat the pressure in five minutes" \
(put "in five minutes" in "trigger").

CRITICAL RULE ON OWNERSHIP: "owner" is the person NAMED to do it. If no one was named, "owner" \
MUST be an empty string. Do NOT guess an owner from whoever happens to be speaking. An unowned \
task is a real and important state that the system needs to detect.

CRITICAL RULE ON WHAT COUNTS AS WORK: only extract work somebody actually ASKED FOR out loud. \
Do NOT create a work item out of a clinical need you infer from the situation. "No antibiotics \
given" is a fact about what happened, not a request to give them. "She can't give us a history" \
is a fact about the patient, not a request to go find one. If nobody asked for it, it does not \
go in the ledger — inventing work makes the ledger untrustworthy and buries the items that were \
genuinely requested.

An order to give a drug is a medication, not a task. Do not also emit it as work.

## RESOLUTIONS (things in the open ledger that this segment resolves)
- "I've got it", "On it", "I'll call them" -> status "acknowledged", owner = the speaker.
- "Respiratory's at the bedside", "Pressure's 100 over 60 now" -> status "completed".
- "She's not on anticoagulants", "Mom says no blood thinners" -> status "answered".
- Only emit a resolution when it clearly maps to an item in the open ledger you were given. \
NEVER invent a task_id. If nothing matches, return an empty resolutions array.

Return empty arrays/strings where nothing applies. Silence is a valid answer.

Writing style: avoid em dashes. Use a comma, a full stop, or a colon instead. Keep punctuation plain so a text-to-speech voice reads it naturally and a clinician scanning the screen is not slowed down by ornament.
"""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "allergies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "allergen": {"type": "string", "description": "Bare substance name only."},
                    "reaction": {"type": "string"},
                    "severity": {"type": "string", "enum": ["severe", "moderate", "mild", ""]},
                    "source": {"type": "string"},
                },
                "required": ["allergen", "reaction", "severity", "source"],
                "additionalProperties": False,
            },
        },
        "vitals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "value": {"type": "string"},
                    "source": {"type": "string"},
                },
                "required": ["name", "value", "source"],
                "additionalProperties": False,
            },
        },
        "medications": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "status": {"type": "string", "enum": ["ordered", "given"]},
                    "source": {"type": "string"},
                },
                "required": ["name", "status", "source"],
                "additionalProperties": False,
            },
        },
        "notes": {"type": "string"},
        "case_details": {
            "type": "object",
            "properties": {
                # Identity is what lets semantic recall reach across cases: a moss
                # document is tagged with the patient name, so "any prior drug
                # reactions" can surface a note from a different encounter. With no
                # name the search silently narrows to the current case only.
                "name": {"type": "string"},
                "age": {"type": "string"},
                "sex": {"type": "string"},
                "mechanism": {"type": "string"},
            },
            "required": ["name", "age", "sex", "mechanism"],
            "additionalProperties": False,
        },
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["task", "uncertainty", "conditional"]},
                    "action": {"type": "string"},
                    "owner": {"type": "string"},
                    "requested_by": {"type": "string"},
                    "trigger": {"type": "string"},
                    "why_it_matters": {"type": "string"},
                    "source_quote": {"type": "string"},
                },
                "required": [
                    "kind",
                    "action",
                    "owner",
                    "requested_by",
                    "trigger",
                    "why_it_matters",
                    "source_quote",
                ],
                "additionalProperties": False,
            },
        },
        "resolutions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["acknowledged", "completed", "answered"],
                    },
                    "owner": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["task_id", "status", "owner", "evidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "allergies",
        "vitals",
        "medications",
        "notes",
        "case_details",
        "tasks",
        "resolutions",
    ],
    "additionalProperties": False,
}


def _normalize(text: str) -> str:
    """Loose key for dedup. 'Penicillin' / 'penicillin allergy' / 'PENICILLIN.'
    all collapse to the same key — an exact lowercase match double-writes."""
    cleaned = "".join(c for c in text.lower() if c.isalnum() or c.isspace())
    tokens = [t for t in cleaned.split() if t not in {"allergy", "allergic", "to", "a", "an", "the"}]
    return " ".join(sorted(tokens))


async def extract_from_segment(
    transcript_segment: str, open_ledger: list[dict], speaker_label: str = ""
) -> dict:
    """Blocking Anthropic call run in a thread; returns the parsed structured dict."""
    if _client is None:
        logger.warning("ANTHROPIC_API_KEY not set — skipping extraction")
        return dict(EMPTY_EXTRACTION)

    ledger_text = json.dumps(open_ledger, indent=2) if open_ledger else "(empty)"
    speaker_line = (
        f"Diarization says this segment was spoken by: {speaker_label}\n"
        "Use that for `requested_by` and for the owner on a claim like \"I've got it\" — unless "
        "the words themselves name a different source (\"Mom just told me\" means the source is "
        "the mother, relayed by this speaker).\n\n"
        if speaker_label
        else ""
    )

    def _call() -> dict:
        response = _client.messages.create(
            model=CLAUDE_MODEL,
            # No `thinking` param: adaptive is the Opus 5 default. Explicitly
            # disabling it risks <thinking> tags leaking into the response and
            # corrupting the JSON parse below. effort=low keeps it fast.
            max_tokens=4096,
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
            },
            # This prompt is ~1500 tokens and byte-identical on every utterance,
            # so it caches. Everything that varies — the open ledger, the speaker,
            # the transcript — lives in the user message, after the breakpoint.
            # Caching is a prefix match: one byte of drift here and nothing hits.
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Open ledger (ONLY these ids may appear in resolutions):\n{ledger_text}\n\n"
                        f"{speaker_line}"
                        f"New transcript segment:\n\n{transcript_segment}"
                    ),
                }
            ],
        )
        text = next(b.text for b in response.content if b.type == "text")
        return json.loads(text)

    try:
        return await asyncio.to_thread(_call)
    except Exception:
        logger.exception("extraction call failed")
        return dict(EMPTY_EXTRACTION)


async def _ensure_patient_and_encounter(case: CaseState) -> None:
    if not medplum_client.configured:
        return
    if case.patient_id and case.encounter_id:
        return
    try:
        patient_id, encounter_id = await medplum_client.create_patient_and_encounter(
            case.case_id, case.case_details.get("age", "") or f"Case {case.case_id[:8]}"
        )
        case.patient_id = patient_id
        case.encounter_id = encounter_id
    except Exception:
        logger.exception("medplum: failed to create Patient/Encounter for case %s", case.case_id)


async def _owner_reference(case: CaseState, owner_label: str) -> str | None:
    """Task.owner is a Reference, not a string — an owner needs a real
    Practitioner resource. Created once per role per case and cached."""
    if not owner_label or not medplum_client.configured:
        return None
    existing = case.practitioner_ids.get(owner_label)
    if existing:
        return existing
    try:
        practitioner_id = await medplum_client.ensure_practitioner(owner_label)
        case.practitioner_ids[owner_label] = practitioner_id
        return practitioner_id
    except Exception:
        logger.exception("medplum: failed to create Practitioner for %s", owner_label)
        return None


async def _persist_work_item(case: CaseState, item: WorkItem) -> None:
    if not (medplum_client.configured and case.patient_id and case.encounter_id):
        return
    owner_id = await _owner_reference(case, item.owner)
    try:
        task = await medplum_client.write_task(
            patient_id=case.patient_id,
            encounter_id=case.encounter_id,
            action=item.action,
            kind=item.kind,
            why_it_matters=item.why_it_matters,
            owner_practitioner_id=owner_id,
            requested_by=item.requested_by,
            trigger=item.trigger,
            opened_at=item.opened_at,
        )
        item.fhir_task_id = task.get("id")
        await medplum_client.write_provenance(
            target_ref=f"Task/{item.fhir_task_id}",
            source=item.requested_by or "clinician transcript",
            quote=item.source_quote,
            recorded_at=item.opened_at,
        )
    except Exception:
        logger.exception("medplum: failed to write Task for case %s", case.case_id)


async def apply_resolution(
    case: CaseState,
    item: WorkItem,
    status: str,
    owner: str,
    evidence: str,
    evidence_source: str,
) -> None:
    """Move a work item forward and mirror the change into Medplum."""
    item.status = status
    if owner:
        item.owner = owner
    item.evidence = evidence
    item.evidence_source = evidence_source
    item.resolved_at = None if status == "acknowledged" else time.time()

    if not (medplum_client.configured and item.fhir_task_id):
        return
    owner_id = await _owner_reference(case, item.owner)
    try:
        await medplum_client.update_task_status(
            task_id=item.fhir_task_id,
            work_status=status,
            owner_practitioner_id=owner_id,
            evidence=evidence,
            evidence_source=evidence_source,
            at=time.time(),
        )
        await medplum_client.write_provenance(
            target_ref=f"Task/{item.fhir_task_id}",
            source=item.owner or "clinician transcript",
            quote=evidence,
            recorded_at=time.time(),
            activity=status,
        )
    except Exception:
        logger.exception("medplum: failed to update Task %s", item.fhir_task_id)


async def run_extraction_and_persist(
    case: CaseState, transcript_segment: str, speaker_label: str = ""
) -> dict:
    """Extract facts + work from a segment, merge into case state, write to
    Medplum. Returns the raw extraction so callers can react.

    `speaker_label` comes from diarization + the case's role mapping. It is used
    only as a fallback attribution — the model's own reading of who was speaking
    wins, because "Mom told me" is a different source than the nurse saying it.
    """

    extracted = await extract_from_segment(
        transcript_segment, case.open_ledger_for_prompt(), speaker_label
    )
    now = time.time()

    await _ensure_patient_and_encounter(case)

    # --- facts ---------------------------------------------------------------

    # Identity is resolved BEFORE any fact is persisted below. Every moss document
    # is tagged with the patient name, and a single segment can state the name
    # alongside a fact ("this is Ava Lennox, she has a penicillin allergy"). If the
    # name were recorded afterwards, that fact would be indexed anonymously and
    # would never surface on a later cross-case lookup for this patient.
    details = extracted.get("case_details", {}) or {}
    for key in ("name", "age", "sex", "mechanism"):
        val = (details.get(key) or "").strip()
        if val and not case.case_details.get(key):
            case.case_details[key] = val
            if key == "name" and case.patient_id:
                try:
                    await medplum_client.update_patient_name(case.patient_id, val)
                except Exception:
                    logger.exception(
                        "medplum: failed to update patient name for case %s", case.case_id
                    )

    patient_name = case.case_details.get("name")

    def _index(fact_type: str, text: str) -> None:
        """Fire-and-forget semantic indexing.

        Deliberately not awaited. index_fact makes two network round trips (write,
        then reload so the very next query sees it), and this function sits on the
        path between a drug being ordered and the contraindication being spoken —
        a path measured at 26s that took real work to get there. Indexing is
        best-effort by design and already swallows its own failures, so the only
        thing awaiting it would buy is latency.
        """
        asyncio.create_task(moss_client.index_fact(case.case_id, patient_name, fact_type, text, now))

    for allergy in extracted.get("allergies", []):
        allergen = allergy.get("allergen", "").strip()
        if not allergen:
            continue
        key = _normalize(allergen)
        if any(_normalize(a["allergen"]) == key for a in case.allergies):
            continue
        entry = {
            "allergen": allergen,
            "reaction": allergy.get("reaction", "").strip(),
            "severity": allergy.get("severity", "").strip(),
            "source": allergy.get("source", "clinician transcript"),
            "timestamp": now,
        }
        case.allergies.append(entry)
        if case.patient_id and case.encounter_id:
            try:
                await medplum_client.write_allergy(
                    case.patient_id,
                    case.encounter_id,
                    allergen,
                    entry["source"],
                    now,
                    reaction=entry["reaction"],
                    severity=entry["severity"],
                )
            except Exception:
                logger.exception("medplum: failed to write allergy for case %s", case.case_id)
        _index("allergy", f"Patient reported {allergen} allergy (source: {entry['source']})")

    for vital in extracted.get("vitals", []):
        name = vital.get("name", "").strip()
        value = vital.get("value", "").strip()
        if not name or not value:
            continue
        entry = {
            "name": name,
            "value": value,
            "source": vital.get("source", "clinician transcript"),
            "timestamp": now,
        }
        case.vitals.append(entry)
        if case.patient_id and case.encounter_id:
            try:
                await medplum_client.write_vital(
                    case.patient_id, case.encounter_id, name, value, entry["source"], now
                )
            except Exception:
                logger.exception("medplum: failed to write vital for case %s", case.case_id)
        _index("vital", f"{name} recorded as {value} (source: {entry['source']})")

    for med in extracted.get("medications", []):
        name = med.get("name", "").strip()
        if not name:
            continue
        key = _normalize(name)
        if any(_normalize(m["name"]) == key for m in case.medications):
            continue
        entry = {
            "name": name,
            "status": med.get("status", "ordered"),
            "source": med.get("source", "clinician transcript"),
            "timestamp": now,
        }
        case.medications.append(entry)
        if case.patient_id and case.encounter_id:
            try:
                await medplum_client.write_medication_request(
                    case.patient_id, case.encounter_id, name, entry["source"], now
                )
            except Exception:
                logger.exception("medplum: failed to write medication for case %s", case.case_id)
        _index("medication", f"{name} {entry['status']} (source: {entry['source']})")

    notes = extracted.get("notes", "").strip()
    if notes:
        case.notes.append(notes)
        # Notes are the highest-value thing in this index: they are free text, so
        # they are exactly what a structured Medplum lookup cannot reach.
        _index("note", notes)

    # --- work ----------------------------------------------------------------

    for raw in extracted.get("tasks", []):
        action = raw.get("action", "").strip()
        if not action:
            continue
        key = _normalize(action)
        if any(_normalize(w.action) == key for w in case.work if w.is_open):
            continue
        item = WorkItem(
            id=case.new_work_id(),
            kind=raw.get("kind", "task"),
            action=action,
            owner=raw.get("owner", "").strip(),
            requested_by=raw.get("requested_by", "").strip(),
            trigger=raw.get("trigger", "").strip(),
            why_it_matters=raw.get("why_it_matters", "").strip(),
            source_quote=raw.get("source_quote", "").strip(),
            opened_at=now,
        )
        case.work.append(item)
        await _persist_work_item(case, item)

    for res in extracted.get("resolutions", []):
        item = case.find_work(res.get("task_id", ""))
        if item is None:
            # The model referenced an id that isn't in the open ledger. Drop it
            # rather than guessing — a wrong completion is worse than a missed one.
            logger.warning("resolution referenced unknown task_id %r", res.get("task_id"))
            continue
        await apply_resolution(
            case,
            item,
            status=res.get("status", "acknowledged"),
            owner=res.get("owner", "").strip(),
            evidence=res.get("evidence", "").strip(),
            evidence_source="speech",
        )

    return extracted
