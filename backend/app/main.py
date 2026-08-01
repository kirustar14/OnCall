import asyncio
import base64
import json
import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.case_store import CaseState, TranscriptEntry, store
from app.deepgram_stt import DeepgramSTTSession
from app.deepgram_tts import synthesize_speech
from app.extraction import run_extraction_and_persist
from app.handoff import build_handoff
from app.intervention import check_for_conflicts
from app.medplum_client import medplum_client
from app.query import answer_question
from app.watchdog import watchdog_loop
from app.ws_manager import ws_manager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("servare.main")

app = FastAPI(title="Servare")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


_watchdog_task: asyncio.Task | None = None


@app.on_event("startup")
async def _reapply_logging_config():
    # uvicorn's own logging setup runs after this module is imported and disables
    # loggers created before it (disable_existing_loggers=True) — reapply so our
    # app logs (servare.*) actually show up under `uvicorn` / `uvicorn --reload`.
    for name in list(logging.root.manager.loggerDict):
        if name.startswith("servare"):
            logging.getLogger(name).disabled = False
    logging.getLogger("servare").setLevel(logging.INFO)

    # A resuscitation doesn't pause because a process restarted.
    restored = store.load()
    if restored:
        logger.info("restored %d case(s) from snapshot", restored)

    global _watchdog_task
    _watchdog_task = asyncio.create_task(watchdog_loop())


@app.on_event("shutdown")
async def _stop_watchdog():
    if _watchdog_task is not None:
        _watchdog_task.cancel()


@app.get("/api/health")
async def health():
    return {"ok": True}


@app.get("/api/cases")
async def list_cases():
    return [c.to_dict() for c in store.all()]


@app.get("/api/cases/{case_id}")
async def get_case(case_id: str):
    case = store.get(case_id)
    if case is None:
        return {"error": "not found"}
    return case.to_dict()


class QueryRequest(BaseModel):
    case_id: str
    question: str


@app.post("/api/query")
async def query_case(req: QueryRequest):
    case = store.get(req.case_id)
    if case is None:
        return {"answer": f"No such case: {req.case_id}"}
    answer = await answer_question(case, req.question)
    return {"answer": answer}


def _structured_payload(case: CaseState) -> dict:
    return {
        "type": "case_data",
        "data": {
            "vitals": case.vitals,
            "allergies": case.allergies,
            "medications": case.medications,
            "notes": case.notes,
            "case_details": case.case_details,
            "work": [w.to_dict() for w in case.work],
            "speaker_roles": {str(k): v for k, v in case.speaker_roles.items()},
            "status": case.status,
        },
    }


class SpeakerRoleRequest(BaseModel):
    case_id: str
    speaker_index: int
    role: str


@app.post("/api/speaker-role")
async def set_speaker_role(req: SpeakerRoleRequest):
    """Map a diarized voice to a role.

    Diarization tells us reliably *that* the speaker changed; it can never tell
    us *who* they are. A human makes that call once per voice, and from then on
    every task that voice claims is attributed correctly. This is deliberately
    not biometric identification — it's a label on an index.
    """
    case = store.get(req.case_id)
    if case is None:
        return {"error": f"No such case: {req.case_id}"}

    case.speaker_roles[req.speaker_index] = req.role.strip()
    store.save()
    await ws_manager.send_json(case.case_id, _structured_payload(case))
    return {"speaker_roles": {str(k): v for k, v in case.speaker_roles.items()}}


class HandoffRequest(BaseModel):
    case_id: str


@app.post("/api/handoff")
async def handoff(req: HandoffRequest):
    """The push half of Context — a briefing for someone who just walked in."""
    case = store.get(req.case_id)
    if case is None:
        return {"error": f"No such case: {req.case_id}"}

    brief = await build_handoff(case)

    audio_b64 = None
    spoken = brief.get("spoken_brief", "")
    if spoken:
        try:
            audio_bytes = await synthesize_speech(spoken)
            if audio_bytes:
                audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
        except Exception:
            logger.exception("handoff TTS failed for case %s", case.case_id)

    await ws_manager.send_json(
        case.case_id,
        {"type": "handoff", "brief": brief, "audio_b64": audio_b64, "audio_mime": "audio/mpeg"},
    )
    return brief


async def _handle_finalized_segment(case: CaseState, segment: str, speaker_label: str = "") -> None:
    """Run extraction -> Medplum writes -> intervention check -> push updates."""
    try:
        await run_extraction_and_persist(case, segment, speaker_label)
    except Exception:
        logger.exception("extraction pipeline failed for case %s", case.case_id)
        return

    store.save()
    await ws_manager.send_json(case.case_id, _structured_payload(case))

    try:
        new_alerts = await check_for_conflicts(case)
    except Exception:
        logger.exception("intervention check failed for case %s", case.case_id)
        new_alerts = []

    for alert in new_alerts:
        audio_b64 = None
        try:
            audio_bytes = await synthesize_speech(alert.text)
            if audio_bytes:
                audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
        except Exception:
            logger.exception("TTS failed for case %s", case.case_id)

        await ws_manager.send_json(
            case.case_id,
            {
                "type": "alert",
                "alert": {
                    "id": alert.id,
                    "text": alert.text,
                    "allergen": alert.allergen,
                    "alternative": alert.alternative,
                    "timestamp": alert.timestamp,
                },
                "audio_b64": audio_b64,
                "audio_mime": "audio/mpeg",
            },
        )


@app.websocket("/ws/case/{case_id}")
async def case_ws(websocket: WebSocket, case_id: str):
    await websocket.accept()

    case = store.get(case_id) or store.create(case_id)
    ws_manager.register(case_id, websocket)

    loop = asyncio.get_running_loop()

    async def on_transcript(text: str, is_final: bool, speaker_index: int | None = None) -> None:
        speaker_label = case.speaker_label(speaker_index)
        entry = TranscriptEntry(
            text=text,
            is_final=is_final,
            timestamp=loop.time(),
            speaker_index=speaker_index,
            speaker_label=speaker_label,
        )
        if is_final:
            case.transcript_entries.append(entry)
            case.running_transcript = (case.running_transcript + " " + text).strip()

        await ws_manager.send_json(
            case_id,
            {
                "type": "transcript",
                "text": text,
                "is_final": is_final,
                "speaker_index": speaker_index,
                "speaker_label": speaker_label,
            },
        )

        if is_final and text.strip():
            asyncio.create_task(_handle_finalized_segment(case, text, speaker_label))

    stt_session = DeepgramSTTSession(on_transcript=on_transcript)
    try:
        await stt_session.start()
    except Exception:
        logger.exception("failed to start Deepgram session for case %s", case_id)

    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break

            if "bytes" in message and message["bytes"] is not None:
                await stt_session.send_audio(message["bytes"])
            elif "text" in message and message["text"] is not None:
                try:
                    payload = json.loads(message["text"])
                except json.JSONDecodeError:
                    continue
                if payload.get("type") == "end_case":
                    break
    except WebSocketDisconnect:
        pass
    finally:
        await stt_session.finish()
        store.close(case_id)
        store.save()
        if case.encounter_id and medplum_client.configured:
            try:
                await medplum_client.close_encounter(case.encounter_id)
            except Exception:
                logger.exception("failed to close Medplum encounter for case %s", case_id)
        await ws_manager.send_json(case_id, {"type": "status", "status": "closed"})
        ws_manager.unregister(case_id)
