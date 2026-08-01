"""Minimal Medplum FHIR client: OAuth2 client-credentials auth + resource writes."""

import logging
import time
from typing import Any, Optional

import httpx

from app.case_store import WORK_STATUS_TO_FHIR
from app.config import MEDPLUM_BASE_URL, MEDPLUM_CLIENT_ID, MEDPLUM_CLIENT_SECRET

logger = logging.getLogger("oncall.medplum")

SOURCE_EXTENSION_URL = "https://oncall.app/fhir/StructureDefinition/source"
SPOKEN_AT_EXTENSION_URL = "https://oncall.app/fhir/StructureDefinition/spoken-at"
QUOTE_EXTENSION_URL = "https://oncall.app/fhir/StructureDefinition/source-quote"
PRACTITIONER_SYSTEM = "https://oncall.app/role"
DEVICE_SYSTEM = "https://oncall.app/device"

# "in five minutes" -> 300. Deliberately crude: the spoken trigger is preserved
# verbatim in Task.note, and restriction.period.end is only a machine hint.
_TRIGGER_UNITS = {
    "second": 1,
    "seconds": 1,
    "minute": 60,
    "minutes": 60,
    "hour": 3600,
    "hours": 3600,
}
_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "fifteen": 15,
    "twenty": 20, "thirty": 30, "sixty": 60,
}


def _trigger_seconds(trigger: str, default: int = 300) -> int:
    tokens = trigger.lower().replace("-", " ").split()
    amount: Optional[int] = None
    for token in tokens:
        if token.isdigit():
            amount = int(token)
        elif token in _WORD_NUMBERS:
            amount = _WORD_NUMBERS[token]
        elif token in _TRIGGER_UNITS and amount is not None:
            return amount * _TRIGGER_UNITS[token]
    return default


class MedplumClient:
    def __init__(self) -> None:
        self._base_url = MEDPLUM_BASE_URL.rstrip("/") + "/"
        self._client_id = MEDPLUM_CLIENT_ID
        self._client_secret = MEDPLUM_CLIENT_SECRET
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._http = httpx.AsyncClient(timeout=15.0)
        self._agent_device_id: Optional[str] = None

    @property
    def configured(self) -> bool:
        return bool(self._client_id and self._client_secret)

    async def _get_token(self) -> str:
        if self._access_token and time.time() < self._token_expires_at - 30:
            return self._access_token

        resp = await self._http.post(
            f"{self._base_url}oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data["access_token"]
        self._token_expires_at = time.time() + float(data.get("expires_in", 3600))
        logger.info("medplum: obtained new access token")
        return self._access_token

    async def _headers(self) -> dict[str, str]:
        token = await self._get_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/fhir+json",
        }

    async def create_resource(self, resource_type: str, body: dict[str, Any]) -> dict[str, Any]:
        headers = await self._headers()
        resp = await self._http.post(f"{self._base_url}fhir/R4/{resource_type}", json=body, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def search(self, resource_type: str, params: dict[str, str]) -> dict[str, Any]:
        headers = await self._headers()
        resp = await self._http.get(f"{self._base_url}fhir/R4/{resource_type}", params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()

    # --- OnCall-specific resource builders -------------------------------------------------

    async def create_patient_and_encounter(self, case_id: str, case_label: str) -> tuple[str, str]:
        patient = await self.create_resource(
            "Patient",
            {
                "resourceType": "Patient",
                "identifier": [{"system": "https://oncall.app/case-id", "value": case_id}],
                "name": [{"text": case_label or f"Case {case_id[:8]}"}],
            },
        )
        patient_id = patient["id"]

        encounter = await self.create_resource(
            "Encounter",
            {
                "resourceType": "Encounter",
                "status": "in-progress",
                "class": {
                    "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                    "code": "EMER",
                    "display": "emergency",
                },
                "subject": {"reference": f"Patient/{patient_id}"},
                "identifier": [{"system": "https://oncall.app/case-id", "value": case_id}],
            },
        )
        return patient_id, encounter["id"]

    def _source_extension(self, source: str, spoken_at: float) -> list[dict[str, Any]]:
        return [
            {"url": SOURCE_EXTENSION_URL, "valueString": source},
            {"url": SPOKEN_AT_EXTENSION_URL, "valueDateTime": _iso(spoken_at)},
        ]

    async def write_allergy(
        self,
        patient_id: str,
        encounter_id: str,
        allergen: str,
        source: str,
        spoken_at: float,
        reaction: str = "",
        severity: str = "",
    ) -> dict[str, Any]:
        """`code` carries the substance ONLY. Severity and reaction are separate
        elements in FHIR for the same reason they are separate here: a safety
        check matches on the substance, and a qualifier packed into that field
        makes a real contraindication invisible."""
        body: dict[str, Any] = {
            "resourceType": "AllergyIntolerance",
            "clinicalStatus": {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
                        "code": "active",
                    }
                ]
            },
            "code": {"text": allergen},
            "patient": {"reference": f"Patient/{patient_id}"},
            "encounter": {"reference": f"Encounter/{encounter_id}"},
            "recordedDate": _iso(spoken_at),
            "extension": self._source_extension(source, spoken_at),
        }

        if severity == "severe":
            body["criticality"] = "high"
        elif severity in ("moderate", "mild"):
            body["criticality"] = "low"

        if reaction:
            entry: dict[str, Any] = {"manifestation": [{"text": reaction}]}
            # AllergyIntolerance.reaction.severity is its own small value set.
            if severity in ("severe", "moderate", "mild"):
                entry["severity"] = severity
            body["reaction"] = [entry]

        return await self.create_resource("AllergyIntolerance", body)

    async def write_vital(
        self, patient_id: str, encounter_id: str, name: str, value: str, source: str, spoken_at: float
    ) -> dict[str, Any]:
        return await self.create_resource(
            "Observation",
            {
                "resourceType": "Observation",
                "status": "final",
                "category": [
                    {
                        "coding": [
                            {
                                "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                                "code": "vital-signs",
                            }
                        ]
                    }
                ],
                "code": {"text": name},
                "valueString": value,
                "subject": {"reference": f"Patient/{patient_id}"},
                "encounter": {"reference": f"Encounter/{encounter_id}"},
                "effectiveDateTime": _iso(spoken_at),
                "extension": self._source_extension(source, spoken_at),
            },
        )

    async def write_medication_request(
        self, patient_id: str, encounter_id: str, medication: str, source: str, spoken_at: float
    ) -> dict[str, Any]:
        return await self.create_resource(
            "MedicationRequest",
            {
                "resourceType": "MedicationRequest",
                "status": "active",
                "intent": "order",
                "medicationCodeableConcept": {"text": medication},
                "subject": {"reference": f"Patient/{patient_id}"},
                "encounter": {"reference": f"Encounter/{encounter_id}"},
                "authoredOn": _iso(spoken_at),
                "extension": self._source_extension(source, spoken_at),
            },
        )

    # --- workflow: Task + Provenance ---------------------------------------------------------

    async def read_resource(self, resource_type: str, resource_id: str) -> dict[str, Any]:
        headers = await self._headers()
        resp = await self._http.get(
            f"{self._base_url}fhir/R4/{resource_type}/{resource_id}", headers=headers
        )
        resp.raise_for_status()
        return resp.json()

    async def update_resource(self, resource_type: str, resource_id: str, body: dict[str, Any]) -> dict[str, Any]:
        headers = await self._headers()
        resp = await self._http.put(
            f"{self._base_url}fhir/R4/{resource_type}/{resource_id}", json=body, headers=headers
        )
        resp.raise_for_status()
        return resp.json()

    async def ensure_practitioner(self, label: str) -> str:
        """Task.owner is a Reference — an owner needs a real resource. Reuse by
        identifier so repeated roles across cases don't pile up duplicates."""
        found = await self.search("Practitioner", {"identifier": f"{PRACTITIONER_SYSTEM}|{label}"})
        for entry in found.get("entry", []):
            existing_id = entry.get("resource", {}).get("id")
            if existing_id:
                return existing_id

        created = await self.create_resource(
            "Practitioner",
            {
                "resourceType": "Practitioner",
                "identifier": [{"system": PRACTITIONER_SYSTEM, "value": label}],
                "name": [{"text": label}],
            },
        )
        return created["id"]

    async def ensure_agent_device(self) -> Optional[str]:
        """The OnCall agent itself, as the Provenance recorder."""
        if self._agent_device_id:
            return self._agent_device_id
        try:
            found = await self.search("Device", {"identifier": f"{DEVICE_SYSTEM}|oncall-agent"})
            for entry in found.get("entry", []):
                existing_id = entry.get("resource", {}).get("id")
                if existing_id:
                    self._agent_device_id = existing_id
                    return existing_id

            created = await self.create_resource(
                "Device",
                {
                    "resourceType": "Device",
                    "identifier": [{"system": DEVICE_SYSTEM, "value": "oncall-agent"}],
                    "deviceName": [{"name": "OnCall voice agent", "type": "user-friendly-name"}],
                    "status": "active",
                },
            )
            self._agent_device_id = created["id"]
            return self._agent_device_id
        except Exception:
            logger.exception("medplum: failed to ensure agent Device")
            return None

    async def write_task(
        self,
        patient_id: str,
        encounter_id: str,
        action: str,
        kind: str,
        why_it_matters: str,
        owner_practitioner_id: Optional[str],
        requested_by: str,
        trigger: str,
        opened_at: float,
    ) -> dict[str, Any]:
        """Create a FHIR Task for a spoken work item.

        `status` and `intent` are both REQUIRED by the spec. Medplum's own docs
        recommend `ready` as the actionable state for single-system implementations
        (requested/received/accepted are for cross-system handoffs).
        """
        body: dict[str, Any] = {
            "resourceType": "Task",
            "status": "ready",
            "intent": "order",
            # Trauma resuscitation — these are not routine.
            "priority": "stat",
            "code": {"text": action},
            "description": why_it_matters or action,
            # businessStatus carries the implementation-specific nuance: whether
            # this is an action, an open question, or a timed re-check.
            "businessStatus": {"text": kind},
            "for": {"reference": f"Patient/{patient_id}"},
            "encounter": {"reference": f"Encounter/{encounter_id}"},
            "authoredOn": _iso(opened_at),
            "extension": self._source_extension(requested_by or "clinician transcript", opened_at),
        }

        if owner_practitioner_id:
            body["owner"] = {"reference": f"Practitioner/{owner_practitioner_id}"}
        # No owner key at all when unowned — that is what makes
        # `Task?owner:missing=true` return it.

        if trigger:
            # restriction.period.end is the FHIR "due date".
            body["restriction"] = {"period": {"end": _iso(opened_at + _trigger_seconds(trigger))}}
            body.setdefault("note", []).append(
                {"text": f"Trigger as spoken: {trigger}", "time": _iso(opened_at)}
            )

        return await self.create_resource("Task", body)

    async def update_task_status(
        self,
        task_id: str,
        work_status: str,
        owner_practitioner_id: Optional[str],
        evidence: str,
        evidence_source: str,
        at: float,
    ) -> dict[str, Any]:
        """Read-modify-write. Simpler and safer than json-patch here, because
        `note` may or may not already exist on the resource."""
        task = await self.read_resource("Task", task_id)
        task["status"] = WORK_STATUS_TO_FHIR.get(work_status, "ready")
        task["lastModified"] = _iso(at)

        if owner_practitioner_id:
            task["owner"] = {"reference": f"Practitioner/{owner_practitioner_id}"}

        execution = task.get("executionPeriod") or {}
        execution.setdefault("start", _iso(at))
        if work_status in ("completed", "answered"):
            execution["end"] = _iso(at)
        task["executionPeriod"] = execution

        if evidence:
            notes = task.get("note") or []
            notes.append({"text": f"[{evidence_source}] {evidence}", "time": _iso(at)})
            task["note"] = notes

        return await self.update_resource("Task", task_id, task)

    async def write_provenance(
        self,
        target_ref: str,
        source: str,
        quote: str,
        recorded_at: float,
        activity: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Who asserted this, when, and on whose authority.

        This is what lets a clinician independently review the basis for
        anything the system surfaced — the whole reason we can state facts
        without making recommendations.
        """
        device_id = await self.ensure_agent_device()
        if not device_id:
            return None

        agent: dict[str, Any] = {
            "type": {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/provenance-participant-type",
                        "code": "author",
                    }
                ]
            },
            "who": {"reference": f"Device/{device_id}", "display": "OnCall voice agent"},
        }
        if source:
            # The human the assertion actually came from — "parent via nurse",
            # "EMS handoff" — recorded as free text on the agent entry.
            agent["onBehalfOf"] = {"display": source}

        body: dict[str, Any] = {
            "resourceType": "Provenance",
            "target": [{"reference": target_ref}],
            "recorded": _iso(recorded_at),
            "occurredDateTime": _iso(recorded_at),
            "agent": [agent],
        }
        if activity:
            body["activity"] = {"text": activity}
        if quote:
            body["extension"] = [{"url": QUOTE_EXTENSION_URL, "valueString": quote}]

        return await self.create_resource("Provenance", body)

    async def find_unowned_tasks(self, encounter_id: str) -> list[dict[str, Any]]:
        """FHIR-native unowned-work query. `owner:missing=true` is the standard
        search modifier for an unassigned Task — this is not a custom concept."""
        found = await self.search(
            "Task",
            {
                "encounter": f"Encounter/{encounter_id}",
                "owner:missing": "true",
                "status": "ready",
            },
        )
        return [e.get("resource", {}) for e in found.get("entry", [])]

    # --- reads: Medplum as the source of truth, not just a write target ------------------
    #
    # The reasoning agent reads the encounter back from FHIR before deciding
    # anything, rather than trusting the in-memory mirror, and can look across
    # prior encounters for the same patient. That is what makes 'has this
    # patient been here before' a real question rather than a demo prop.

    async def update_patient_name(self, patient_id: str, name: str) -> None:
        headers = await self._headers()
        resp = await self._http.patch(
            f"{self._base_url}fhir/R4/Patient/{patient_id}",
            headers={**headers, "Content-Type": "application/json-patch+json"},
            json=[{"op": "replace", "path": "/name", "value": [{"text": name}]}],
        )
        resp.raise_for_status()

    # --- Patient history (cross-encounter / cross-case lookups for the reasoning agent) -----

    async def search_patients_by_name(self, name: str) -> list[dict[str, Any]]:
        """Returns raw Patient resources whose name matches (FHIR does fuzzy/token matching)."""
        bundle = await self.search("Patient", {"name": name})
        return [entry["resource"] for entry in bundle.get("entry", [])]

    async def fetch_patient_history(
        self, patient_id: str, exclude_encounter_id: Optional[str] = None
    ) -> dict[str, list[dict[str, Any]]]:
        """All AllergyIntolerance / MedicationRequest / Observation / Encounter resources
        for a patient across every encounter, simplified into the same shape used
        elsewhere in the app. Used by the search_patient_history agent tool."""

        allergy_bundle = await self.search("AllergyIntolerance", {"patient": f"Patient/{patient_id}"})
        med_bundle = await self.search("MedicationRequest", {"patient": f"Patient/{patient_id}"})
        obs_bundle = await self.search("Observation", {"patient": f"Patient/{patient_id}", "category": "vital-signs"})
        encounter_bundle = await self.search("Encounter", {"patient": f"Patient/{patient_id}"})

        def _entries(bundle: dict) -> list[dict]:
            return [e["resource"] for e in bundle.get("entry", [])]

        allergies = [
            {
                "allergen": r.get("code", {}).get("text", "unknown"),
                "source": _extract_extension(r, SOURCE_EXTENSION_URL),
                "encounter": _extract_reference_id(r.get("encounter")),
            }
            for r in _entries(allergy_bundle)
        ]
        medications = [
            {
                "name": r.get("medicationCodeableConcept", {}).get("text", "unknown"),
                "source": _extract_extension(r, SOURCE_EXTENSION_URL),
                "encounter": _extract_reference_id(r.get("encounter")),
            }
            for r in _entries(med_bundle)
        ]
        vitals = [
            {
                "name": r.get("code", {}).get("text", "unknown"),
                "value": r.get("valueString", ""),
                "encounter": _extract_reference_id(r.get("encounter")),
            }
            for r in _entries(obs_bundle)
        ]
        encounters = [
            {"id": r.get("id"), "status": r.get("status")}
            for r in _entries(encounter_bundle)
        ]

        if exclude_encounter_id:
            allergies = [a for a in allergies if a["encounter"] != exclude_encounter_id]
            medications = [m for m in medications if m["encounter"] != exclude_encounter_id]
            vitals = [v for v in vitals if v["encounter"] != exclude_encounter_id]
            encounters = [e for e in encounters if e["id"] != exclude_encounter_id]

        return {"allergies": allergies, "medications": medications, "vitals": vitals, "encounters": encounters}

    async def fetch_encounter_resources(self, encounter_id: str) -> dict[str, list[dict[str, Any]]]:
        """Current allergies/medications/vitals for ONE encounter, read back from Medplum
        (rather than trusting the in-memory mirror) — used to build the reasoning agent's
        full case context."""

        allergy_bundle = await self.search("AllergyIntolerance", {"encounter": f"Encounter/{encounter_id}"})
        med_bundle = await self.search("MedicationRequest", {"encounter": f"Encounter/{encounter_id}"})
        obs_bundle = await self.search(
            "Observation", {"encounter": f"Encounter/{encounter_id}", "category": "vital-signs"}
        )

        def _entries(bundle: dict) -> list[dict]:
            return [e["resource"] for e in bundle.get("entry", [])]

        allergies = [
            {"allergen": r.get("code", {}).get("text", "unknown"), "source": _extract_extension(r, SOURCE_EXTENSION_URL)}
            for r in _entries(allergy_bundle)
        ]
        medications = [
            {
                "name": r.get("medicationCodeableConcept", {}).get("text", "unknown"),
                "status": "given" if r.get("status") == "completed" else "ordered",
                "source": _extract_extension(r, SOURCE_EXTENSION_URL),
            }
            for r in _entries(med_bundle)
        ]
        vitals = [
            {"name": r.get("code", {}).get("text", "unknown"), "value": r.get("valueString", "")}
            for r in _entries(obs_bundle)
        ]
        return {"allergies": allergies, "medications": medications, "vitals": vitals}

    async def close_encounter(self, encounter_id: str) -> None:
        headers = await self._headers()
        resp = await self._http.patch(
            f"{self._base_url}fhir/R4/Encounter/{encounter_id}",
            headers={**headers, "Content-Type": "application/json-patch+json"},
            json=[{"op": "replace", "path": "/status", "value": "finished"}],
        )
        resp.raise_for_status()

    async def close_encounter(self, encounter_id: str) -> None:
        headers = await self._headers()
        resp = await self._http.patch(
            f"{self._base_url}fhir/R4/Encounter/{encounter_id}",
            headers={**headers, "Content-Type": "application/json-patch+json"},
            json=[{"op": "replace", "path": "/status", "value": "finished"}],
        )
        resp.raise_for_status()



def _extract_extension(resource: dict[str, Any], url: str) -> str:
    for ext in resource.get("extension", []) or []:
        if ext.get("url") == url:
            return ext.get("valueString", "")
    return ""


def _extract_reference_id(ref: Optional[dict[str, Any]]) -> str:
    if not ref:
        return ""
    return str(ref.get("reference", "")).split("/")[-1]


def _iso(epoch_seconds: float) -> str:
    import datetime

    return datetime.datetime.fromtimestamp(epoch_seconds, tz=datetime.timezone.utc).isoformat()


medplum_client = MedplumClient()
