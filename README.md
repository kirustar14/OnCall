# Servare

A clinician-facing, always-on voice agent for the ER. Transcribes live speech, extracts
structured clinical data into Medplum (FHIR), detects allergy/medication conflicts, and
speaks interventions back to the doctor.

Hackathon MVP — uses the laptop's own camera and microphone. The input source (`getUserMedia`
audio capture in `frontend/src/lib/audioCapture.js`) is isolated behind a small module so it can
be swapped for a glasses feed later without touching the rest of the pipeline.

## Stack

- **Frontend:** React + Vite (`frontend/`)
- **Backend:** FastAPI with WebSocket audio streaming (`backend/`)
- **Voice:** Deepgram (streaming STT + TTS)
- **Clinical data:** Medplum (FHIR), OAuth2 client-credentials auth
- **Reasoning:** Anthropic Claude (structured extraction, allergy/medication conflict checks with
  `web_search` for alternatives)

## Setup

### Backend

```sh
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in DEEPGRAM_API_KEY, ANTHROPIC_API_KEY, MEDPLUM_CLIENT_ID/SECRET
uvicorn app.main:app --reload --port 8000
```

Medplum requires a project with a **Client Application** (Project Admin → Client Applications)
configured for client-credentials OAuth, with FHIR write access. Put its client ID/secret in `.env`.

### Frontend

```sh
cd frontend
npm install
cp .env.example .env   # defaults to localhost:8000, edit if backend runs elsewhere
npm run dev
```

Open the printed local URL (typically http://localhost:5173). Camera/mic permission prompts
require either `localhost` or HTTPS.

## Architecture

```
Browser (getUserMedia) --PCM16 audio--> /ws/case/{id} (FastAPI WS)
                                              |
                          Deepgram nova-3-medical, diarize=true + keyterms
                                              |
                         finalized segment + speaker index -> role label
                                              |
                    Claude structured extraction (JSON schema), STATEFUL:
                    the open ledger goes IN, so "I've got it" can resolve
                                              |
              +-------------------------------+-------------------------------+
              |                               |                               |
        clinical FACTS                    WORK items                     RESOLUTIONS
              |                               |                               |
   AllergyIntolerance                     FHIR Task                 status -> in-progress /
   Observation                          (+ Provenance)              completed, evidence in note
   MedicationRequest                          |
              |                               |
              +--------> intervention check (allergy x medication)
                                              |
                             Deepgram Aura TTS -> audio over WS -> browser

     ... and in parallel, independent of anyone speaking:

     watchdog loop --> unowned + open + past grace period --> "Who is taking it?"
```

### The ledger

`Task` is not a stretch here — it is what the resource is for:

| Ledger concept | FHIR |
|---|---|
| the request | `Task.code.text` |
| why it matters | `Task.description` |
| task / uncertainty / conditional | `Task.businessStatus` |
| who owns it | `Task.owner` → `Practitioner` (omitted entirely when unowned) |
| **unowned work** | `GET /Task?owner:missing=true&status=ready` — standard search, not a custom concept |
| "in five minutes" | `Task.restriction.period.end` (verbatim trigger kept in `Task.note`) |
| evidence of completion | `Task.note`, tagged `[speech]` / `[vision]` |
| when it was actioned | `Task.executionPeriod` |
| who asserted it, on whose authority | `Provenance` (`agent.who` = agent Device, `agent.onBehalfOf` = human source) |

Status mapping follows Medplum's own guidance — `ready` is the actionable state for a
single-system implementation; `requested`/`received`/`accepted` are for cross-system handoffs:

    open -> ready     acknowledged -> in-progress     completed/answered -> completed

`Provenance` is what lets a clinician independently review the basis for anything the system
surfaced. That is the whole reason the agent can state facts without making recommendations.

### Persistence

In-memory, with an atomic JSON snapshot on disk (`SERVARE_SNAPSHOT`, default
`.servare-state.json`) rewritten on mutation and reloaded on startup — so `--reload` firing
mid-demo doesn't wipe the ledger.

### Speaker attribution

Diarization tells us reliably *that* the speaker changed; it can never tell us *who* they are.
A human maps each voice index to a role once via `POST /api/speaker-role`, and from then on
every task that voice claims is attributed correctly. Deliberately a label on an index — not
biometric identification.

## Demo script

The arc is **fill the ledger, disturb it, drain it.**

1. **EMS handoff** — establishes the case, and establishes what we *don't* know:
   "Nineteen year old female, front seat passenger, GCS 13, she's confused, **she can't give us
   a history**. Open left tibia fracture, gross contamination. Heart rate 122, blood pressure
   104 over 68. Splinted in the field, no antibiotics given."
2. **Unowned work appears** — the physician fires off three requests at once: "Someone find out
   whether she's anticoagulated, get ortho down here, and repeat that pressure in five minutes."
   Nobody is named for the first one.
3. **One claimed, one dangling** — a nurse takes one: "I've got ortho, I'm calling them now."
   Ortho goes `in-progress` with an owner; anticoagulation stays `NO OWNER`.
4. **The agent asks the room** — after the grace period the watchdog speaks: *"Determine
   anticoagulation status is still unanswered and nobody has taken it. Who is taking it?"*
   It asks. It never assigns, and it never advises.
5. **Outside-source update** — "Mom just told me she has a severe penicillin allergy,
   anaphylaxis as a child." The `AllergyIntolerance` lands with a `Provenance` recording the
   mother as the source, relayed by the nurse.
6. **Conflicting order** — "Let's get **ampicillin-sulbactam**, three grams IV, push it."
   Checked against the documented allergy by drug *class*, not string match — "ampicillin-sulbactam"
   contains no substring a keyword matcher would flag, and the FDA classes it as a
   *Penicillin-class Antibacterial*.
7. **Handoff** — someone who wasn't there gets five panels instead of a transcript, with the
   still-unowned anticoagulation question leading the unresolved list.

**On the drug pairing:** ampicillin-sulbactam is a genuine guideline choice for a contaminated
open fracture, which is what makes the conflict real rather than contrived. The earlier
amoxicillin → vancomycin example was not — amoxicillin isn't what you'd reach for here, and a
clinician in the audience would notice.
