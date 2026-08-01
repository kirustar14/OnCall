import asyncio
import base64
import json
import logging
import time
import uuid

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.agent import make_alert, run_agent_step
from app.case_store import CaseState, TranscriptEntry, store
from app.deepgram_stt import DeepgramSTTSession
from app.deepgram_tts import synthesize_speech
from app.extraction import run_extraction_and_persist
from app.medplum_client import medplum_client
from app.query import answer_question
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


@app.on_event("startup")
async def _reapply_logging_config():
    # uvicorn's own logging setup runs after this module is imported and disables
    # loggers created before it (disable_existing_loggers=True) — reapply so our
    # app logs (servare.*) actually show up under `uvicorn` / `uvicorn --reload`.
    for name in list(logging.root.manager.loggerDict):
        if name.startswith("servare"):
            logging.getLogger(name).disabled = False
    logging.getLogger("servare").setLevel(logging.INFO)


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


@app.get("/api/agent_log")
async def get_agent_log():
    """All agent reasoning steps across every case, oldest first — used to hydrate
    the Agent Log tab on load; live updates after that come over each case's WS."""
    steps = [step for case in store.all() for step in case.agent_steps]
    steps.sort(key=lambda s: s.get("timestamp", 0))
    return steps


class SimulateTranscriptRequest(BaseModel):
    case_id: str
    text: str


@app.post("/api/debug/simulate_transcript")
async def simulate_transcript(req: SimulateTranscriptRequest):
    """Dev/demo helper: inject a line as if Deepgram had just finalized it, without
    needing a live microphone — handy for rehearsing the demo script or testing the
    extraction/agent pipeline against a specific case that's already got a socket open."""
    case = store.get(req.case_id) or store.create(req.case_id)
    await ws_manager.send_json(req.case_id, {"type": "transcript", "text": req.text, "is_final": True})
    case.running_transcript = (case.running_transcript + " " + req.text).strip()
    await _handle_finalized_segment(case, req.text)
    return {"ok": True}


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
            "status": case.status,
        },
    }


async def _emit_agent_step(case: CaseState, step: dict) -> None:
    step_with_meta = {"id": str(uuid.uuid4()), "case_id": case.case_id, "timestamp": time.time(), **step}
    case.agent_steps.append(step_with_meta)
    await ws_manager.send_json(case.case_id, {"type": "agent_step", **step_with_meta})


async def _handle_finalized_segment(case: CaseState, segment: str) -> None:
    """Run extraction -> Medplum writes -> (if anything new) full agentic reasoning
    step -> push updates. The agent decides for itself whether to speak up; there's
    no hardcoded conflict rule here."""
    try:
        _extracted, new_facts = await run_extraction_and_persist(case, segment)
    except Exception:
        logger.exception("extraction pipeline failed for case %s", case.case_id)
        return

    await ws_manager.send_json(case.case_id, _structured_payload(case))

    if not new_facts:
        return

    trigger_text = "\n".join(new_facts)

    try:
        decision = await run_agent_step(case, trigger_text, on_step=lambda s: _emit_agent_step(case, s))
    except Exception:
        logger.exception("agent reasoning step failed for case %s", case.case_id)
        return

    if not decision.get("action_needed") or not decision.get("alert_text"):
        return

    alert = make_alert(decision)
    case.alerts.append(alert)

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
                "reasoning": alert.reasoning,
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

    async def on_transcript(text: str, is_final: bool) -> None:
        entry = TranscriptEntry(text=text, is_final=is_final, timestamp=loop.time())
        if is_final:
            case.transcript_entries.append(entry)
            case.running_transcript = (case.running_transcript + " " + text).strip()

        await ws_manager.send_json(case_id, {"type": "transcript", "text": text, "is_final": is_final})

        if is_final and text.strip():
            asyncio.create_task(_handle_finalized_segment(case, text))

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
        if case.encounter_id and medplum_client.configured:
            try:
                await medplum_client.close_encounter(case.encounter_id)
            except Exception:
                logger.exception("failed to close Medplum encounter for case %s", case_id)
        await ws_manager.send_json(case_id, {"type": "status", "status": "closed"})
        ws_manager.unregister(case_id)
