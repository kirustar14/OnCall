"""Feature 5 query box: answer a free-text question about a specific case's
structured data using Claude."""

import asyncio
import json
import logging

import anthropic

from app.case_store import CaseState
from app.config import ANTHROPIC_API_KEY, CLAUDE_MODEL

logger = logging.getLogger("servare.query")

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

SYSTEM_PROMPT = """You answer a clinician's question about a single ER case using ONLY the \
structured case data provided as JSON. Give a direct, concise answer (1-3 sentences). If the data \
doesn't contain the answer, say so plainly — do not guess or fabricate clinical facts."""


def _case_to_context(case: CaseState) -> dict:
    return {
        "case_id": case.case_id,
        "status": case.status,
        "case_details": case.case_details,
        "vitals": case.vitals,
        "allergies": case.allergies,
        "medications": case.medications,
        "notes": case.notes,
        "transcript": case.running_transcript,
    }


async def answer_question(case: CaseState, question: str) -> str:
    if _client is None:
        return "ANTHROPIC_API_KEY not configured — cannot answer."

    context = _case_to_context(case)

    def _call() -> str:
        response = _client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=512,
            thinking={"type": "disabled"},
            output_config={"effort": "low"},
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": f"Case data:\n{json.dumps(context, indent=2)}\n\nQuestion: {question}",
                }
            ],
        )
        return next(b.text for b in response.content if b.type == "text")

    return await asyncio.to_thread(_call)
