"""Verification for the ledger layer.

Runs with no API keys and no network: the Medplum client's HTTP layer is
stubbed so we can assert the exact FHIR bodies we would POST.

Enum values below are transcribed from Medplum's generated FHIR R4 types
(packages/fhirtypes/dist/Task.d.ts, Provenance.d.ts) — the same definitions
the server validates against.

    ./venv/bin/python -m tests.test_ledger
"""

import asyncio
import inspect
import json
import os
import sys
import tempfile
import time
from dataclasses import fields as dc_fields

from app.case_store import (
    OPEN_STATUSES,
    WORK_STATUS_TO_FHIR,
    Alert,
    CaseState,
    CaseStore,
    WorkItem,
)
from app.deepgram_stt import _dominant_speaker
from app.extraction import _normalize, extract_from_segment
from app.intervention import (
    CONFLICT_SCHEMA,
    CONFLICT_SYSTEM_PROMPT,
    _allergen_keys,
    _assess_conflict,
    _find_conflicts,
    _spoken_alert,
)
from app.medplum_client import MedplumClient, _trigger_seconds
from app.rxnav import epc_classes, normalize_drug_name
from app.segmenter import MAX_CHARS, QUIET_SECONDS, UtteranceBuffer
from app.watchdog import _find_orphan, _prompt_text

# --- ground truth from Medplum's generated FHIR types -------------------------

TASK_STATUS_VALUES = {
    "draft", "requested", "received", "accepted", "rejected", "ready",
    "cancelled", "in-progress", "on-hold", "failed", "completed", "entered-in-error",
}
TASK_INTENT_VALUES = {
    "unknown", "proposal", "plan", "order", "original-order",
    "reflex-order", "filler-order", "instance-order", "option",
}
TASK_PRIORITY_VALUES = {"routine", "urgent", "asap", "stat"}
TASK_REQUIRED = {"resourceType", "status", "intent"}
PROVENANCE_REQUIRED = {"resourceType", "target", "recorded", "agent"}

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
    else:
        FAILED.append(f"{name}{' — ' + detail if detail else ''}")


# --- 1. dedup normalization (the double-write bug) ---------------------------


def test_normalize() -> None:
    same = ["Penicillin", "penicillin allergy", "PENICILLIN.", "allergic to penicillin"]
    keys = {_normalize(s) for s in same}
    check("dedup: penicillin variants collapse to one key", len(keys) == 1, f"got {keys}")

    check(
        "dedup: distinct allergens stay distinct",
        _normalize("penicillin") != _normalize("sulfa drugs"),
    )
    check(
        "dedup: word order does not matter",
        _normalize("sulfa drugs") == _normalize("drugs sulfa"),
    )


# --- 2. spoken trigger -> due date -------------------------------------------


def test_trigger_seconds() -> None:
    cases = [
        ("in five minutes", 300),
        ("in 5 minutes", 300),
        ("in ten minutes", 600),
        ("in 90 seconds", 90),
        ("in two hours", 7200),
        ("", 300),               # default
        ("shortly", 300),        # unparseable -> default
    ]
    for text, expected in cases:
        got = _trigger_seconds(text)
        check(f"trigger: {text!r} -> {expected}s", got == expected, f"got {got}")


# --- 3. work item state ------------------------------------------------------


def test_work_item_states() -> None:
    orphan = WorkItem(id="a", kind="uncertainty", action="Determine anticoagulation status")
    check("orphan: unowned+open is an orphan", orphan.is_orphan)
    check("orphan: unowned+open is open", orphan.is_open)

    owned = WorkItem(id="b", kind="task", action="Call ortho", owner="NURSE OKAFOR")
    check("orphan: owned is not an orphan", not owned.is_orphan)

    done = WorkItem(id="c", kind="task", action="X", status="completed")
    check("orphan: completed is not open", not done.is_open)
    check("orphan: completed is not an orphan", not done.is_orphan)

    check("status map covers every open status", all(s in WORK_STATUS_TO_FHIR for s in OPEN_STATUSES))
    check(
        "status map: every value is a legal FHIR Task.status",
        set(WORK_STATUS_TO_FHIR.values()) <= TASK_STATUS_VALUES,
        f"got {set(WORK_STATUS_TO_FHIR.values()) - TASK_STATUS_VALUES}",
    )
    check("status map: open -> ready", WORK_STATUS_TO_FHIR["open"] == "ready")
    check("status map: acknowledged -> in-progress", WORK_STATUS_TO_FHIR["acknowledged"] == "in-progress")
    check("status map: answered -> completed", WORK_STATUS_TO_FHIR["answered"] == "completed")


# --- 4. watchdog selection ---------------------------------------------------


def test_watchdog() -> None:
    now = time.time()
    case = CaseState(case_id="deadbeef")

    fresh = WorkItem(id="1", kind="task", action="Fresh", opened_at=now - 1)
    stale = WorkItem(id="2", kind="uncertainty", action="Anticoagulated?", opened_at=now - 999)
    owned = WorkItem(id="3", kind="task", action="Call ortho", owner="OKAFOR", opened_at=now - 999)
    asked = WorkItem(id="4", kind="task", action="Already asked", opened_at=now - 999, prompted_at=now - 5)
    case.work = [fresh, owned, asked, stale]

    picked = _find_orphan(case, now)
    check("watchdog: picks the stale unowned item", picked is stale, f"picked {picked and picked.action!r}")

    case.work = [fresh, owned, asked]
    check("watchdog: nothing to pick when all are fresh/owned/asked", _find_orphan(case, now) is None)

    older = WorkItem(id="5", kind="task", action="Older", opened_at=now - 5000)
    case.work = [stale, older]
    check("watchdog: picks the oldest orphan first", _find_orphan(case, now) is older)

    check(
        "watchdog: uncertainty gets question phrasing",
        "unanswered" in _prompt_text(case, stale),
        _prompt_text(case, stale),
    )
    check("watchdog: prompt asks, never assigns", "Who is taking it?" in _prompt_text(case, stale))

    # open_ledger_for_prompt is what bounds the model's resolution ids
    case.work = [stale, owned, WorkItem(id="9", kind="task", action="Done", status="completed")]
    ledger = case.open_ledger_for_prompt()
    check("ledger: only open items are offered for resolution", {e["id"] for e in ledger} == {"2", "3"})
    check("ledger: entries carry id/kind/action/owner", set(ledger[0]) == {"id", "kind", "action", "owner"})


# --- 5. the FHIR bodies we would actually POST -------------------------------


class CapturingClient(MedplumClient):
    """Intercepts writes so we can inspect the exact payload, no network."""

    def __init__(self) -> None:
        super().__init__()
        self.created: list[tuple[str, dict]] = []
        self._agent_device_id = "device-1"

    async def create_resource(self, resource_type, body):
        self.created.append((resource_type, body))
        return {"id": f"{resource_type.lower()}-1", **body}

    async def read_resource(self, resource_type, resource_id):
        return {"resourceType": resource_type, "id": resource_id, "status": "ready", "intent": "order"}

    async def update_resource(self, resource_type, resource_id, body):
        self.created.append((f"{resource_type}#update", body))
        return body

    async def ensure_agent_device(self):
        return "device-1"


async def test_fhir_bodies() -> None:
    client = CapturingClient()
    now = time.time()

    # --- unowned task ---
    await client.write_task(
        patient_id="p1", encounter_id="e1",
        action="Determine anticoagulation status", kind="uncertainty",
        why_it_matters="She is going to the OR", owner_practitioner_id=None,
        requested_by="DR. REYES", trigger="", opened_at=now,
    )
    _, task = client.created[-1]

    check("Task: resourceType", task.get("resourceType") == "Task")
    check("Task: required fields present", TASK_REQUIRED <= set(task), f"missing {TASK_REQUIRED - set(task)}")
    check("Task: status is legal", task["status"] in TASK_STATUS_VALUES, task["status"])
    check("Task: intent is legal", task["intent"] in TASK_INTENT_VALUES, task["intent"])
    check("Task: priority is legal", task["priority"] in TASK_PRIORITY_VALUES, task["priority"])
    check("Task: status is 'ready' per Medplum guidance", task["status"] == "ready")
    check("Task: action goes in code.text", task["code"]["text"] == "Determine anticoagulation status")
    check("Task: kind goes in businessStatus", task["businessStatus"]["text"] == "uncertainty")
    check("Task: for -> Patient reference", task["for"]["reference"] == "Patient/p1")
    check("Task: encounter -> Encounter reference", task["encounter"]["reference"] == "Encounter/e1")
    check(
        "Task: UNOWNED omits owner entirely (so owner:missing=true finds it)",
        "owner" not in task,
        f"owner present: {task.get('owner')}",
    )

    # --- owned + conditional task ---
    await client.write_task(
        patient_id="p1", encounter_id="e1",
        action="Repeat the pressure", kind="conditional",
        why_it_matters="", owner_practitioner_id="pr-9",
        requested_by="DR. REYES", trigger="in five minutes", opened_at=now,
    )
    _, cond = client.created[-1]
    check("Task: owner -> Practitioner reference", cond["owner"]["reference"] == "Practitioner/pr-9")
    check("Task: trigger sets restriction.period.end (the due date)", "end" in cond["restriction"]["period"])
    check("Task: spoken trigger preserved verbatim in note", "in five minutes" in cond["note"][0]["text"])

    # --- resolution ---
    await client.update_task_status(
        task_id="t1", work_status="completed", owner_practitioner_id="pr-9",
        evidence="She's not on any blood thinners", evidence_source="speech", at=now,
    )
    _, updated = client.created[-1]
    check("Task update: status maps to FHIR completed", updated["status"] == "completed")
    check("Task update: executionPeriod.end set on completion", "end" in updated["executionPeriod"])
    check("Task update: evidence recorded in note with source tag", "[speech]" in updated["note"][0]["text"])

    # --- provenance ---
    await client.write_provenance(
        target_ref="Task/t1", source="parent via nurse",
        quote="Mom says she has a severe penicillin allergy", recorded_at=now, activity="answered",
    )
    _, prov = client.created[-1]
    check("Provenance: required fields present", PROVENANCE_REQUIRED <= set(prov), f"missing {PROVENANCE_REQUIRED - set(prov)}")
    check("Provenance: target is a reference array", prov["target"][0]["reference"] == "Task/t1")
    check("Provenance: agent[].who is required and set", "reference" in prov["agent"][0]["who"])
    check("Provenance: human source recorded as onBehalfOf", prov["agent"][0]["onBehalfOf"]["display"] == "parent via nurse")
    check("Provenance: verbatim quote preserved in extension", "penicillin" in prov["extension"][0]["valueString"])


# --- 6. speaker attribution --------------------------------------------------


def test_speaker_attribution() -> None:
    case = CaseState(case_id="abc")

    check("speaker: no diarization -> empty label", case.speaker_label(None) == "")
    check(
        "speaker: unmapped index gets a neutral label, never a guessed role",
        case.speaker_label(0) == "Speaker 0",
        case.speaker_label(0),
    )

    case.speaker_roles[0] = "DR. REYES"
    check("speaker: mapped index resolves to the role", case.speaker_label(0) == "DR. REYES")
    check("speaker: other indices stay neutral", case.speaker_label(1) == "Speaker 1")

    words = [
        {"word": "i", "speaker": 1},
        {"word": "have", "speaker": 1},
        {"word": "ortho", "speaker": 1},
        {"word": "ok", "speaker": 0},
    ]
    check("diarize: segment attributed to the dominant speaker", _dominant_speaker(words) == 1)
    check("diarize: no speaker labels -> None", _dominant_speaker([{"word": "x"}]) is None)
    check("diarize: empty word list -> None", _dominant_speaker([]) is None)


# --- 7. persistence ----------------------------------------------------------


def test_persistence() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "state.json")

        store_a = CaseStore(snapshot_path=path)
        case = store_a.create("case-1")
        case.allergies.append({"allergen": "penicillin", "source": "parent via nurse", "timestamp": 1.0})
        case.speaker_roles[2] = "NURSE OKAFOR"
        case.work.append(
            WorkItem(
                id="w1",
                kind="uncertainty",
                action="Determine anticoagulation status",
                requested_by="DR. REYES",
                opened_at=1.0,
            )
        )
        case.work.append(
            WorkItem(
                id="w2", kind="task", action="Call ortho", owner="NURSE OKAFOR",
                status="acknowledged", evidence="I've got ortho", evidence_source="speech",
                opened_at=2.0,
            )
        )
        store_a.save()

        check("persist: snapshot file written", os.path.exists(path))

        store_b = CaseStore(snapshot_path=path)
        restored_count = store_b.load()
        check("persist: one case restored", restored_count == 1, f"got {restored_count}")

        restored = store_b.get("case-1")
        check("persist: case survives", restored is not None)
        check("persist: allergies survive", restored.allergies[0]["allergen"] == "penicillin")
        check("persist: work items survive", len(restored.work) == 2)
        check("persist: work items rehydrate as WorkItem", isinstance(restored.work[0], WorkItem))
        check("persist: orphan state survives the round trip", restored.work[0].is_orphan)
        check("persist: owner survives", restored.work[1].owner == "NURSE OKAFOR")
        check("persist: evidence survives", restored.work[1].evidence == "I've got ortho")
        check(
            "persist: speaker_roles int keys survive JSON",
            restored.speaker_roles == {2: "NURSE OKAFOR"},
            f"got {restored.speaker_roles!r}",
        )
        check(
            "persist: restored case still resolves speaker labels",
            restored.speaker_label(2) == "NURSE OKAFOR",
        )

        # A missing snapshot must be survivable, not fatal.
        store_c = CaseStore(snapshot_path=os.path.join(tmpdir, "nope.json"))
        check("persist: missing snapshot loads zero, no crash", store_c.load() == 0)


# --- 8. the no-recommendation boundary ---------------------------------------


def test_no_recommendations() -> None:
    """The product states facts and their provenance. It must not advise.
    These assertions exist so the boundary can't drift back in quietly."""

    check(
        "boundary: Alert has no 'alternative' field",
        "alternative" not in {f.name for f in dc_fields(Alert)},
    )
    check(
        "boundary: conflict schema cannot return an alternative drug",
        "alternative" not in CONFLICT_SCHEMA["properties"],
    )
    check(
        "boundary: conflict schema returns class + basis only",
        set(CONFLICT_SCHEMA["properties"]) == {"conflict", "drug_class", "basis"},
        f"got {set(CONFLICT_SCHEMA['properties'])}",
    )
    check(
        "boundary: system prompt forbids naming an alternative",
        "Do not name an alternative drug" in CONFLICT_SYSTEM_PROMPT,
    )
    check(
        "boundary: no web_search tool in the conflict path",
        "web_search" not in inspect.getsource(_assess_conflict),
    )

    case = CaseState(case_id="45abcdef")
    allergy = {"allergen": "penicillin", "source": "parent via nurse", "timestamp": time.time() - 120}
    med = {"name": "ampicillin-sulbactam 3 g IV", "status": "ordered"}
    assessment = {
        "conflict": True,
        "drug_class": "penicillin (beta-lactam)",
        "basis": "Ampicillin-sulbactam is a penicillin",
    }
    spoken = _spoken_alert(case, allergy, med, assessment)

    check("alert: names what was ordered", "ampicillin-sulbactam" in spoken.lower(), spoken)
    check("alert: names the documented allergy", "penicillin allergy" in spoken.lower(), spoken)
    check("alert: states when it was recorded", "minute" in spoken, spoken)
    check("alert: attributes the source", "parent via nurse" in spoken, spoken)
    check("alert: states the class relationship", "is a penicillin" in spoken, spoken)

    advice = ["recommend", "instead", "give ", "switch to", "use ", "should"]
    found = [w for w in advice if w in spoken.lower()]
    check("alert: contains NO advice language", not found, f"found {found} in: {spoken}")


# --- 8b. conflict pre-filter (regression) ------------------------------------


def test_conflict_prefilter() -> None:
    """Regression: a live run stored the allergen as a whole descriptive phrase,
    the exact dict lookup missed, and an ordered penicillin was never checked.
    The alert silently did not fire — the worst possible failure mode."""

    live_string = "Penicillin (severe, anaphylaxis as a child with hospitalization)"
    check(
        "prefilter: qualified allergen phrase still resolves to penicillin",
        "penicillin" in _allergen_keys(live_string),
        f"got {_allergen_keys(live_string)}",
    )

    variants = [
        "Penicillin",
        "penicillin allergy",
        "Penicillin (severe, anaphylaxis as a child with hospitalization)",
        "Penicillin - anaphylaxis",
        "severe penicillin allergy",
    ]
    meds = [{"name": "ampicillin-sulbactam 3 g IV", "status": "ordered"}]
    for v in variants:
        pairs = _find_conflicts([{"allergen": v, "timestamp": 0.0, "source": "x"}], meds)
        check(f"prefilter: {v!r} vs ampicillin-sulbactam -> conflict", len(pairs) == 1, f"got {len(pairs)}")

    # The whole point: the trigger word is not a substring of the allergen.
    check(
        "prefilter: catches a penicillin whose name doesn't contain 'penicillin'",
        "penicillin" not in "ampicillin-sulbactam 3 g iv",
    )

    # And it must not fire on unrelated drugs.
    safe = [{"name": "clindamycin 900 mg IV", "status": "ordered"}]
    pairs = _find_conflicts([{"allergen": live_string, "timestamp": 0.0, "source": "x"}], safe)
    check("prefilter: no false positive on clindamycin", len(pairs) == 0, f"got {len(pairs)}")

    pairs = _find_conflicts([{"allergen": "Sulfa drugs", "timestamp": 0.0, "source": "x"}], meds)
    check("prefilter: sulfa allergy does not fire on ampicillin-sulbactam", len(pairs) == 0, f"got {len(pairs)}")


# --- 8c. utterance buffering -------------------------------------------------


async def test_segmenter() -> None:
    """Deepgram finalizes on acoustic boundaries, not sentence boundaries.
    Extracting per raw segment lost a vital across a seam and split a claim
    from its own second half, so the task it claimed was never resolved."""

    async def collect(sink):
        async def handler(text, speaker):
            sink.append((text, speaker))
        return handler

    # The measured failure: a 16-char fragment mis-diarized onto the person who
    # had just asked for the task.
    got = []
    buf = UtteranceBuffer(on_utterance=await collect(got))
    await buf.add("I've got ortho.", 1)          # actually the nurse
    await buf.add("I'm calling them now.", 2)    # nurse
    await buf.close()
    check("segmenter: short fragment merges across a speaker change", len(got) == 1, f"got {got}")
    check("segmenter: merged text keeps both halves", "ortho" in got[0][0] and "calling" in got[0][0], got[0][0])
    check(
        "segmenter: merged turn attributed to whoever said more of it",
        got[0][1] == 2,
        f"attributed to speaker {got[0][1]}, expected 2",
    )

    # A real turn change must still split.
    got = []
    buf = UtteranceBuffer(on_utterance=await collect(got))
    await buf.add("Okay, someone find out whether she's anticoagulated and get ortho down here.", 1)
    await buf.add("I've got ortho, I'm calling them right now.", 2)
    await buf.close()
    check("segmenter: substantial turns still split on speaker change", len(got) == 2, f"got {len(got)}")
    check("segmenter: first turn keeps its speaker", got[0][1] == 1)
    check("segmenter: second turn keeps its speaker", got[1][1] == 2)

    # Fragments from one speaker join into a single utterance (the lost-GCS case).
    got = []
    buf = UtteranceBuffer(on_utterance=await collect(got))
    await buf.add("respirations 22, sat 97 on four liters, GCS", 0)
    await buf.add("13, she's confused, she can't give us a history", 0)
    await buf.close()
    check("segmenter: same-speaker fragments join", len(got) == 1, f"got {len(got)}")
    check("segmenter: the value split across the seam survives", "GCS 13" in got[0][0], got[0][0])

    # Long monologue flushes without waiting for quiet.
    got = []
    buf = UtteranceBuffer(on_utterance=await collect(got))
    await buf.add("x" * (MAX_CHARS + 10), 0)
    check("segmenter: over-length flushes immediately", len(got) == 1)

    # Quiet gap flushes on its own.
    got = []
    buf = UtteranceBuffer(on_utterance=await collect(got))
    await buf.add("Pressure is 100 over 60 now.", 0)
    check("segmenter: nothing flushed yet while still speaking", len(got) == 0)
    await asyncio.sleep(QUIET_SECONDS + 0.4)
    check("segmenter: quiet gap flushes the utterance", len(got) == 1, f"got {got}")


# --- 8d. FDA class verification + prompt caching ------------------------------


def test_rxnav_and_caching() -> None:
    """The class claim should be checkable, not asserted."""

    cases = [
        ("Ampicillin-sulbactam 3 g IV push", "ampicillin / sulbactam"),
        ("vancomycin 1 gram IV over 60 minutes", "vancomycin"),
        ("clindamycin 900 mg IV", "clindamycin"),
        ("ceftriaxone 2g IV stat", "ceftriaxone"),
        ("piperacillin-tazobactam 4.5 g IV q6h", "piperacillin / tazobactam"),
    ]
    for spoken, expected in cases:
        got = normalize_drug_name(spoken)
        check(f"rxnav: normalize {spoken!r}", got == expected, f"got {got!r}")

    classes = [
        "EPC: Penicillin-class Antibacterial",
        "EPC: beta Lactamase Inhibitor",
        "ATC1-4: Antibiotics",
        "CHEM: Penicillins",
    ]
    epc = epc_classes(classes)
    check("rxnav: epc_classes strips the prefix", epc[0] == "Penicillin-class Antibacterial", str(epc))
    check("rxnav: epc_classes excludes non-EPC types", len(epc) == 2, str(epc))
    check("rxnav: no EPC returned -> unverified", epc_classes(["ATC1-4: Antibiotics"]) == [])

    check(
        "alert: carries FDA classes",
        "fda_classes" in {f.name for f in dc_fields(Alert)},
    )
    check(
        "alert: carries a verified flag",
        "fda_verified" in {f.name for f in dc_fields(Alert)},
    )

    # Caching is a prefix match — the stable prompt must be in `system`, and
    # anything that varies must be in the user message, or nothing ever hits.
    src = inspect.getsource(extract_from_segment)
    check("caching: extraction marks the system prompt cacheable", "cache_control" in src)
    check(
        "caching: the varying ledger stays out of the cached prefix",
        "ledger_text" in src and "messages=[" in src,
    )
    check(
        "caching: conflict check marks its system prompt cacheable",
        "cache_control" in inspect.getsource(_assess_conflict),
    )
    check(
        "conflict: FDA classes are fetched before the model reasons",
        "drug_classes" in inspect.getsource(_assess_conflict),
    )


# --- 9. snapshot schema drift ------------------------------------------------


def test_snapshot_drift() -> None:
    """A snapshot written before a field was removed must still load."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "old.json")
        legacy = {
            "case-old": {
                "case_id": "case-old",
                "alerts": [
                    {
                        "id": "a1",
                        "text": "old alert",
                        "allergen": "penicillin",
                        "alternative": "vancomycin",  # field no longer exists
                        "timestamp": 1.0,
                    }
                ],
                "work": [],
                "transcript_entries": [],
            }
        }
        with open(path, "w") as fh:
            json.dump(legacy, fh)

        s = CaseStore(snapshot_path=path)
        restored = s.load()
        check("drift: legacy snapshot still loads", restored == 1, f"got {restored}")
        case = s.get("case-old")
        check("drift: alert survives", case is not None and len(case.alerts) == 1)
        check("drift: removed field is dropped, not fatal", not hasattr(case.alerts[0], "alternative"))


def main() -> int:
    test_normalize()
    test_trigger_seconds()
    test_work_item_states()
    test_watchdog()
    asyncio.run(test_fhir_bodies())
    test_speaker_attribution()
    test_persistence()
    test_no_recommendations()
    test_conflict_prefilter()
    asyncio.run(test_segmenter())
    test_rxnav_and_caching()
    test_snapshot_drift()

    for name in PASSED:
        print(f"  PASS  {name}")
    for name in FAILED:
        print(f"  FAIL  {name}")
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
