import asyncio
import base64
import json
import logging
import time
import uuid

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.agent import make_alert, run_agent_step, run_query_step
from app.case_store import Alert, CaseState, TranscriptEntry, next_alert_seq, store
from app.deepgram_stt import DeepgramSTTSession
from app.deepgram_tts import synthesize_speech
from app.extraction import run_extraction_and_persist
from app.frame_buffer import Frame, frame_buffer
from app.handoff import build_handoff
from app.intent import classify_segment
from app.intervention import check_for_conflicts
from app.medplum_client import medplum_client
from app.moss_client import moss_client
from app.segmenter import UtteranceBuffer
from app.vision import describe_scene
from app.warmup import warm_schemas
from app.watchdog import watchdog_loop
from app.ws_manager import ws_manager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("oncall.main")

app = FastAPI(title="OnCall")

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
    # app logs (oncall.*) actually show up under `uvicorn` / `uvicorn --reload`.
    for name in list(logging.root.manager.loggerDict):
        if name.startswith("oncall"):
            logging.getLogger(name).disabled = False
    logging.getLogger("oncall").setLevel(logging.INFO)

    # A resuscitation doesn't pause because a process restarted.
    restored = store.load()
    if restored:
        logger.info("restored %d case(s) from snapshot", restored)

    if moss_client.configured:
        # Build the client and load the index during boot rather than blocking the
        # first live extraction. The SDK has an intermittent first-call auth race
        # (see app/moss_client.py) that this absorbs, with retries, before any
        # case is open.
        asyncio.create_task(moss_client.warmup())

    global _watchdog_task
    _watchdog_task = asyncio.create_task(watchdog_loop())

    # Fire-and-forget: moves one-time JSON-schema compilation off the first
    # transcript segment, where it costs ~12s of visible dead air.
    asyncio.create_task(warm_schemas())


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
    return {"answer": await _answer_query(case, req.question)}


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


async def _emit_agent_step(case: CaseState, step: dict) -> None:
    """Stream one reasoning step to the Agent Log."""
    entry = {"id": str(uuid.uuid4()), "case_id": case.case_id, "timestamp": time.time(), **step}
    case.agent_steps.append(entry)
    await ws_manager.send_json(case.case_id, {"type": "agent_step", **entry})


async def _speak_alert(case: CaseState, alert: Alert) -> None:
    """Bank an alert, synthesize it, and hand it to the frontend.

    Ordering across cases is the browser's job — there is one speaker, and the
    priority queue there decides what plays next. This just delivers one item.
    """
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
            "alert": alert.to_dict(),
            "audio_b64": audio_b64,
            "audio_mime": "audio/mpeg",
        },
    )


async def _answer_query(case: CaseState, question: str) -> str:
    """Run the query agent, then speak the answer as an advisory item."""
    result = await run_query_step(case, question, lambda s: _emit_agent_step(case, s))
    answer = result.get("answer", "")
    if answer:
        await _speak_alert(
            case,
            Alert(
                id=str(uuid.uuid4()),
                text=answer,
                timestamp=time.time(),
                seq=next_alert_seq(),
                urgency="advisory",
                kind="agent",
                reasoning=f"Answer to: {question}",
            ),
        )
    return answer


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


@app.get("/api/agent_log")
async def agent_log(limit: int = 400):
    """Every reasoning step so far, newest last, across all cases.

    The Agent Log is the audit trail: it is where a reviewer sees which tool the
    agent reached for, what came back, and why it decided to speak or stay quiet.
    Live steps arrive over each case's WebSocket, but a page load has no history,
    so a refresh mid-case used to blank the whole trail. The frontend calls this
    on mount and dedupes against the live stream by step id.
    """
    steps = [step for case in store.all() for step in case.agent_steps]
    steps.sort(key=lambda s: s.get("timestamp", 0))
    return steps[-limit:]


class FrameRequest(BaseModel):
    case_id: str
    image_b64: str
    media_type: str = "image/jpeg"
    captured_at: float
    source: str = "unknown"


@app.post("/api/frame")
async def ingest_frame(req: FrameRequest):
    """Accept one point-of-view frame into the case's short ring buffer.

    Posted about once a second by whatever is wearing the camera — the iOS relay
    for Ray-Ban Display, or the browser with a webcam. Both send identical JSON
    and nothing downstream distinguishes them.

    Nothing is analysed here. Frames sit in memory, age out, and exactly one is
    ever looked at: the one nearest a moment that already mattered.
    """
    case = store.get(req.case_id)
    if case is None:
        return {"error": f"No such case: {req.case_id}"}

    held = frame_buffer.add(
        req.case_id,
        Frame(
            captured_at=req.captured_at,
            image_b64=req.image_b64,
            media_type=req.media_type,
            source=req.source,
        ),
    )
    return {"buffered": held}


async def look_back_at(case: CaseState, moment: float) -> None:
    """Describe the buffered frame from a given moment, if one is close enough.

    An alert fires a beat after the thing that caused it, so the frame worth
    seeing is a few seconds old — and nobody narrates a glance at a monitor, so
    there is no audio marking it either. Silent when the buffer has nothing
    nearby: a picture is context, never a precondition for an alert.
    """
    hit = frame_buffer.nearest(case.case_id, moment)
    if hit is None:
        return
    frame, delta = hit

    result = await describe_scene(
        frame.image_b64,
        frame.media_type,
        open_ledger=case.open_ledger_for_prompt(),
    )
    if result.get("scene") in ("unreadable", "other") and not result.get("readings"):
        return

    await _emit_agent_step(
        case,
        {
            "stage": "vision",
            "detail": f"[{frame.source}, {delta:.0f}s before the alert] "
            + (result.get("description") or ""),
            "evidence": ", ".join(
                f"{r['label']} appears to read {r['value']} ({r['legibility']})"
                for r in result.get("readings", [])
            ),
        },
    )


class ObserveRequest(BaseModel):
    case_id: str
    image_b64: str
    media_type: str = "image/jpeg"


@app.post("/api/observe")
async def observe(req: ObserveRequest):
    """Describe a point-of-view frame.

    Vision informs; speech records. Nothing returned here is written into
    vitals, allergies or medications — a reading off a screen is offered for a
    human to confirm, never documented. That boundary is why a camera can be in
    the loop at all without weakening the claim that every recorded fact has a
    source you can check.
    """
    case = store.get(req.case_id)
    if case is None:
        return {"error": f"No such case: {req.case_id}"}

    result = await describe_scene(
        req.image_b64,
        req.media_type,
        open_ledger=case.open_ledger_for_prompt(),
    )

    await _emit_agent_step(
        case,
        {
            "stage": "vision",
            "detail": result.get("description", "") or "nothing clinically relevant in frame",
            "evidence": ", ".join(
                f"{r['label']} appears to read {r['value']} ({r['legibility']})"
                for r in result.get("readings", [])
            ),
        },
    )

    # Only interrupt when the frame genuinely bears on outstanding work.
    spoken = (result.get("prompt_the_room") or "").strip()
    if spoken:
        await _speak_alert(
            case,
            Alert(
                id=str(uuid.uuid4()),
                text=spoken,
                timestamp=time.time(),
                seq=next_alert_seq(),
                urgency="advisory",
                kind="vision",
                reasoning=(
                    f"Seen: {result.get('description', '')}. "
                    "Unconfirmed — the camera does not record values, it asks."
                ),
            ),
        )

    return result


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
    """One whole utterance, all the way through.

        route  -> is this narration, or a question aimed at the assistant?
        extract-> facts and work items into the ledger and into Medplum
        tier 1 -> deterministic, FDA-verified contraindication check
        tier 2 -> open-ended reasoning over everything a fixed rule misses

    Both tiers can produce an alert and both go through the same speaker queue.
    They differ in what backs the claim: tier 1 cites an external classification,
    tier 2 cites its own reasoning. Neither prescribes treatment.
    """
    # A question addressed to the assistant is not case narration, and extracting
    # it would record the clinician's question as if it were a clinical finding.
    try:
        if await classify_segment(segment):
            await _emit_agent_step(case, {"step": "query", "text": segment})
            await _answer_query(case, segment)
            return
    except Exception:
        logger.exception("intent classification failed for case %s — treating as narration", case.case_id)

    try:
        await run_extraction_and_persist(case, segment, speaker_label)
    except Exception:
        logger.exception("extraction pipeline failed for case %s", case.case_id)
        return

    store.save()
    await ws_manager.send_json(case.case_id, _structured_payload(case))

    # --- tier 1: verified contraindication -----------------------------------
    try:
        for alert in await check_for_conflicts(case):
            await _speak_alert(case, alert)
            # Register it where tier 2 looks, so the agent doesn't say the same
            # thing again in its own words a second later.
            key = f"verified_conflict_{alert.allergen}_{alert.drug_class}".lower().replace(" ", "_")
            case.surfaced_issues[key] = {
                "alert_text": alert.text,
                "reasoning": "Raised by the FDA-verified contraindication check.",
                "timestamp": alert.timestamp,
            }
            await _emit_agent_step(
                case,
                {
                    "step": "decision",
                    "action_needed": True,
                    "already_surfaced": False,
                    "issue_key": key,
                    "alert_text": alert.text,
                    "urgency": alert.urgency,
                    "reasoning": (
                        f"Deterministic check: {alert.drug_class or 'drug class'} vs documented "
                        f"{alert.allergen} allergy. "
                        + (
                            f"FDA classification confirms: {'; '.join(alert.fda_classes[:2])}."
                            if alert.fda_verified
                            else "Drug class could not be verified against FDA data."
                        )
                    ),
                    "source": "verified_conflict",
                },
            )
    except Exception:
        logger.exception("conflict check failed for case %s", case.case_id)

    store.save()

    # --- tier 2: open-ended reasoning ----------------------------------------
    # Dispatched, not awaited. Extraction and tier 1 mutate the ledger, so they
    # have to stay strictly ordered — but tier 2 only reads context and appends
    # an alert, so it is safe to run alongside the next utterance.
    #
    # This matters because the agent runs a tool loop and can take longer than
    # the utterance that triggered it. Awaiting it made the pipeline slower than
    # real time: a live 57s clip backed the queue up so far that the last two
    # utterances — the allergy report and the drug order, i.e. the entire point —
    # were still queued when the case closed and never ran at all.
    asyncio.create_task(_run_agent_tier(case, segment))


# One tier-2 run per case at a time, plus at most one queued trigger.
#
# Dispatching tier 2 with create_task kept it off the critical path, but it also
# let several runs overlap — and the agent's only defence against repeating
# itself is reading case.surfaced_issues, which is written *after* a run returns.
# Overlapping runs therefore all read an empty list and each concluded it was
# saying something new. Measured on the demo clip: "get a full vital set" was
# announced three times under three different issue_keys, and one alert was
# emitted twice verbatim, 0.2s apart. Serializing per case is what makes the
# already-surfaced check work at all.
#
# The queue holds one trigger, not a backlog: if two utterances land while a run
# is in flight, the older one would reason about a stale picture by the time it
# got a turn, so the newer simply replaces it.
_agent_inflight: set[str] = set()
_agent_pending: dict[str, str] = {}


async def _agent_tier_once(case: CaseState, segment: str) -> None:
    decision = await run_agent_step(case, segment, lambda s: _emit_agent_step(case, s))
    if (
        decision.get("action_needed")
        and decision.get("alert_text")
        # Re-speaking a standing issue every utterance is how a room learns
        # to tune the system out.
        and not decision.get("already_surfaced")
    ):
        await _speak_alert(case, make_alert(decision))
        store.save()


async def _run_agent_tier(case: CaseState, segment: str) -> None:
    """Tier 2, off the critical path. It suppresses issues already raised, so
    tier 1's alert never gets echoed back in the agent's own words."""
    if case.case_id in _agent_inflight:
        _agent_pending[case.case_id] = segment
        return

    _agent_inflight.add(case.case_id)
    try:
        while segment:
            try:
                await _agent_tier_once(case, segment)
            except Exception:
                logger.exception("reasoning agent failed for case %s", case.case_id)
            segment = _agent_pending.pop(case.case_id, "")
    finally:
        _agent_inflight.discard(case.case_id)
        _agent_pending.pop(case.case_id, None)


@app.websocket("/ws/case/{case_id}")
async def case_ws(websocket: WebSocket, case_id: str):
    await websocket.accept()

    case = store.get(case_id) or store.create(case_id)
    ws_manager.register(case_id, websocket)

    loop = asyncio.get_running_loop()

    # Extraction is STATEFUL — the open ledger is passed into every call so that
    # "I've got ortho" can resolve a task created by an earlier utterance. That
    # makes concurrency a correctness bug, not just a performance question:
    # dispatching each utterance with create_task let the claim be extracted
    # before the request it answers had finished being written, so it saw an
    # empty ledger and silently resolved nothing. One worker, strict FIFO.
    utterances: asyncio.Queue = asyncio.Queue()

    async def utterance_worker() -> None:
        while True:
            text, speaker_label = await utterances.get()
            try:
                await _handle_finalized_segment(case, text, speaker_label)
            except Exception:
                logger.exception("utterance handling failed for case %s", case_id)
            finally:
                utterances.task_done()

    worker = asyncio.create_task(utterance_worker())

    async def on_utterance(text: str, speaker_index: int | None) -> None:
        """A whole utterance, not an acoustic fragment. See app/segmenter.py."""
        await utterances.put((text, case.speaker_label(speaker_index)))

    buffer = UtteranceBuffer(on_utterance=on_utterance)

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

        # The UI shows segments as they land — the transcript should feel live
        # even though extraction waits for a complete utterance.
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
            await buffer.add(text, speaker_index)

    stt_session = DeepgramSTTSession(
        on_transcript=on_transcript,
        # Real silence, from Deepgram, rather than a guess based on how long ago
        # a final happened to arrive.
        on_utterance_end=buffer.flush_now,
    )
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
        await buffer.close()  # don't lose a half-finished utterance
        # Let queued utterances finish before tearing down — the last thing said
        # is often the medication order, and dropping it loses the safety check.
        try:
            await asyncio.wait_for(utterances.join(), timeout=30)
        except asyncio.TimeoutError:
            logger.warning("utterance queue did not drain for case %s", case_id)
        worker.cancel()
        store.close(case_id)
        store.save()
        if case.encounter_id and medplum_client.configured:
            try:
                await medplum_client.close_encounter(case.encounter_id)
            except Exception:
                logger.exception("failed to close Medplum encounter for case %s", case_id)
        await ws_manager.send_json(case_id, {"type": "status", "status": "closed"})
        ws_manager.unregister(case_id)
