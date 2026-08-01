"""Drive the whole pipeline over the real WebSocket with real audio.

Uses the Aura-generated demo track (tests/make_demo_audio.py), so this exercises
every leg: browser-equivalent WS client -> FastAPI -> Deepgram STT (nova-3-medical,
diarize, keyterms) -> utterance buffering -> Claude extraction -> Medplum writes
-> conflict surfacing -> Aura TTS -> back over the socket.

    ./venv/bin/uvicorn app.main:app --port 8200 &
    ./venv/bin/python -m tests.live_ws_run 8200
"""

import asyncio
import json
import sys
import wave

import httpx
import websockets

WAV = "/tmp/servare-audio/demo-script.wav"
CASE = "livecase"
ROLES = [(0, "MEDIC"), (1, "DR. REYES"), (2, "NURSE OKAFOR")]


async def run(port: int) -> None:
    with wave.open(WAV, "rb") as w:
        pcm = w.readframes(w.getnframes())
    print(f"streaming {len(pcm) / 32000:.1f}s of audio\n", flush=True)

    events: list[dict] = []
    async with websockets.connect(f"ws://localhost:{port}/ws/case/{CASE}", max_size=None) as ws:

        async def reader() -> None:
            try:
                async for raw in ws:
                    m = json.loads(raw)
                    events.append(m)
                    if m["type"] == "transcript" and m.get("is_final"):
                        print(f"   seg [{m.get('speaker_label')}] {m['text'][:60]}", flush=True)
                    elif m["type"] == "alert":
                        print(f"\n  *** ALERT *** {m['alert']['text']}\n", flush=True)
                    elif m["type"] == "unowned_prompt":
                        print(f"\n  *** WATCHDOG *** {m['text']}\n", flush=True)
            except Exception:
                pass

        async def assign_roles() -> None:
            # Wait until all three voices have been heard, as a user would.
            await asyncio.sleep(33)
            async with httpx.AsyncClient(timeout=10) as c:
                for idx, role in ROLES:
                    await c.post(
                        f"http://localhost:{port}/api/speaker-role",
                        json={"case_id": CASE, "speaker_index": idx, "role": role},
                    )
            print("\n   [roles assigned]\n", flush=True)

        reader_task = asyncio.create_task(reader())
        roles_task = asyncio.create_task(assign_roles())

        # Trailing silence matters: a browser mic never stops sending, it sends
        # quiet. Deepgram endpoints on silence in the audio, not on absence of
        # data — cutting the stream dead leaves the final utterance unfinalized,
        # and the final utterance is the medication order.
        pcm = pcm + (b"\x00\x00" * 16000 * 4)

        for i in range(0, len(pcm), 3200):  # 100 ms
            await ws.send(pcm[i : i + 3200])
            await asyncio.sleep(0.1)

        print("\n   …audio done, holding 30s…\n", flush=True)
        await asyncio.sleep(30)
        reader_task.cancel()
        roles_task.cancel()

    async with httpx.AsyncClient(timeout=20) as c:
        case = (await c.get(f"http://localhost:{port}/api/cases/{CASE}")).json()

    print("=" * 78)
    print("LEDGER")
    for w in case["work"]:
        owner = w["owner"] or "*** NO OWNER ***"
        print(f"  [{w['kind']:<11}][{w['status']:<12}] {w['action'][:38]:<38} owner={owner}")
        if w["evidence"]:
            print(f"        evidence({w['evidence_source']}): {w['evidence'][:58]}")
    print(f"\n  vitals    : {[(v['name'], v['value']) for v in case['vitals']]}")
    print(f"  allergies : {[(a['allergen'], a.get('reaction'), a['source']) for a in case['allergies']]}")
    print(f"  meds      : {[m['name'] for m in case['medications']]}")
    print(f"  alerts    : {len(case['alerts'])}")
    print(f"  medplum   : Patient/{case['patient_id']}")
    print(f"  ws types  : {sorted({e['type'] for e in events})}")


if __name__ == "__main__":
    asyncio.run(run(int(sys.argv[1]) if len(sys.argv) > 1 else 8000))
