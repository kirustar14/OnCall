"""Minimal Medplum FHIR client: OAuth2 client-credentials auth + resource writes."""

import logging
import time
from typing import Any, Optional

import httpx

from app.config import MEDPLUM_BASE_URL, MEDPLUM_CLIENT_ID, MEDPLUM_CLIENT_SECRET

logger = logging.getLogger("servare.medplum")

SOURCE_EXTENSION_URL = "https://servare.app/fhir/StructureDefinition/source"
SPOKEN_AT_EXTENSION_URL = "https://servare.app/fhir/StructureDefinition/spoken-at"


class MedplumClient:
    def __init__(self) -> None:
        self._base_url = MEDPLUM_BASE_URL.rstrip("/") + "/"
        self._client_id = MEDPLUM_CLIENT_ID
        self._client_secret = MEDPLUM_CLIENT_SECRET
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._http = httpx.AsyncClient(timeout=15.0)

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

    # --- Servare-specific resource builders -------------------------------------------------

    async def create_patient_and_encounter(self, case_id: str, case_label: str) -> tuple[str, str]:
        patient = await self.create_resource(
            "Patient",
            {
                "resourceType": "Patient",
                "identifier": [{"system": "https://servare.app/case-id", "value": case_id}],
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
                "identifier": [{"system": "https://servare.app/case-id", "value": case_id}],
            },
        )
        return patient_id, encounter["id"]

    def _source_extension(self, source: str, spoken_at: float) -> list[dict[str, Any]]:
        return [
            {"url": SOURCE_EXTENSION_URL, "valueString": source},
            {"url": SPOKEN_AT_EXTENSION_URL, "valueDateTime": _iso(spoken_at)},
        ]

    async def write_allergy(
        self, patient_id: str, encounter_id: str, allergen: str, source: str, spoken_at: float
    ) -> dict[str, Any]:
        return await self.create_resource(
            "AllergyIntolerance",
            {
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
                "extension": self._source_extension(source, spoken_at),
            },
        )

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

    async def close_encounter(self, encounter_id: str) -> None:
        headers = await self._headers()
        resp = await self._http.patch(
            f"{self._base_url}fhir/R4/Encounter/{encounter_id}",
            headers={**headers, "Content-Type": "application/json-patch+json"},
            json=[{"op": "replace", "path": "/status", "value": "finished"}],
        )
        resp.raise_for_status()


def _iso(epoch_seconds: float) -> str:
    import datetime

    return datetime.datetime.fromtimestamp(epoch_seconds, tz=datetime.timezone.utc).isoformat()


medplum_client = MedplumClient()
