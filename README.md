# OnCall

****because the best doctors are the ones who pay attention* :) 

OnCall is a clinician-facing, always-on voice agent for the ER. It listens to the room —
EMS handoffs, nurse relays, physician orders — turns speech into structured clinical
data in real time, and uses an LLM reasoning agent (not a hardcoded rules engine) to
decide when something needs a doctor's attention: an allergy/medication conflict, a
dangerous vital trend, a dosing concern. When it decides something matters, it says so
out loud, in one short sentence, right when it matters.

Everything the agent hears, decides, and says is written to a FHIR-native clinical
record (Medplum) and to a permanent, fully-transparent reasoning log — every trigger,
every tool call, every decision, including the ones it chose to stay silent about.

Hackathon build. Runs off the laptop's own camera and microphone today; the capture
layer (`frontend/src/lib/audioCapture.js`) is isolated behind a small module specifically
so it can be swapped for a hands-free device (e.g. smart glasses) later without touching
anything downstream.

## Table of contents

- [Features](#features)
- [How the technology stack fits together](#how-the-technology-stack-fits-together)
- [Architecture](#architecture)
- [Repo layout](#repo-layout)
- [Setup](#setup)
- [Demo script](#demo-script)

## Features

### Live capture & transcription
- Streams mic audio from the browser over a WebSocket to the backend, which relays it
  to **Deepgram's live streaming STT** (`nova-2`, raw wire protocol, no SDK dependency)
  and gets interim + finalized transcript segments back in real time.
- Transcript renders live in the UI, with interim (in-progress) text visually
  distinguished from finalized text.

### Intent-aware structured extraction
- Every finalized transcript segment is first classified by a cheap Claude call as
  either **clinical narration** or **a direct question addressed to the assistant**
  (`backend/app/intent.py`) — so "patient's asking if he can have water" is correctly
  treated as narration (it reports the *patient's* question), while "is this medication
  safe given his allergy" is routed straight to the query agent instead of being
  mis-extracted as a clinical fact.
- Narration is extracted by Claude against a strict JSON schema
  (`backend/app/extraction.py`) into allergies, vitals, medications, free-text notes,
  and case details (name/age/sex/mechanism) — extracting *only* what's explicitly
  stated in that segment, never inferring or carrying facts forward.
- Every extracted fact is written to **Medplum (FHIR R4)** as a proper clinical
  resource (`AllergyIntolerance`, `Observation`, `MedicationRequest`) under the case's
  `Patient`/`Encounter`, tagged with a custom `source` extension (who said it — EMS,
  nurse, physician) and a `spoken-at` timestamp.

### Agentic reasoning — no hardcoded alert rules
- On every newly extracted fact, a Claude agent (`backend/app/agent.py`) is handed the
  *entire* current case picture and decides for itself whether the clinician needs a
  warning, more information, or nothing — there is no `if allergy and medication then
  alert` rule anywhere in the code. It reasons about allergy cross-reactivity,
  medication conflicts, age/weight-based dosing, dangerous vital patterns, and relevant
  history from the patient's other encounters.
- The agent has three tools and is explicitly prompted on when to use which:
  - **`search_patient_history`** — exact, structured Medplum lookups (this case and,
    when a patient identity is known, prior encounters).
  - **`semantic_patient_search`** — fuzzy semantic recall over a moss.dev index of
    every fact ever captured for this patient, across every case, including
    free-text notes worded completely differently than the query.
  - **`web_search`** (Claude's native server-side tool) — external clinical knowledge
    that has nothing to do with this specific patient.
- Runs identically for proactive monitoring and for clinician Q&A (`run_agent_step`
  and `run_query_step` share one tool-use loop), so both get the same reasoning
  quality and the same audit trail.

### Spoken alerts built for a doctor with seconds, not minutes
- A dedicated prompt section forces every spoken alert into closed-loop-radio style:
  one short sentence (~15 words), action first, no reasoning, no differential, no
  more than one action item — with the *full* clinical reasoning still captured
  in full, unrestricted, in the Agent Log entry behind it.
- TTS runs through **Deepgram Aura-2** at `speed=1.5` (the empirically-verified hard
  cap for the API's own speed parameter — chosen over a naive frontend
  `playbackRate` hack, which would also shift pitch).

### Repetition suppression, decided by the model
- The agent is shown every issue it has already surfaced this case (keyed by a
  short, stable, agent-assigned slug like `penicillin_allergy_amoxicillin_conflict`)
  and explicitly told to ask itself, before speaking: *have I already told the
  clinician this, and has anything changed?* If nothing changed, it logs its
  re-consideration silently instead of repeating itself out loud — there's no
  Python-side string/semantic matching driving this, it's entirely the model
  reading back its own history.

### Priority-ordered spoken-alert queue
- Only one thing can be spoken at a time, but the visual banner and Agent Log entry
  for every alert appear **immediately**, regardless of audio queue position.
- Playback is a single global priority queue (`frontend/src/lib/audioQueue.js`):
  sorted by urgency (`critical` > `advisory` > `informational`), then by a
  backend-assigned monotonic sequence number (earlier-triggered first), then by
  open-case-beats-closed-case as a final tiebreak. A critical alert that arrives
  mid-speech jumps to the front for the *next* slot — it never cuts off audio
  that's already playing.
- Each on-screen alert banner is tied to its own audio clip's lifecycle: it appears
  the instant its alert is triggered and disappears (with a brief fade) the instant
  *its own* clip finishes playing — not on a timer, not when a different alert
  finishes. The Agent Log keeps every alert, spoken or not, permanently.

### Fast semantic recall across a patient's whole history (moss.dev)
- Every captured fact is indexed into a dedicated moss.dev index
  (`servare-patient-context`, kept fully separate from any onboarding/starter index)
  the moment it's written to Medplum, tagged with `case_id`, `patient_name`,
  `fact_type`, and `timestamp`.
- `semantic_patient_search` queries that index filtered by patient identity first
  (so it can surface facts from a patient's *other* cases, not just the current one),
  falling back to the current case if no identity is known yet. Medplum stays the
  system of record; moss is purely the fast fuzzy-recall layer on top.
- Retrieval is fast enough to feel instant in the query loop (single-digit
  milliseconds through the production code path in testing).

### Hands-free & typed clinician Q&A
- A clinician can just *ask* — out loud, mid-case — and the same intent classifier
  routes it straight to the query agent instead of the extraction pipeline, whether
  it's a general-knowledge question ("what does tachycardic mean") answered instantly
  with no tool use, or a patient-specific one that pulls from Medplum/moss/web search.
- There's also a typed query box (in the Patient Database tab) that hits the same
  `/api/query` endpoint and gets the same reasoning, logging, and spoken-answer
  treatment as a voice-asked question.

### Full reasoning transparency — the Agent Log
- Every trigger, tool call, tool result, decision, and answer — across every case —
  streams live into a permanent Agent Log tab: which tool was called and why, what it
  returned, whether the agent decided to speak, and if it stayed silent, exactly why
  (already surfaced / not clinically relevant / nothing changed).
- Survives a page refresh (hydrated from `GET /api/agent_log` on load, deduplicated
  against live WebSocket updates by step ID).

### Multi-case, editorial-styled UI
- Open and monitor multiple cases in parallel as tabs, each with its own live video
  preview, transcript, structured data panel, and alert stack.
- A Patient Database tab lists every case (open and closed) with its structured data
  at a glance, plus the typed query box.
- Warm-neutral, editorial visual style (serif headlines, muted sans-serif body,
  pill-shaped buttons, restrained monochrome palette) with alert banners as the one
  deliberate high-contrast exception — urgency-tiered (critical/advisory/informational)
  so severity is visible before you even read the text.

## How the technology stack fits together

| Technology | Role | Where |
|---|---|---|
| **Deepgram STT** | Live streaming transcription over a raw WebSocket connection (`wss://api.deepgram.com/v1/listen`, `nova-2`, `interim_results`, `smart_format`) — no SDK, so behavior isn't tied to SDK version churn. | `backend/app/deepgram_stt.py` |
| **Deepgram TTS** | Turns each spoken-alert line into audio via the REST `/v1/speak` endpoint, `aura-2-asteria-en` at `speed=1.5` (Aura-2 is required for the `speed` param at all; Aura-1 voices 400 on it). | `backend/app/deepgram_tts.py` |
| **Anthropic Claude** (`claude-opus-5`) | Three distinct jobs, all through the Messages API: (1) structured extraction against a JSON schema (`output_config.format`), (2) a cheap classification call for question-vs-narration intent, (3) the agentic reasoning loop with tool use (`thinking: {type: "adaptive"}`, native `web_search` + two custom tools) for both proactive monitoring and clinician Q&A. | `backend/app/extraction.py`, `backend/app/intent.py`, `backend/app/agent.py` |
| **Medplum (FHIR R4)** | The system of record. OAuth2 client-credentials auth; every extracted fact becomes a real `AllergyIntolerance` / `Observation` / `MedicationRequest` under the case's `Patient`/`Encounter`, tagged with custom `source` and `spoken-at` extensions. Cross-encounter history queries back the `search_patient_history` tool. | `backend/app/medplum_client.py` |
| **moss.dev** | The fast semantic-recall layer *on top of* Medplum. One dedicated index (`servare-patient-context`), one long-lived `MossClient`, warmed up at server startup. Every fact gets indexed right after its Medplum write, filterable by patient name so it can recall facts (including free-text notes) from a patient's other cases. | `backend/app/moss_client.py` |
| **FastAPI + WebSockets** | Ties it all together: one WS connection per case streams audio in and transcript/structured-data/alert/agent-step events out; REST endpoints cover query, case listing, and the Agent Log. | `backend/app/main.py` |
| **React + Vite** | The clinician-facing UI: multi-case tabs, live transcript, structured data panels, the spoken-alert priority queue and banner lifecycle, the Patient Database, and the Agent Log. | `frontend/src/` |

## Architecture

```
Browser (getUserMedia) --PCM16 audio--> /ws/case/{id} (FastAPI WebSocket)
                                              |
                                    Deepgram live STT session
                                              |
                                  finalized transcript segment
                                              |
                          Claude intent check: narration, or a direct question?
                                     |                          |
                              [narration]                 [question]
                                     |                          |
                    Claude structured extraction        Claude query agent
                         (JSON schema)                  (same tool loop, below)
                                     |                          |
        Medplum writes (AllergyIntolerance /                    |
         Observation / MedicationRequest)                       |
                                     |                          |
              moss.dev semantic index (per fact)                |
                                     |                          |
                    Claude reasoning agent  <--------------------
              (full case context + surfaced-issue history)
                        tools: search_patient_history
                               semantic_patient_search (moss.dev)
                               web_search (native)
                                     |
                    decision: alert / already-surfaced / nothing
                                     |
                     alert banner (instant) + Agent Log entry (instant)
                                     |
                    Deepgram TTS -> priority-ordered spoken-alert queue
                                     |
                         audio streamed back over WS --> browser
                    (banner for that alert clears when its own clip ends)
```

Case state lives in an in-memory store (`backend/app/case_store.py`) — sufficient for a
demo session; Medplum is the durable clinical record, nothing else survives a backend
restart.

## Repo layout

```
backend/app/
  main.py            FastAPI app, WebSocket handler, REST endpoints, alert dispatch
  agent.py           The reasoning agent: proactive monitoring + clinician Q&A, tool loop
  extraction.py      Claude structured extraction -> Medplum + moss.dev writes
  intent.py          Narration-vs-question classifier
  deepgram_stt.py     Live streaming transcription (raw WS protocol)
  deepgram_tts.py     Spoken-alert synthesis (Aura-2, speed=1.5)
  medplum_client.py  FHIR R4 client: OAuth2, resource writes, cross-encounter history
  moss_client.py     moss.dev semantic index: setup, indexing, filtered search
  case_store.py      In-memory per-case state (thread-safe dict store)
  ws_manager.py      Per-case WebSocket connection registry

frontend/src/
  App.jsx                    Case tabs, view routing, Agent Log hydration/dedup
  hooks/useCaseSocket.js     Per-case WebSocket lifecycle, transcript/alert state
  lib/audioCapture.js        getUserMedia -> PCM16 chunks (swap point for other input devices)
  lib/audioQueue.js          Global priority queue for spoken-alert audio playback
  components/CaseSession.jsx Live video, transcript, structured data, alert banners
  components/AgentLog.jsx    Full reasoning transparency feed
  components/PatientDatabase.jsx / QueryBox.jsx   All-cases table + typed Q&A
  components/CaseTabBar.jsx  Multi-case tab navigation
```

## Setup

### Backend

```sh
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in the keys below
uvicorn app.main:app --reload --port 8000
```

`.env` variables:

| Variable | Required for | Notes |
|---|---|---|
| `DEEPGRAM_API_KEY` | Live transcription + spoken alerts | |
| `ANTHROPIC_API_KEY` | Extraction, intent classification, reasoning agent | |
| `MEDPLUM_CLIENT_ID` / `MEDPLUM_CLIENT_SECRET` | Writing clinical data to FHIR | Needs a Medplum **Client Application** (Project Admin → Client Applications) configured for client-credentials OAuth, with FHIR write access |
| `MEDPLUM_BASE_URL` | — | Defaults to `https://api.medplum.com/` |
| `MOSS_PROJECT_ID` / `MOSS_PROJECT_KEY` | Semantic cross-case recall | Optional — the agent simply drops the `semantic_patient_search` tool if unset |

### Frontend

```sh
cd frontend
npm install
cp .env.example .env   # defaults to localhost:8000, edit if backend runs elsewhere
npm run dev
```

Open the printed local URL (typically http://localhost:5173). Camera/mic permission
prompts require either `localhost` or HTTPS.

## Demo script

1. **EMS handoff** — establishes a case: "20 year old male, motor vehicle collision, blood
   pressure 90 over 60, heart rate 130, medic says he has a documented penicillin allergy."
2. **Outside source update** — a nurse relays new info mid-case: "Mom just told me at the
   bedside he's also allergic to sulfa drugs."
3. **Conflicting order** — a doctor orders a conflicting medication: "Start him on amoxicillin
   500 milligrams IV." This triggers the reasoning agent: a short spoken alert plus a
   full-reasoning Agent Log entry naming the penicillin allergy and a suggested alternative
   (e.g. vancomycin/azithromycin) — with the alert banner clearing itself the moment its
   own audio finishes.
4. **Ask it something** — either say it out loud or type it in the Patient Database query
   box: "what was his blood pressure on arrival?" or "is this medication safe given his
   allergy?" — watch it route to the query agent and, for the general-knowledge case,
   answer instantly with no tool calls at all.
