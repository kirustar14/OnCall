"""The reasoning agent: on every meaningful new fact extracted from the transcript,
gives Claude the FULL current case context and lets it decide for itself — using
tools if it wants more information — whether the clinician needs a warning,
additional info, or nothing at all. There is no hardcoded "if allergy + medication
then alert" rule; every decision is Claude's, and every step (trigger, tool call,
tool result, decision) is streamed out via on_step() for the Agent Log tab.
"""

import asyncio
import logging
import time
import uuid
from typing import Any, Awaitable, Callable, Optional

import anthropic

from app.case_store import Alert, CaseState, store
from app.config import ANTHROPIC_API_KEY, CLAUDE_MODEL
from app.medplum_client import medplum_client

logger = logging.getLogger("servare.agent")

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

Use your tools if you need more information before deciding:
- web_search: for medication information, contraindications, age-appropriateness, or alternatives
- search_patient_history: to check this patient's prior encounters, allergies, medications, or \
notes recorded outside of this current case

When you have enough information, respond in EXACTLY this format and nothing else:

ACTION: yes|no
ALERT: <if ACTION is yes, a short warning or piece of information written to be SPOKEN ALOUD to a \
clinician in an ER — direct, concise, one or two sentences. If ACTION is no, write "none">
REASONING: <one or two sentences explaining your decision — always present, even when ACTION is no>
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


def _parse_decision(text: str) -> dict[str, Any]:
    action_needed = False
    alert_text = ""
    reasoning = ""
    for line in text.splitlines():
        line = line.strip()
        if line.upper().startswith("ACTION:"):
            action_needed = "yes" in line.lower()
        elif line.upper().startswith("ALERT:"):
            alert_text = line.split(":", 1)[1].strip()
        elif line.upper().startswith("REASONING:"):
            reasoning = line.split(":", 1)[1].strip()
    if alert_text.lower() == "none":
        alert_text = ""
    return {"action_needed": action_needed, "alert_text": alert_text, "reasoning": reasoning}


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


def _build_user_prompt(context: dict[str, Any], trigger_text: str) -> str:
    import json

    return (
        f"Current case data:\n{json.dumps(context, indent=2)}\n\n"
        f"What was just newly recorded:\n{trigger_text}\n\n"
        "Decide whether the clinician needs a warning, additional information, or nothing."
    )


def _call_claude(messages: list[dict], tools: list[dict]):
    return _client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1536,
        thinking={"type": "adaptive"},
        output_config={"effort": "medium"},
        tools=tools,
        system=SYSTEM_PROMPT,
        messages=messages,
    )


async def _emit_server_tool_steps(response, on_step: OnStep) -> None:
    """web_search runs server-side inside the same response — scan for the
    server_tool_use / web_search_tool_result pairs and log them as steps."""
    pending_query: Optional[str] = None
    for block in response.content:
        if block.type == "server_tool_use" and block.name == "web_search":
            pending_query = block.input.get("query", "")
            await on_step({"step": "tool_call", "tool": "web_search", "query": pending_query})
        elif block.type == "web_search_tool_result":
            content = block.content
            if isinstance(content, list):
                titles = [r.get("title", "") for r in content[:3] if isinstance(r, dict)]
                summary = "; ".join(t for t in titles if t) or f"{len(content)} result(s)"
            else:
                summary = f"error: {getattr(content, 'error_code', 'unknown')}"
            await on_step({"step": "tool_result", "tool": "web_search", "result_summary": _truncate(summary)})
            pending_query = None


async def _execute_custom_tool(name: str, tool_input: dict, case: CaseState) -> str:
    if name == "search_patient_history":
        return await search_patient_history(case, tool_input.get("query", ""))
    return f"Unknown tool: {name}"


async def run_agent_step(case: CaseState, trigger_text: str, on_step: OnStep) -> dict[str, Any]:
    """The full agent loop for one triggering event. Streams every step via on_step()
    and returns the final decision dict {action_needed, alert_text, reasoning}."""

    await on_step({"step": "trigger", "text": trigger_text})

    if _client is None:
        decision = {"action_needed": False, "alert_text": "", "reasoning": "ANTHROPIC_API_KEY not configured."}
        await on_step({"step": "decision", **decision})
        return decision

    context = await _fetch_full_context(case)
    tools = [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 4},
        SEARCH_HISTORY_TOOL,
    ]
    messages = [{"role": "user", "content": _build_user_prompt(context, trigger_text)}]

    response = None
    final_text = ""
    for _ in range(MAX_TOOL_ITERATIONS):
        response = await asyncio.to_thread(_call_claude, messages, tools)
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

    decision = _parse_decision(final_text)
    await on_step({"step": "decision", **decision})
    return decision


def make_alert(decision: dict[str, Any]) -> Alert:
    return Alert(
        id=str(uuid.uuid4()),
        text=decision["alert_text"],
        reasoning=decision["reasoning"],
        timestamp=time.time(),
    )
