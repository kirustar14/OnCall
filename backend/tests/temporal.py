"""Temporal analysis: how far behind the room does the pipeline run?

Streams the demo clip into a running server at wall-clock pace and timestamps
every event against the audio position, so "it felt laggy" becomes a number.

The ground truth is the clip's own timeline: each script line's start offset is
known from tests/make_demo_audio.py, so for any event we can say how far past
the moment it describes it actually landed.

    ./venv/bin/uvicorn app.main:app --port 8000     # in another terminal
    ./venv/bin/python -m tests.temporal 8000
"""

import asyncio
import json
import sys
import time
import uuid
import wave

import websockets

# A fresh case every run. A fixed id looks harmless but silently invalidates the
# measurement: the server snapshots state to disk, so a second run reloads the
# first run's allergy, medication and — fatally — its `surfaced_issues`, and
# tier 1 then correctly declines to re-announce a conflict it already made. The
# result reads as "the alert never fired" when the pipeline is working properly.
CASE_ID = f"temporal-{uuid.uuid4().hex[:8]}"

# (speaker, start_offset_seconds, what it contains) — from make_demo_audio.py
TIMELINE = [
    ("MEDIC", 0.0, "handoff: GCS, vitals, open tibia fracture"),
    ("DR. REYES", 28.3, "three requests, one unowned"),
    ("NURSE OKAFOR", 34.9, "claims ortho"),
    ("NURSE OKAFOR", 38.6, "penicillin allergy from the mother"),
    ("DR. REYES", 50.5, "orders ampicillin-sulbactam"),
]
CLIP_SECONDS = 57.3


def _bar(seconds: float, scale: float = 1.4) -> str:
    return "#" * max(1, int(seconds * scale))


async def run(port: int) -> None:
    with wave.open("/tmp/servare-audio/demo-script.wav", "rb") as w:
        pcm = w.readframes(w.getnframes())

    events: list[tuple[float, str, str]] = []  # (audio_position, kind, detail)
    started = 0.0

    async with websockets.connect(f"ws://localhost:{port}/ws/case/{CASE_ID}", max_size=None) as ws:

        async def reader() -> None:
            try:
                async for raw in ws:
                    m = json.loads(raw)
                    at = time.monotonic() - started
                    t = m.get("type")
                    if t == "transcript" and m.get("is_final"):
                        events.append((at, "transcript", m["text"][:58]))
                    elif t == "case_data":
                        d = m.get("data", {})
                        events.append(
                            (
                                at,
                                "extracted",
                                f"vitals={len(d.get('vitals', []))} "
                                f"allergies={len(d.get('allergies', []))} "
                                f"meds={len(d.get('medications', []))} "
                                f"work={len(d.get('work', []))}",
                            )
                        )
                    elif t == "alert":
                        a = m["alert"]
                        events.append(
                            (at, f"ALERT/{a.get('kind')}", f"[{a.get('urgency')}] {a['text'][:52]}")
                        )
                    elif t == "unowned_prompt":
                        events.append((at, "WATCHDOG", m["text"][:52]))
                    elif t == "agent_step" and m.get("step") == "decision":
                        events.append(
                            (
                                at,
                                "agent-decision",
                                f"action={m.get('action_needed')} surfaced={m.get('already_surfaced')}",
                            )
                        )
            except Exception:
                pass

        task = asyncio.create_task(reader())
        started = time.monotonic()

        chunk = 16000 * 2 // 10  # 100 ms
        for off in range(0, len(pcm), chunk):
            await ws.send(pcm[off : off + chunk])
            await asyncio.sleep(0.1)

        audio_done = time.monotonic() - started
        print(f"\naudio finished at {audio_done:.1f}s (clip is {CLIP_SECONDS:.1f}s)\n")
        await asyncio.sleep(45)
        task.cancel()

    # ---- report ------------------------------------------------------------
    print("=" * 88)
    print(f"{'audio pos':>10}  {'kind':<16}  detail")
    print("-" * 88)
    for at, kind, detail in events:
        marker = "  <<< AFTER AUDIO" if at > audio_done else ""
        print(f"{at:9.1f}s  {kind:<16}  {detail}{marker}")

    print("\n" + "=" * 88)
    print("LAG AGAINST THE MOMENT EACH THING WAS SAID")
    print("-" * 88)
    transcripts = [e for e in events if e[1] == "transcript"]
    for i, (speaker, spoken_at, what) in enumerate(TIMELINE):
        landed = next((e[0] for e in transcripts if e[0] >= spoken_at), None)
        if landed is None:
            print(f"  {speaker:<13} spoken@{spoken_at:5.1f}s  NEVER TRANSCRIBED   {what}")
            continue
        lag = landed - spoken_at
        print(f"  {speaker:<13} spoken@{spoken_at:5.1f}s  transcript +{lag:4.1f}s  {_bar(lag)}  {what}")

    # Match the conflict alert by KIND, not by "first alert after the order".
    # The tier-2 agent fires its own alerts continuously, so first-after-timestamp
    # matched an unrelated advisory and reported a lag ~20s better than reality.
    alerts = [e for e in events if e[1].startswith("ALERT")]
    verified = [e for e in alerts if e[1] == "ALERT/verified_conflict"]
    order_at = TIMELINE[-1][1]
    print()
    if verified:
        landed = verified[0][0]
        print(
            f"  CRITICAL PATH: order spoken@{order_at:.1f}s -> FDA-verified conflict "
            f"at {landed:.1f}s = {landed - order_at:.1f}s behind the room"
        )
    else:
        print("  CRITICAL PATH: NO verified_conflict ALERT FIRED")

    agent_alerts = [e for e in alerts if e[1] == "ALERT/agent"]
    crit = [e for e in agent_alerts if e[2].startswith("[critical]")]
    print(f"  tier-2 agent alerts: {len(agent_alerts)} ({len(crit)} marked critical)")
    print(f"  transcripts (utterances): {len(transcripts)}  [script has {len(TIMELINE)} turns]")

    tail = [e for e in events if e[0] > audio_done]
    print(f"\n  events landing after the audio ended: {len(tail)}/{len(events)}")
    if tail:
        print("  (a large tail here is the pipeline finishing work the room already moved past)")


if __name__ == "__main__":
    asyncio.run(run(int(sys.argv[1]) if len(sys.argv) > 1 else 8000))
