"""In-memory store of all case state. Simple dict — no DB, per hackathon spec."""

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class TranscriptEntry:
    text: str
    is_final: bool
    timestamp: float
    source: str = "mic"


@dataclass
class Alert:
    id: str
    text: str
    allergen: str
    alternative: str
    timestamp: float


@dataclass
class CaseState:
    case_id: str
    status: str = "open"  # "open" | "closed"
    created_at: float = field(default_factory=time.time)
    closed_at: Optional[float] = None

    # transcript
    transcript_entries: list[TranscriptEntry] = field(default_factory=list)
    running_transcript: str = ""

    # medplum linkage
    patient_id: Optional[str] = None
    encounter_id: Optional[str] = None

    # structured clinical data (mirrors what's in Medplum, kept locally for fast UI + intervention checks)
    vitals: list[dict[str, Any]] = field(default_factory=list)
    allergies: list[dict[str, Any]] = field(default_factory=list)
    medications: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    case_details: dict[str, Any] = field(default_factory=dict)

    alerts: list[Alert] = field(default_factory=list)

    # bookkeeping for extraction: how much of running_transcript has been sent to Claude already
    last_extracted_offset: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "status": self.status,
            "created_at": self.created_at,
            "closed_at": self.closed_at,
            "transcript": self.running_transcript,
            "patient_id": self.patient_id,
            "encounter_id": self.encounter_id,
            "vitals": self.vitals,
            "allergies": self.allergies,
            "medications": self.medications,
            "notes": self.notes,
            "case_details": self.case_details,
            "alerts": [
                {
                    "id": a.id,
                    "text": a.text,
                    "allergen": a.allergen,
                    "alternative": a.alternative,
                    "timestamp": a.timestamp,
                }
                for a in self.alerts
            ],
        }


class CaseStore:
    def __init__(self) -> None:
        self._cases: dict[str, CaseState] = {}
        self._lock = threading.Lock()

    def create(self, case_id: str) -> CaseState:
        with self._lock:
            case = CaseState(case_id=case_id)
            self._cases[case_id] = case
            return case

    def get(self, case_id: str) -> Optional[CaseState]:
        return self._cases.get(case_id)

    def all(self) -> list[CaseState]:
        return list(self._cases.values())

    def close(self, case_id: str) -> None:
        case = self._cases.get(case_id)
        if case:
            case.status = "closed"
            case.closed_at = time.time()


store = CaseStore()
