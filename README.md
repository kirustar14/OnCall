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
                                    Deepgram live STT session
                                              |
                                  finalized transcript segment
                                              |
                              Claude structured extraction (JSON schema)
                                              |
                        Medplum writes (AllergyIntolerance / Observation / MedicationRequest)
                                              |
                          Intervention check (allergy x medication conflict)
                                              |
                     Claude + web_search -> alternative medication + spoken alert
                                              |
                              Deepgram TTS -> audio back over WS --> browser
```

Case state lives in an in-memory store (`backend/app/case_store.py`) — sufficient for a
demo session; nothing survives a backend restart.

## Demo script

1. **EMS handoff** — establishes a case: "20 year old male, motor vehicle collision, blood
   pressure 90 over 60, heart rate 130, medic says he has a documented penicillin allergy."
2. **Outside source update** — a nurse relays new info mid-case: "Mom just told me at the
   bedside he's also allergic to sulfa drugs."
3. **Conflicting order** — a doctor orders a conflicting medication: "Start him on amoxicillin
   500 milligrams IV." This should trigger the intervention agent: a spoken + on-screen alert
   naming the penicillin allergy and a suggested alternative (e.g. vancomycin/azithromycin).
