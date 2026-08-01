"""Claude extraction agent: turns finalized transcript text into structured
clinical facts, then writes each fact into Medplum under the case's Encounter."""

import logging
import time

import anthropic

from app.case_store import CaseState
from app.config import ANTHROPIC_API_KEY, CLAUDE_MODEL
from app.medplum_client import medplum_client
from app.moss_client import moss_client

logger = logging.getLogger("servare.extraction")

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

SYSTEM_PROMPT = """You are a clinical extraction agent listening to a live ER transcript \
(EMS handoff, nurse relay, physician orders, bystander statements). Given a new segment of \
transcript, extract ONLY facts that are explicitly stated in THIS segment — do not infer or \
carry forward facts from outside the given text, and do not invent anything not said.

Extract into these fields:
- "allergies": known drug/food/environmental allergies. Each item: {"allergen": <string>, \
"source": <who said it, e.g. "EMS handoff", "nurse relay", "parent via nurse", "physician">}
- "vitals": vital signs (BP, HR, RR, SpO2, temp, GCS, pain score, etc). Each item: \
{"name": <e.g. "BP", "HR">, "value": <e.g. "120/80", "98 bpm">, "source": <as above>}
- "medications": medications ordered or given. Each item: {"name": <drug name, include dose/route \
if stated>, "status": "ordered" or "given", "source": <as above>}
- "notes": any other explicitly stated case detail (age, sex, mechanism of injury, chief complaint, \
past medical history) as a short free-text sentence. Empty string if nothing else notable.
- "case_details": structured demographic/incident facts if stated: {"name": <patient's name, string \
or "" if not stated>, "age": <string or "">, "sex": <string or "">, "mechanism": <string or "">}

If a category has nothing new in this segment, return an empty array/string for it. Infer "source" \
from context (e.g. "medics say", "the mother told the nurse") — default to "clinician transcript" if \
no source is stated."""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "allergies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "allergen": {"type": "string"},
                    "source": {"type": "string"},
                },
                "required": ["allergen", "source"],
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
                "name": {"type": "string"},
                "age": {"type": "string"},
                "sex": {"type": "string"},
                "mechanism": {"type": "string"},
            },
            "required": ["name", "age", "sex", "mechanism"],
            "additionalProperties": False,
        },
    },
    "required": ["allergies", "vitals", "medications", "notes", "case_details"],
    "additionalProperties": False,
}


async def extract_from_segment(transcript_segment: str) -> dict:
    """Blocking Anthropic call run in a thread; returns the parsed structured dict."""
    import asyncio
    import json

    if _client is None:
        logger.warning("ANTHROPIC_API_KEY not set — skipping extraction")
        return {"allergies": [], "vitals": [], "medications": [], "notes": "", "case_details": {}}

    def _call() -> dict:
        response = _client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2048,
            thinking={"type": "disabled"},
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"New transcript segment:\n\n{transcript_segment}"}],
        )
        text = next(b.text for b in response.content if b.type == "text")
        return json.loads(text)

    return await asyncio.to_thread(_call)


async def run_extraction_and_persist(case: CaseState, transcript_segment: str) -> tuple[dict, list[str]]:
    """Extract structured facts from a transcript segment, merge into the case's
    in-memory structured data, and write each fact to Medplum. Returns the raw
    extraction plus a list of short human-readable descriptions of facts that were
    ACTUALLY NEW (not already known) — this is what triggers the reasoning agent,
    so a repeated/already-known fact doesn't cause a redundant agent step."""

    extracted = await extract_from_segment(transcript_segment)
    now = time.time()
    new_facts: list[str] = []

    if medplum_client.configured and (case.patient_id is None or case.encounter_id is None):
        try:
            patient_id, encounter_id = await medplum_client.create_patient_and_encounter(
                case.case_id, case.case_details.get("age", "") or f"Case {case.case_id[:8]}"
            )
            case.patient_id = patient_id
            case.encounter_id = encounter_id
        except Exception:
            logger.exception("medplum: failed to create Patient/Encounter for case %s", case.case_id)

    # case_details (especially "name") is set FIRST, before any allergy/vital/medication/note
    # processing below — those all tag their moss.dev doc with case.case_details.get("name"),
    # and a segment that states the name alongside a fact (e.g. "This is Jordan Lee, he got
    # hives after amoxicillin") must have the name already recorded when that fact is indexed,
    # not after.
    details = extracted.get("case_details", {}) or {}
    for key in ("name", "age", "sex", "mechanism"):
        val = (details.get(key) or "").strip()
        if val and not case.case_details.get(key):
            case.case_details[key] = val
            new_facts.append(f"Patient {key} recorded: {val}")
            if key == "name" and medplum_client.configured and case.patient_id:
                try:
                    await medplum_client.update_patient_name(case.patient_id, val)
                except Exception:
                    logger.exception("medplum: failed to update patient name for case %s", case.case_id)

    for allergy in extracted.get("allergies", []):
        allergen = allergy.get("allergen", "").strip()
        if not allergen:
            continue
        if any(a["allergen"].lower() == allergen.lower() for a in case.allergies):
            continue
        entry = {"allergen": allergen, "source": allergy.get("source", "clinician transcript"), "timestamp": now}
        case.allergies.append(entry)
        new_facts.append(f"New allergy recorded: {allergen} (source: {entry['source']})")
        if case.patient_id and case.encounter_id:
            try:
                await medplum_client.write_allergy(case.patient_id, case.encounter_id, allergen, entry["source"], now)
            except Exception:
                logger.exception("medplum: failed to write allergy for case %s", case.case_id)
        await moss_client.index_fact(
            case.case_id,
            case.case_details.get("name"),
            "allergy",
            f"Patient reported {allergen} allergy (source: {entry['source']})",
            now,
        )

    for vital in extracted.get("vitals", []):
        name = vital.get("name", "").strip()
        value = vital.get("value", "").strip()
        if not name or not value:
            continue
        entry = {"name": name, "value": value, "source": vital.get("source", "clinician transcript"), "timestamp": now}
        case.vitals.append(entry)
        new_facts.append(f"New vital recorded: {name} {value} (source: {entry['source']})")
        if case.patient_id and case.encounter_id:
            try:
                await medplum_client.write_vital(case.patient_id, case.encounter_id, name, value, entry["source"], now)
            except Exception:
                logger.exception("medplum: failed to write vital for case %s", case.case_id)
        await moss_client.index_fact(
            case.case_id,
            case.case_details.get("name"),
            "vital",
            f"{name} recorded as {value} (source: {entry['source']})",
            now,
        )

    for med in extracted.get("medications", []):
        name = med.get("name", "").strip()
        if not name:
            continue
        if any(m["name"].lower() == name.lower() for m in case.medications):
            continue
        entry = {
            "name": name,
            "status": med.get("status", "ordered"),
            "source": med.get("source", "clinician transcript"),
            "timestamp": now,
        }
        case.medications.append(entry)
        new_facts.append(f"New medication recorded: {name} — {entry['status']} (source: {entry['source']})")
        if case.patient_id and case.encounter_id:
            try:
                await medplum_client.write_medication_request(
                    case.patient_id, case.encounter_id, name, entry["source"], now
                )
            except Exception:
                logger.exception("medplum: failed to write medication for case %s", case.case_id)
        await moss_client.index_fact(
            case.case_id,
            case.case_details.get("name"),
            "medication",
            f"{name} {entry['status']} (source: {entry['source']})",
            now,
        )

    notes = extracted.get("notes", "").strip()
    if notes:
        case.notes.append(notes)
        new_facts.append(f"New note: {notes}")
        await moss_client.index_fact(case.case_id, case.case_details.get("name"), "note", notes, now)

    return extracted, new_facts
