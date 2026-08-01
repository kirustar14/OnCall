"""The reasoning agent. Two entry points share one Claude + tool loop:

- run_agent_step: on every meaningful new fact extracted from the transcript, gives
  Claude the FULL current case context and lets it decide for itself — using tools
  if it wants more information — whether the clinician needs a warning, additional
  info, or nothing at all. No hardcoded "if allergy + medication then alert" rule.
- run_query_step: the same treatment for a clinician's typed/spoken question — Claude
  decides whether the case data alone answers it, or whether it needs to search first.

Every step (trigger, tool call, tool result, decision/answer) streams out via
on_step() for the Agent Log tab.
"""

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Awaitable, Callable, Optional

import anthropic

from app.case_store import Alert, CaseState, next_alert_seq, store
from app.config import ANTHROPIC_API_KEY, CLAUDE_MODEL
from app.medplum_client import medplum_client
from app.moss_client import moss_client

logger = logging.getLogger("oncall.agent")

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

MAX_TOOL_ITERATIONS = 4

OnStep = Callable[[dict[str, Any]], Awaitable[None]]

SYSTEM_PROMPT = """You are monitoring an active ER case in real time. You will be given the \
full current case data (allergies, medications, vitals, notes, case details) and a description \
of what was just newly recorded. Decide for yourself whether the clinician needs a warning, \
additional information, or nothing at all right now — there is no fixed rulebook, use your \
clinical judgment.

Consider allergies and cross-reactivity, medication conflicts, age- or weight-based dosing and \
appropriateness, dangerous vital sign patterns, and relevant history from this patient's prior \
encounters. Do not limit yourself to allergy/medication conflicts — flag anything clinically \
relevant, including a medication that's inappropriate for the patient's stated age, even with no \
allergy involved at all.

Use your tools if you need more information before deciding. Each is for a different kind of \
lookup, so pick the one that actually fits rather than reaching for all of them or picking at random:
- search_patient_history: structured, exact lookups in Medplum — the actual allergy, medication and \
vital records on file for this patient, in this case and (when an identity is known) prior ones. Use \
this when you need to know precisely what is documented.
- semantic_patient_search: fuzzy, meaning-based recall across everything ever said or recorded for \
this patient, including free-text notes, even when it is worded nothing like your query. "Any prior \
drug reactions" can surface a note that said "got hives after amoxicillin" without sharing a single \
keyword. Use it for anything that would only ever exist as a note rather than a structured field.
- web_search: external clinical knowledge with nothing to do with this specific patient — drug \
classes, contraindications, dosing references, definitions.

You are an assistant who filters constantly, not a system that repeats itself. You will be shown \
a list of issues you've already surfaced to the clinician earlier in this case. Before deciding to \
speak, ask yourself: have I already told the clinician this specific thing, and has anything \
changed since? If what you're about to say is the same underlying concern as one already listed — \
same allergy status, same medication order, no meaningful vital change — mark it as already \
surfaced instead of speaking again. Only speak again about an issue if something genuinely changed \
about it: it was confirmed or denied, a new medication now intersects with it, or a vital worsened \
significantly. A new, unrelated fact being recorded elsewhere in the case is not, by itself, a \
reason to repeat an old warning.

SPOKEN ALERT STYLE — this applies ONLY to your ALERT field, and it is a hard constraint, separate \
from your REASONING (which has no length limit and can stay as detailed as the case warrants):

You are speaking to a doctor mid-treatment who has seconds, not minutes. Say the single most \
urgent thing to do right now, in one short sentence. Everything else — the "why," the differential, \
treatment nuance, secondary considerations — goes in your written REASONING, not your spoken line. \
Your ALERT must sound like closed-loop radio communication, not a clinical note read aloud:
- One sentence, ideally under ~15 words. A second short sentence is allowed ONLY for a second \
genuinely critical action — never more than 2 short sentences total.
- Lead with the ACTION — what to do or check right now — not the reasoning behind it.
- Cut every piece of secondary/supporting detail. No context, no differential, no nuance.
- Never pack more than one distinct action item in. If there are two things, say the more urgent \
one — later triggers and the spoken-alert priority queue handle the rest.

WRONG (buries the action in reasoning, way too long): "This patient is unconscious after a \
head-impact fall with no vitals or GCS documented yet — while you control the bleeding, maintain \
C-spine precautions and get airway, GCS and a full vital set now. Also consider that scalp bleeding \
may be masking intracranial injury..."
RIGHT: "No vitals or GCS recorded yet on this patient."

If what you have to say is genuinely just background information with no time-sensitive action \
(e.g. a suturing technique note, a minor context detail) — either compress it to one short \
actionable line if it's worth saying at all, or set ACTION to no and put it in REASONING only. Low \
time-sensitivity, detailed, or "nice to know" content does not belong in a spoken alert.

WHERE "ACTION" STOPS. Leading with the action means directing attention and asking for \
information — "airway and C-spine first", "get vitals and GCS now", "check whether she's \
anticoagulated", "that order is a penicillin". It does NOT mean prescribing therapy. Never name a \
substitute drug, never give a dose or rate to switch to, never say what to administer instead. \
Say what is true and what is worth looking at; the clinician chooses the treatment, because they \
have the airway, the room and the patient in front of them and you have a transcript. Software \
that recommends a therapy is a different thing, clinically and legally, from software that \
surfaces a fact a clinician can independently check — and the second is what you are. This limits \
what you SAY, never what you notice: keep flagging anything clinically relevant.

Concretely, these are over the line and must never appear in ALERT, however urgent the case looks: \
naming a treatment to start ("start blood products", "give TXA", "switch to clindamycin"), naming a \
management strategy ("assume occult hemorrhage", "treat as a head bleed"), or instructing on \
clinical handling ("keep C-spine precautions on"). Each of those decides care. The same underlying \
observation is fine stated as a fact or a request: "heart rate 122 and confused, no BP since \
arrival", "still no antibiotic given on a contaminated open fracture", "that order is a \
penicillin". Say what is recorded, what is missing, and what was asked for. Put the clinical \
interpretation in REASONING, where it informs without directing.

A separate deterministic check already covers documented-allergy versus ordered-drug conflicts, \
verified against the FDA's own classification via NIH RxNav. When it has fired, that issue is in \
the already-surfaced list — don't repeat it. Your value is everything a fixed rule cannot catch.

When you have enough information, respond in EXACTLY this format and nothing else:

ACTION: yes|no
ISSUE_KEY: <a short, stable snake_case label for the underlying concern this is about, e.g. \
"penicillin_allergy_amoxicillin_conflict" or "hr_bradycardia_52". Always present when ACTION is \
yes — use the SAME key an already-surfaced issue used if this is that same issue, so it can be \
matched up.>
ALREADY_SURFACED: yes|no — <yes ONLY if this is the same issue as one already listed above AND \
nothing material has changed since; no if this is new, or if something about it has genuinely \
changed. Always present when ACTION is yes.>
ALERT: <if ACTION is yes, the SHORT spoken line per SPOKEN ALERT STYLE above — one sentence, \
under ~15 words, action first, nothing else. If ACTION is no, write "none">
URGENCY: critical|advisory|informational — <only if ACTION is yes. "critical" = an active, \
immediate danger (e.g. an allergy/medication conflict, a dangerous vital pattern) that should \
interrupt whatever the clinician is doing. "advisory" = worth flagging soon but not an emergency \
(e.g. a dosing caution, a missing piece of context). "informational" = background/context that's \
useful but not urgent. If ACTION is no, write "n/a">
REASONING: <the full clinical picture, differentials, and treatment nuance — this is NOT spoken \
aloud and has no length limit, unlike ALERT. Always present, even when ACTION is no. If \
ALREADY_SURFACED is yes, explain briefly why nothing has changed.>


Writing style: avoid em dashes. Use a comma, a full stop, or a colon instead. Keep punctuation plain so a text-to-speech voice reads it naturally and a clinician scanning the screen is not slowed down by ornament.
"""

QUERY_SYSTEM_PROMPT = """You are answering a clinician's question, spoken or typed, during a live \
ER case. It might be a general medical knowledge question with nothing to do with this specific \
patient (e.g. "what does tachycardic mean"), or it might be about this specific case (e.g. "what \
was the BP on arrival?", "is this medication safe given his allergy?"). You have the full current \
case data (allergies, medications, vitals, notes, case details) for reference when it's needed.

Speed matters — this answer gets spoken back to a clinician who is waiting. If you already \
confidently know the answer (a medical term definition, general clinical knowledge, or something \
answerable straight from the case data shown to you), answer immediately — do NOT use a tool just \
out of caution or habit. Only reach for a tool when you genuinely need information you don't \
already have, and pick the one that actually fits:
- search_patient_history: structured, exact lookups in Medplum — the allergy, medication and vital \
records on file, this case and (when identity is known) prior ones.
- semantic_patient_search: fuzzy, meaning-based recall across everything ever said or recorded for \
this patient, including free-text notes, even worded nothing like your query ("any prior drug \
reactions" can surface a note that said "got hives after amoxicillin"). Use it for recall a \
structured lookup would miss.
- web_search: external clinical knowledge with nothing to do with this specific patient — drug \
safety, dosing limits, interactions, or facts you are not confident about or that may have changed \
since your training.

Decide for yourself, question by question — don't search when you already know the answer, and \
don't skip searching when real clinical judgment requires information you don't have.

When you have your answer, respond in EXACTLY this format and nothing else:

ANSWER: <a direct, concise answer, one to three sentences, written to be spoken aloud to a \
clinician. If you genuinely cannot answer from the data or your tools, say so plainly.>


Writing style: avoid em dashes. Use a comma, a full stop, or a colon instead. Keep punctuation plain so a text-to-speech voice reads it naturally and a clinician scanning the screen is not slowed down by ornament.
"""

SEARCH_HISTORY_TOOL = {
    "name": "search_patient_history",
    "description": (
        "Look up this patient's history from OUTSIDE the current case — prior encounters, "
        "allergies, medications, or notes recorded in other visits. Use this before deciding "
        "if you need more context than what's been said in the current conversation."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "What you're looking for, e.g. 'prior adverse drug reactions' or "
                    "'previous encounters and diagnoses'"
                ),
            }
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}

SEMANTIC_SEARCH_TOOL = {
    "name": "semantic_patient_search",
    "description": (
        "Fuzzy, meaning-based recall across everything ever said or recorded for this patient — "
        "including free-text notes — from any of their cases, even if it is worded completely "
        "differently than your query. Finds what an exact lookup would miss, especially anything "
        "that only ever existed as a note rather than a structured field (e.g. 'any prior adverse "
        "drug reactions' can surface a note that said 'got hives after amoxicillin', with no "
        "shared keywords)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "What you are trying to recall, in plain language, e.g. "
                    "'prior reaction to an antibiotic'"
                ),
            }
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}


def _build_tools() -> list[dict]:
    """Offer semantic recall only when it can actually answer.

    An advertised tool that always returns nothing is worse than an absent one:
    the model spends a turn calling it, gets an empty result, and may conclude the
    patient has no prior history rather than that the index is switched off.
    """
    tools = [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 4},
        SEARCH_HISTORY_TOOL,
    ]
    if moss_client.configured:
        tools.append(SEMANTIC_SEARCH_TOOL)
    return tools


def _parse_decision(text: str) -> dict[str, Any]:
    action_needed = False
    already_surfaced = False
    issue_key = ""
    alert_text = ""
    reasoning = ""
    urgency = "advisory"
    for line in text.splitlines():
        line = line.strip()
        if line.upper().startswith("ACTION:"):
            action_needed = "yes" in line.lower()
        elif line.upper().startswith("ISSUE_KEY:"):
            issue_key = line.split(":", 1)[1].strip()
        elif line.upper().startswith("ALREADY_SURFACED:"):
            already_surfaced = "yes" in line.lower()
        elif line.upper().startswith("ALERT:"):
            alert_text = line.split(":", 1)[1].strip()
        elif line.upper().startswith("URGENCY:"):
            value = line.split(":", 1)[1].strip().lower()
            for candidate in ("critical", "advisory", "informational"):
                if candidate in value:
                    urgency = candidate
                    break
        elif line.upper().startswith("REASONING:"):
            reasoning = line.split(":", 1)[1].strip()
    if alert_text.lower() == "none":
        alert_text = ""
    return {
        "action_needed": action_needed,
        "already_surfaced": already_surfaced,
        "issue_key": issue_key,
        "alert_text": alert_text,
        "reasoning": reasoning,
        "urgency": urgency,
    }


def _parse_answer(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line.upper().startswith("ANSWER:"):
            return line.split(":", 1)[1].strip()
    # Lenient fallback — still return something sensible if the model didn't
    # follow the exact format rather than silently failing.
    return text.strip()


def _truncate(text: str, limit: int = 220) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def _fetch_full_context(case: CaseState) -> dict[str, Any]:
    """Full current case picture. Reads back from Medplum (the source of truth) when
    configured, rather than trusting the in-memory mirror could be stale; falls back
    to the mirror (which already accumulates every segment, not just the latest) if
    Medplum isn't configured or the read fails."""

    if medplum_client.configured and case.encounter_id:
        try:
            resources = await medplum_client.fetch_encounter_resources(case.encounter_id)
            return {
                "allergies": resources["allergies"],
                "vitals": resources["vitals"],
                "medications": resources["medications"],
                "notes": case.notes,
                "case_details": case.case_details,
            }
        except Exception:
            logger.exception("agent: failed to read full context from Medplum for case %s", case.case_id)

    return {
        "allergies": case.allergies,
        "vitals": case.vitals,
        "medications": case.medications,
        "notes": case.notes,
        "case_details": case.case_details,
    }


async def search_patient_history(case: CaseState, query: str) -> str:
    """The search_patient_history tool's implementation. Looks for this patient's data
    outside the current case: first via Medplum (real cross-encounter FHIR history if a
    Patient name lets us find other Patient records), and — always, since it's fast and
    works even without Medplum — via other in-memory cases that share the same stated
    patient name (the hackathon-simple identity match)."""

    name = (case.case_details.get("name") or "").strip()
    if not name:
        return (
            "No patient name has been recorded for this case yet, so patient history can't be "
            "looked up by identity. Only the current case's own data is available."
        )

    findings: list[str] = []

    # In-memory cross-case match (works even without Medplum configured).
    for other in store.all():
        if other.case_id == case.case_id:
            continue
        other_name = (other.case_details.get("name") or "").strip()
        if not other_name or other_name.lower() != name.lower():
            continue
        parts = []
        if other.allergies:
            parts.append("allergies: " + ", ".join(a["allergen"] for a in other.allergies))
        if other.medications:
            parts.append("medications: " + ", ".join(m["name"] for m in other.medications))
        if other.notes:
            parts.append("notes: " + " / ".join(other.notes))
        if parts:
            findings.append(f"Other case {other.case_id[:8]} ({other.status}): " + "; ".join(parts))

    # Medplum cross-encounter history, if configured.
    if medplum_client.configured:
        try:
            patients = await medplum_client.search_patients_by_name(name)
            for patient in patients:
                if patient.get("id") == case.patient_id:
                    continue
                history = await medplum_client.fetch_patient_history(
                    patient["id"], exclude_encounter_id=case.encounter_id
                )
                parts = []
                if history["allergies"]:
                    parts.append("allergies: " + ", ".join(a["allergen"] for a in history["allergies"]))
                if history["medications"]:
                    parts.append("medications: " + ", ".join(m["name"] for m in history["medications"]))
                if parts:
                    findings.append(f"Medplum patient {patient['id']}: " + "; ".join(parts))
        except Exception:
            logger.exception("agent: Medplum patient-history search failed for case %s", case.case_id)

    if not findings:
        return f"No prior encounters or history found for a patient named '{name}'."

    return f"Query: {query}\n\n" + "\n".join(findings)


async def search_patient_semantic(case: CaseState, query: str) -> str:
    """The semantic_patient_search tool's implementation — moss.dev fuzzy recall
    across everything captured for this patient (by name when known, else scoped
    to this case), including the free-text notes that search_patient_history's
    structured Medplum lookup cannot reach at all."""

    name = (case.case_details.get("name") or "").strip()
    results = await moss_client.search(
        query, patient_name=name or None, case_id=case.case_id, top_k=5
    )

    if not results:
        scope = f"a patient named '{name}'" if name else "this case (no patient name recorded yet)"
        return f"No semantically related facts found for {scope}."

    lines = []
    for r in results:
        meta = r["metadata"]
        other_case = meta.get("case_id", "")
        case_note = f" [case {other_case[:8]}]" if other_case and other_case != case.case_id else ""
        lines.append(f'- "{r["text"]}"{case_note} (relevance {r["score"]:.2f})')

    return f"Query: {query}\n\n" + "\n".join(lines)


def _time_ago(timestamp: float) -> str:
    seconds = max(0, time.time() - timestamp)
    if seconds < 60:
        return "moments ago"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = int(minutes // 60)
    return f"{hours} hour{'s' if hours != 1 else ''} ago"


def _format_surfaced_issues(surfaced_issues: dict[str, dict[str, Any]]) -> str:
    if not surfaced_issues:
        return "(none yet — this would be the first thing surfaced for this case)"
    lines = []
    for key, info in surfaced_issues.items():
        lines.append(f'- [{key}] "{info["alert_text"]}" (said {_time_ago(info["timestamp"])})')
    return "\n".join(lines)


def _build_user_prompt(context: dict[str, Any], trigger_text: str, surfaced_issues: dict[str, dict[str, Any]]) -> str:
    return (
        f"Current case data:\n{json.dumps(context, indent=2)}\n\n"
        f"Issues already surfaced to the clinician earlier in this case:\n"
        f"{_format_surfaced_issues(surfaced_issues)}\n\n"
        f"What was just newly recorded:\n{trigger_text}\n\n"
        "Decide whether the clinician needs a warning, additional information, or nothing."
    )


def _build_query_prompt(context: dict[str, Any], question: str) -> str:
    return (
        f"Current case data:\n{json.dumps(context, indent=2)}\n\n"
        f"Clinician's question:\n{question}"
    )


def _call_claude(messages: list[dict], tools: list[dict], system_prompt: str):
    return _client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1536,
        thinking={"type": "adaptive"},
        output_config={"effort": "medium"},
        tools=tools,
        system=system_prompt,
        messages=messages,
    )


async def _emit_server_tool_steps(response, on_step: OnStep) -> None:
    """web_search runs server-side inside the same response — scan for the
    server_tool_use / web_search_tool_result pairs and log them as steps."""
    for block in response.content:
        if block.type == "server_tool_use" and block.name == "web_search":
            await on_step({"step": "tool_call", "tool": "web_search", "query": block.input.get("query", "")})
        elif block.type == "web_search_tool_result":
            content = block.content
            if isinstance(content, list):
                titles = [r.get("title", "") for r in content[:3] if isinstance(r, dict)]
                summary = "; ".join(t for t in titles if t) or f"{len(content)} result(s)"
            else:
                summary = f"error: {getattr(content, 'error_code', 'unknown')}"
            await on_step({"step": "tool_result", "tool": "web_search", "result_summary": _truncate(summary)})


async def _execute_custom_tool(name: str, tool_input: dict, case: CaseState) -> str:
    if name == "search_patient_history":
        return await search_patient_history(case, tool_input.get("query", ""))
    if name == "semantic_patient_search":
        return await search_patient_semantic(case, tool_input.get("query", ""))
    return f"Unknown tool: {name}"


async def _run_reasoning_loop(
    case: CaseState, system_prompt: str, user_message: str, on_step: OnStep, tools: Optional[list[dict]] = None
) -> str:
    """Shared Claude + tool loop used by both run_agent_step and run_query_step.
    Streams every tool call/result via on_step() and returns the model's final
    (unparsed) text response."""

    if tools is None:
        tools = _build_tools()

    messages = [{"role": "user", "content": user_message}]

    response = None
    final_text = ""
    for _ in range(MAX_TOOL_ITERATIONS):
        response = await asyncio.to_thread(_call_claude, messages, tools, system_prompt)
        await _emit_server_tool_steps(response, on_step)

        if response.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            continue

        custom_tool_uses = [b for b in response.content if b.type == "tool_use"]
        messages.append({"role": "assistant", "content": response.content})

        if not custom_tool_uses:
            final_text = "\n".join(b.text for b in response.content if b.type == "text")
            break

        tool_results = []
        for tu in custom_tool_uses:
            await on_step({"step": "tool_call", "tool": tu.name, "query": tu.input.get("query", "")})
            try:
                result_text = await _execute_custom_tool(tu.name, tu.input, case)
            except Exception:
                logger.exception("agent: tool %s failed for case %s", tu.name, case.case_id)
                result_text = f"Tool {tu.name} failed to execute."
            await on_step({"step": "tool_result", "tool": tu.name, "result_summary": _truncate(result_text)})
            tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "content": result_text})
        messages.append({"role": "user", "content": tool_results})
    else:
        final_text = "\n".join(b.text for b in response.content if b.type == "text") if response else ""

    return final_text


async def run_agent_step(case: CaseState, trigger_text: str, on_step: OnStep) -> dict[str, Any]:
    """The full agent loop for one proactively-triggered event (a new extracted fact).
    Returns the final decision dict {action_needed, alert_text, urgency, reasoning}."""

    await on_step({"step": "trigger", "kind": "extraction", "text": trigger_text})

    if _client is None:
        decision = {
            "action_needed": False,
            "already_surfaced": False,
            "issue_key": "",
            "alert_text": "",
            "urgency": "advisory",
            "reasoning": "ANTHROPIC_API_KEY not configured.",
        }
        await on_step({"step": "decision", **decision})
        return decision

    context = await _fetch_full_context(case)
    user_message = _build_user_prompt(context, trigger_text, case.surfaced_issues)
    final_text = await _run_reasoning_loop(case, SYSTEM_PROMPT, user_message, on_step)

    decision = _parse_decision(final_text)
    await on_step({"step": "decision", **decision})

    if decision["action_needed"] and not decision["already_surfaced"]:
        key = decision["issue_key"] or str(uuid.uuid4())
        case.surfaced_issues[key] = {
            "alert_text": decision["alert_text"],
            "reasoning": decision["reasoning"],
            "timestamp": time.time(),
        }

    return decision


async def run_query_step(case: CaseState, question: str, on_step: OnStep) -> dict[str, Any]:
    """The full agent loop for a clinician's typed/spoken question. Returns
    {"answer": <text>}. Logged just like a proactive decision: a "clinician query"
    trigger, any tool calls it makes, then the final answer."""

    await on_step({"step": "trigger", "kind": "query", "text": f"Clinician query: {question}"})

    if _client is None:
        answer = "ANTHROPIC_API_KEY not configured — cannot answer."
        await on_step({"step": "answer", "question": question, "text": answer, "urgency": "advisory"})
        return {"answer": answer}

    context = await _fetch_full_context(case)
    user_message = _build_query_prompt(context, question)
    final_text = await _run_reasoning_loop(case, QUERY_SYSTEM_PROMPT, user_message, on_step)

    answer = _parse_answer(final_text)
    # Query answers are always "advisory": a direct clinician request deserves
    # prompt handling, but it shouldn't preempt an active critical warning the
    # way a genuinely urgent proactive decision does.
    await on_step({"step": "answer", "question": question, "text": answer, "urgency": "advisory"})
    return {"answer": answer}


def make_alert(decision: dict[str, Any]) -> Alert:
    return Alert(
        id=str(uuid.uuid4()),
        text=decision["alert_text"],
        reasoning=decision["reasoning"],
        urgency=decision.get("urgency", "advisory"),
        timestamp=time.time(),
        seq=next_alert_seq(),
    )
