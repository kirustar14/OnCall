"""In-memory store of all case state. Simple dict — no DB, per hackathon spec."""

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("servare.case_store")

# Work item lifecycle. Mirrors the FHIR Task state machine Medplum recommends
# (see docs/careplans/tasks): "ready" is the actionable state for single-system
# implementations; requested/received/accepted are for cross-system handoffs.
#   open        -> Task.status "ready"        (nobody has picked it up)
#   acknowledged-> Task.status "in-progress"  (someone claimed it)
#   completed   -> Task.status "completed"
#   answered    -> Task.status "completed"    (an uncertainty that got an answer)
WORK_STATUS_TO_FHIR = {
    "open": "ready",
    "acknowledged": "in-progress",
    "completed": "completed",
    "answered": "completed",
}

OPEN_STATUSES = ("open", "acknowledged")


@dataclass
class TranscriptEntry:
    text: str
    is_final: bool
    timestamp: float
    source: str = "mic"
    # Deepgram diarization index, and the role it has been mapped to.
    speaker_index: Optional[int] = None
    speaker_label: str = ""


@dataclass
class Alert:
    id: str
    text: str
    allergen: str
    alternative: str
    timestamp: float


@dataclass
class WorkItem:
    """Something the room asked for out loud.

    An unowned item is the important case: nobody was named, so nobody will do
    it, and nobody will notice. `owner == ""` is a real state, not missing data.
    """

    id: str
    kind: str  # "task" | "uncertainty" | "conditional"
    action: str
    owner: str = ""  # "" means nobody was named — do not backfill from the speaker
    requested_by: str = ""
    trigger: str = ""  # conditional only, e.g. "in 5 minutes"
    why_it_matters: str = ""
    source_quote: str = ""

    status: str = "open"
    evidence: str = ""
    evidence_source: Optional[str] = None  # "speech" | "vision"

    opened_at: float = field(default_factory=time.time)
    resolved_at: Optional[float] = None
    # Set once the agent has asked the room who owns this. Never ask twice.
    prompted_at: Optional[float] = None

    fhir_task_id: Optional[str] = None

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    @property
    def is_orphan(self) -> bool:
        return self.status == "open" and not self.owner

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "action": self.action,
            "owner": self.owner,
            "requested_by": self.requested_by,
            "trigger": self.trigger,
            "why_it_matters": self.why_it_matters,
            "source_quote": self.source_quote,
            "status": self.status,
            "evidence": self.evidence,
            "evidence_source": self.evidence_source,
            "opened_at": self.opened_at,
            "resolved_at": self.resolved_at,
            "prompted_at": self.prompted_at,
            "fhir_task_id": self.fhir_task_id,
        }


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
    # role label ("DR. REYES", "NURSE OKAFOR", ...) -> Medplum Practitioner id.
    # Task.owner is a Reference, not a string, so an owner needs a real resource.
    practitioner_ids: dict[str, str] = field(default_factory=dict)

    # Deepgram diarization index -> role label. Diarization tells us *that* the
    # speaker changed, never *who* they are; a human maps each voice once.
    speaker_roles: dict[int, str] = field(default_factory=dict)

    # structured clinical data (mirrors what's in Medplum, kept locally for fast UI + intervention checks)
    vitals: list[dict[str, Any]] = field(default_factory=list)
    allergies: list[dict[str, Any]] = field(default_factory=list)
    medications: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    case_details: dict[str, Any] = field(default_factory=dict)

    # the ledger
    work: list[WorkItem] = field(default_factory=list)

    alerts: list[Alert] = field(default_factory=list)

    def open_work(self) -> list[WorkItem]:
        return [w for w in self.work if w.is_open]

    def open_ledger_for_prompt(self) -> list[dict[str, str]]:
        """Compact view handed to the extractor so it can resolve items.
        Only these ids may appear in a resolution."""
        return [
            {"id": w.id, "kind": w.kind, "action": w.action, "owner": w.owner}
            for w in self.open_work()
        ]

    def find_work(self, work_id: str) -> Optional[WorkItem]:
        return next((w for w in self.work if w.id == work_id), None)

    def new_work_id(self) -> str:
        return uuid.uuid4().hex[:8]

    def speaker_label(self, speaker_index: Optional[int]) -> str:
        """Diarization gives us a stable index per voice, never an identity.
        Until a human maps it, fall back to a neutral label — never guess a role."""
        if speaker_index is None:
            return ""
        return self.speaker_roles.get(speaker_index) or f"Speaker {speaker_index}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "status": self.status,
            "created_at": self.created_at,
            "closed_at": self.closed_at,
            "transcript": self.running_transcript,
            "patient_id": self.patient_id,
            "encounter_id": self.encounter_id,
            "speaker_roles": {str(k): v for k, v in self.speaker_roles.items()},
            "vitals": self.vitals,
            "allergies": self.allergies,
            "medications": self.medications,
            "notes": self.notes,
            "case_details": self.case_details,
            "work": [w.to_dict() for w in self.work],
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
    """In-memory, with a JSON snapshot on disk.

    A resuscitation does not pause because a process restarted. Snapshotting is
    best-effort and deliberately dumb — one file, rewritten on mutation — but it
    means `--reload` firing mid-demo doesn't wipe the ledger.
    """

    def __init__(self, snapshot_path: Optional[str] = None) -> None:
        self._cases: dict[str, CaseState] = {}
        self._lock = threading.Lock()
        self._snapshot_path = Path(snapshot_path or os.environ.get("SERVARE_SNAPSHOT", ".servare-state.json"))

    def create(self, case_id: str) -> CaseState:
        with self._lock:
            case = CaseState(case_id=case_id)
            self._cases[case_id] = case
            return case

    # --- persistence ---------------------------------------------------------

    def save(self) -> None:
        try:
            payload = {
                cid: {
                    **asdict(case),
                    # int keys don't survive JSON
                    "speaker_roles": {str(k): v for k, v in case.speaker_roles.items()},
                }
                for cid, case in self._cases.items()
            }
            tmp = self._snapshot_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload))
            tmp.replace(self._snapshot_path)  # atomic — never a half-written file
        except Exception:
            logger.exception("failed to snapshot case state")

    def load(self) -> int:
        if not self._snapshot_path.exists():
            return 0
        try:
            payload = json.loads(self._snapshot_path.read_text())
        except Exception:
            logger.exception("failed to read snapshot; starting empty")
            return 0

        restored = 0
        for cid, raw in payload.items():
            try:
                case = CaseState(case_id=cid)
                for key, value in raw.items():
                    if key in ("work", "alerts", "transcript_entries", "speaker_roles", "case_id"):
                        continue
                    if hasattr(case, key):
                        setattr(case, key, value)
                case.work = [WorkItem(**w) for w in raw.get("work", [])]
                case.alerts = [Alert(**a) for a in raw.get("alerts", [])]
                case.transcript_entries = [
                    TranscriptEntry(**t) for t in raw.get("transcript_entries", [])
                ]
                case.speaker_roles = {int(k): v for k, v in (raw.get("speaker_roles") or {}).items()}
                self._cases[cid] = case
                restored += 1
            except Exception:
                logger.exception("failed to restore case %s", cid)
        logger.info("restored %d case(s) from snapshot", restored)
        return restored

    def get(self, case_id: str) -> Optional[CaseState]:
        return self._cases.get(case_id)

    def all(self) -> list[CaseState]:
        return list(self._cases.values())

    def open_cases(self) -> list[CaseState]:
        return [c for c in self._cases.values() if c.status == "open"]

    def close(self, case_id: str) -> None:
        case = self._cases.get(case_id)
        if case:
            case.status = "closed"
            case.closed_at = time.time()


store = CaseStore()
