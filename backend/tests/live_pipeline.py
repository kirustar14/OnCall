"""End-to-end live run: audio -> WebSocket -> STT -> ledger -> Medplum -> handoff.

Drives the real server exactly as the browser does, using the generated demo
audio, so every link is exercised: Deepgram STT with diarization, utterance
buffering, stateful extraction, resolution matching, the watchdog, the conflict
check, Medplum writes and the handoff briefing.

    ./venv/bin/python -m tests.make_demo_audio          # once
    ./venv/bin/uvicorn app.main:app --port 8201 &
    ./venv/bin/python -m tests.live_pipeline 8201
"""

import asyncio
import json
import sys
import wave

import httpx
import websockets

CASE_ID = "live-pipeline"
ROLES = [(0, "MEDIC"), (1, "DR. REYES"), (2, "NURSE OKAFOR")]


async def run(port: int) -> None:
    base = f"http://localhost:{port}"
    with wave.open("/tmp/servare-audio/demo-script.wav", "rb") as w:
        pcm = w.readframes(w.getnframes())

    alerts: list[str] = []
    prompts: list[str] = []
    transcripts: list[tuple[str, str]] = []

    async with websockets.connect(f"ws://localhost:{port}/ws/case/{CASE_ID}", max_size=None) as ws:

        async def reader() -> None:
            try:
                async for raw in ws:
                    m = json.loads(raw)
                    if m["type"] == "alert":
                        alerts.append(m["alert"]["text"])
                        print(f"\n  *** ALERT *** {m['alert']['text']}\n", flush=True)
                    elif m["type"] == "unowned_prompt":
                        prompts.append(m["text"])
                        print(f"\n  *** WATCHDOG *** {m['text']}\n", flush=True)
                    elif m["type"] == "transcript" and m.get("is_final"):
                        transcripts.append((m.get("speaker_label") or "?", m["text"]))
                        print(f"    [{m.get('speaker_label')}] {m['text'][:66]}", flush=True)
            except Exception:
                pass

        task = asyncio.create_task(reader())
        chunk = 16000 * 2 // 10  # 100 ms
        for i, off in enumerate(range(0, len(pcm), chunk)):
            await ws.send(pcm[off : off + chunk])
            await asyncio.sleep(0.1)
            if i == 80:  # once the voices exist, map them as the UI would
                async with httpx.AsyncClient() as c:
                    for idx, role in ROLES:
                        await c.post(
                            f"{base}/api/speaker-role",
                            json={"case_id": CASE_ID, "speaker_index": idx, "role": role},
                        )
        print("\n  …audio done, holding 40s for extraction + watchdog…\n", flush=True)
        await asyncio.sleep(40)
        task.cancel()

    async with httpx.AsyncClient(timeout=120) as c:
        case = (await c.get(f"{base}/api/cases/{CASE_ID}")).json()
        brief = (await c.post(f"{base}/api/handoff", json={"case_id": CASE_ID})).json()

    print("=" * 78)
    print("LEDGER")
    for w in case["work"]:
        mark = "  " if w["owner"] else ">>"
        print(f"{mark}[{w['kind']:<11}][{w['status']:<12}] {w['action'][:34]:<34} "
              f"owner={w['owner'] or '*** NO OWNER ***'}")
        if w["evidence"]:
            print(f"        evidence({w['evidence_source']}): {w['evidence'][:54]}")

    print(f"\n  vitals   : {[(v['name'], v['value']) for v in case['vitals']]}")
    print(f"  allergies: {[(a['allergen'], a.get('reaction'), a['source']) for a in case['allergies']]}")
    print(f"  meds     : {[m['name'] for m in case['medications']]}")
    print(f"  alerts   : {len(case['alerts'])}   watchdog prompts: {len(prompts)}")
    print(f"  medplum  : Patient/{case['patient_id']}")
    print(f"  FHIR Task: {sum(1 for w in case['work'] if w['fhir_task_id'])}/{len(case['work'])} written")

    print("\nHANDOFF — unresolved")
    for u in brief.get("unresolved", []):
        print(f"  - {u[:112]}")

    # --- scorecard -----------------------------------------------------------
    work_by_action = {w["action"].lower(): w for w in case["work"]}
    ortho = next((w for a, w in work_by_action.items() if "ortho" in a), None)
    checks = [
        ("transcript captured", len(transcripts) >= 8),
        ("speaker roles applied", any(t[0] in ("MEDIC", "DR. REYES", "NURSE OKAFOR") for t in transcripts)),
        ("vitals extracted", len(case["vitals"]) >= 4),
        ("allergy extracted with reaction", any(a.get("reaction") for a in case["allergies"])),
        ("allergy attributed to the mother", any("mother" in a["source"].lower() for a in case["allergies"])),
        ("work items created", len(case["work"]) >= 3),
        ("ortho task CLAIMED by the nurse", bool(ortho and ortho["owner"])),
        ("anticoagulation left unowned", any(not w["owner"] and "anticoag" in w["action"].lower() for w in case["work"])),
        ("watchdog spoke", len(prompts) >= 1),
        ("medication extracted", len(case["medications"]) >= 1),
        ("conflict alert fired", len(case["alerts"]) >= 1),
        ("FHIR Tasks written", all(w["fhir_task_id"] for w in case["work"])),
        ("handoff leads with unowned work", bool(brief.get("unresolved"))),
    ]
    print("\n" + "=" * 78)
    print("SCORECARD")
    for name, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"\n  {sum(1 for _, ok in checks if ok)}/{len(checks)} passed")


if __name__ == "__main__":
    asyncio.run(run(int(sys.argv[1]) if len(sys.argv) > 1 else 8000))
